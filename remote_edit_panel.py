"""复用本机计算页和 rsync 的流程上传、取出及结果未确认处理。"""

import json
from pathlib import Path, PurePosixPath
import threading
import uuid

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QDialog, QFileDialog, QInputDialog, QLabel, QMessageBox, QPlainTextEdit, QPushButton

import grasp_core as core
import grasp_config as config
import grasp_remote_edit as edit
import grasp_ssh as ssh
import grasp_summary as summary
import grasp_transfer as transfer
from grasp_core import T
from gui_common import button
from remote_panel import ConnectDialog, RemoteWindow, Source
from transfer_panel import TransferDialog


def supported(source, parent):
    if edit.CAPABILITY in getattr(source, "server", {}).get("capabilities", []):
        return True
    QMessageBox.warning(parent, T("上传到服务器…"),
                        T("服务器端版本较旧，请用“安装 / 更新服务器版…”更新。"))
    return False


def take_out(source, directory, parent, opened, local=None):
    if not supported(source, parent):
        return
    if local is None:
        local, accepted = QInputDialog.getText(parent, T("在本机编辑…"), T("本机工作目录"),
                          text=transfer.download_directory(source.client.host, directory))
        if not accepted or not local.strip():
            return
    local = str(Path(local).expanduser().resolve())
    if Path(local).exists() and not Path(local).is_dir():
        QMessageBox.warning(parent, T("在本机编辑…"), T("本机目录已有其他内容，请换一个目录。"))
        return
    if Path(local).exists() and any(Path(local).iterdir()):
        previous = edit.association(local)
        if not previous or (previous["host"], previous["directory"]) != (source.client.host, directory):
            QMessageBox.warning(parent, T("在本机编辑…"), T("本机目录已有其他内容，请换一个目录。"))
            return
        if QMessageBox.question(parent, T("重新取出服务器流程"),
                T("将放弃本机流程修改并重新取出；可先导出本机配置。是否继续？"),
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
    def received(value, error):
        if error:
            QMessageBox.warning(parent, T("在本机编辑…"), str(error))
            return
        warnings = []
        try:
            path = edit.checkout(local, source.client.host, directory, value, replace=True, warnings=warnings)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(parent, T("在本机编辑…"), str(exc))
            return
        if value.get("warnings"):
            QMessageBox.warning(parent, T("在本机编辑…"), json.dumps(value["warnings"], ensure_ascii=False, indent=1))
        if warnings:
            QMessageBox.warning(parent, T("在本机编辑…"), "\n".join(warnings))
        opened(path)
        widget = parent
        while widget is not None:
            if hasattr(widget, "statusBar"):
                widget.statusBar().showMessage(T("修改完成后，在计算页点“更新服务器流程…”。"))
            widget = widget.parentWidget()
    source.submit("get_pipeline", dict(directory=directory), received)


class TargetDialog(ConnectDialog):
    def __init__(self, parent, local, link=None, configuration=None):
        super().__init__(parent)
        self.setWindowTitle(T("选择流程上传目标"))
        self.create_button.hide()
        self.local, self.link = local, link
        self.configuration = configuration
        self.pending_target, self.closed_target, self.accept_after_load = False, False, False
        self.existing_directory = None
        self.target_generation, self.checkout_path = 0, None
        self.target_info = QLabel()
        self.target_info.setWordWrap(True)
        self.target_info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.layout().insertWidget(2, self.target_info)
        self.new_button = button("新建子目录…", self.new_directory)
        self.new_button.setEnabled(False)
        self.layout().itemAt(1).layout().insertWidget(2, self.new_button)
        self.use.setText(T("上传到此目录"))
        self.path.returnPressed.connect(self.connect_server)
        self.path.textEdited.connect(self.path_edited)
        self.finished.connect(lambda _: setattr(self, "closed_target", True))
        if link:
            self.host.setCurrentText(link["host"])
            self.host.setEnabled(False)
            self.path.setText(link["directory"])
            self.path.setReadOnly(True)
            self.files.setEnabled(False)
            for widget in self.findChildren(QPushButton):
                if widget.text() == T("上一级"):
                    widget.setEnabled(False)

    def path_edited(self, _):
        self.target_generation += 1
        self.directory = None
        self.use.setEnabled(False)
        self.new_button.setEnabled(False)
        self.target_info.setText(T("请连接 / 刷新以核对这个目标目录。"))

    def connect_server(self):
        # 父类的连接窗口不变；这里给上传浏览请求加代次，丢弃用户换目标后的回答。
        self.target_generation += 1
        generation = self.target_generation
        if self.link:
            self.path.setText(self.link["directory"])
        host, program = self.host.currentText().strip(), self.program.text().strip()
        if self.source is not None and (self.source.client.host != host or self.source.client.program != program or
                                        self.source.client.closed.is_set()):
            self.source.close()
            self.source = None
            self.existing_directory = None
        if self.source is None:
            try:
                self.source = Source(ssh.Client(host, program), self)
            except (OSError, ValueError) as error:
                self.status.setText(str(error))
                return
            self.source.notice.connect(self.status.setText)
        self.directory = None
        self.use.setEnabled(False)
        self.new_button.setEnabled(False)
        self.connect_button.setEnabled(False)
        source = self.source
        def received(value, error):
            if not self.closed_target and generation == self.target_generation and source is self.source:
                self.loaded(value, error)
        self.source.submit("list_dir", dict(directory=self.path.text().strip()), received)

    def loaded(self, value, error):
        if not error:
            self.show_target(value, False)
            return
        if (self.link or isinstance(error, ssh.ServerUnavailable) or self.configuration is None or
                edit.CAPABILITY not in getattr(self.source, "server", {}).get("capabilities", [])):
            super().loaded(value, error)
            return
        # 不用翻译后的错误字符串猜 ENOENT；现有只读预览能证明目标不存在 / 为空且可新建。
        generation = self.target_generation
        def checked(preview, exc):
            if self.closed_target or generation != self.target_generation:
                return
            if exc:
                super(TargetDialog, self).loaded(None, exc)
                self.accept_after_load = False
            else:
                directory = preview["directory"]
                self.show_target(dict(directory=directory, parent=str(PurePosixPath(directory).parent), files=[]), True)
        self.source.submit("preview_upload", dict(directory=self.path.text().strip(),
                           configuration=self.configuration, uploads=[]), checked)

    def show_target(self, value, pending):
        super().loaded(value, None)
        self.pending_target = pending
        if not pending:
            self.existing_directory = self.directory
        self.new_button.setEnabled(not self.link)
        text = T("上传到：{p}", p=self.directory)
        if pending:
            text += " — " + T("确认上传时创建")
        self.target_info.setText(text)
        if self.accept_after_load:
            self.accept_after_load = False
            self.accept()

    def new_directory(self):
        if not self.directory or self.link:
            return
        name, accepted = QInputDialog.getText(self, T("新建子目录…"), T("子目录名"), text=Path(self.local).name)
        if not accepted:
            return
        name = name.strip()
        if not name or "/" in name or name in (".", ".."):
            reason = T("子目录名不能为空。") if not name else T("子目录名不能包含 /，也不能是 . 或 ..。")
            QMessageBox.warning(self, T("新建子目录…"), reason)
            return
        self.path.setText(str(PurePosixPath(self.directory) / name))
        self.connect_server()

    def enter(self, item):
        if not self.link:
            super().enter(item)

    def parent_directory(self):
        if not self.link:
            super().parent_directory()

    def accept(self):
        if not self.source:
            return
        if not self.directory or self.path.text().strip() != self.directory:
            self.accept_after_load = True
            self.connect_server()
            return
        if not supported(self.source, self):
            return
        if not self.valid_connection():
            return
        if self.link:
            super().accept()
            return
        generation, directory = self.target_generation, self.directory
        self.use.setEnabled(False)
        def current():
            return not self.closed_target and generation == self.target_generation and directory == self.directory
        def checked(value, error):
            if not current():
                return
            self.use.setEnabled(True)
            if not error:
                box = QMessageBox(self)
                box.setWindowTitle(T("服务器已有计算"))
                box.setText(T("此目录已有计算，请先取出到本机编辑。当前本机流程的修改不会带到取出的副本中；"
                              "需要保留时请先“导出配置…”。"))
                fetch = box.addButton(T("取出到本机编辑…"), QMessageBox.ActionRole)
                cancel = box.addButton(T("取消"), QMessageBox.RejectRole)
                box.setDefaultButton(cancel)
                box.exec()
                if box.clickedButton() == fetch:
                    def opened(path):
                        self.checkout_path = path
                        self.reject()
                    take_out(self.source, directory, self, opened)
                return
            def empty_checked(preview, exc):
                if not current():
                    return
                if exc:
                    # 协议只返回错误文字；仅识别这两条已知中英文回答，其他原因原样显示。
                    no_record = "此目录没有工作目录记录"
                    nonempty = "目标目录不是空的；已有流程请先在本机取出编辑。"
                    if str(error) in (no_record, core.EN[no_record]) and str(exc) in (nonempty, core.EN[nonempty]):
                        self.status.setText(T("此目录不是空的，也没有计算记录，不能上传到这里。"
                                              "请选择空目录，或点“新建子目录…”。"))
                    else:
                        self.status.setText(str(error) + "\n" + str(exc))
                else:
                    target = self.directory
                    try:
                        if self.pending_target:
                            # 新建子目录保存已有上级；手输多级新路径时退回已浏览目录或服务器 HOME。
                            self.directory = self.existing_directory or self.source.server.get("home", "~")
                        super(TargetDialog, self).accept()
                    finally:
                        self.directory = target
            self.source.submit("preview_upload", dict(directory=directory, configuration=self.configuration,
                               uploads=[]), empty_checked)
        self.source.submit("get_pipeline", dict(directory=directory), checked)


class UploadDialog(TransferDialog):
    remoteCreated = Signal(str, bool)

    def __init__(self, panel, source, directory, configuration, link, run=False, *, remote_only=False,
                 own_source=True, create_pending=False):
        self.panel, self.configuration, self.link = panel, configuration, link
        self.revision = (link or {}).get("revision")
        self.remote_only = remote_only
        self.local_directory = None if remote_only else panel.cur_workdir
        self.ready, self.written, self.keep_source = False, False, False
        self.files_loaded, self.preview_generation = False, 0
        self.existing, self.generated = set(), edit.generated_files(configuration["jobs"])
        self.run_after = run if remote_only else False
        self.create_pending, self.completed = create_pending, False
        self.pending = bool((link or {}).get("pending"))
        self.previous = link
        super().__init__(source, directory, self.local_directory, parent=panel, upload=True)
        if own_source:
            source.setParent(self)
        self.setWindowTitle(T("更新 {h}:{p} 的流程", h=source.client.host, p=directory) if link else T("上传到服务器…"))
        self.selection_hint.setText(T("默认选择小文件；流程生成文件、服务器已有同名文件和大文件默认不选。"))
        if create_pending:
            self.source_label.setText(self.source_label.text() + " — " + T("确认上传时创建"))
        self.report = QPlainTextEdit()
        self.report.setReadOnly(True)
        self.report.setMaximumHeight(140)
        self.report.setMinimumHeight(75)
        self.layout().insertWidget(1, self.report)
        self.fetch_button = button("重新取出服务器流程", self.fetch_again)
        self.unlink_button = button("解除本机关联…", self.unlink)
        self.layout().insertWidget(2, self.fetch_button)
        self.layout().insertWidget(3, self.unlink_button)
        self.input_button = button("同时从本机上传输入文件…", self.choose_inputs)
        self.input_button.setVisible(remote_only)
        self.layout().insertWidget(4, self.input_button)
        self.open_button = button("打开远程窗口", self.open_remote)
        self.open_button.setEnabled(False)
        self.open_button.setVisible(not remote_only)
        self.layout().addWidget(self.open_button)
        self.fetch_button.setVisible(not remote_only)
        self.unlink_button.setVisible(not remote_only)
        if remote_only:
            self.setWindowTitle(T("在此新建计算…"))
            self.start_button.setText(T("确认并继续…"))
        # 流程上传保留服务器同名文件；需要替换原始输入时使用已有的独立文件传输确认流程。
        self.policy.setCurrentIndex(self.policy.findData("skip"))
        self.policy.setEnabled(False)
        self.policy.setToolTip(T("上传流程时保留服务器同名文件。"))
        self.transferred.connect(self.completed_upload)
        self.transferFailed.connect(self.failed_upload)
        self.finished.connect(lambda result: None if self.keep_source or not own_source else source.close())

    def saved_link(self):
        return self.link if self.remote_only else edit.association(self.local_directory)

    def save_link(self, value):
        if self.remote_only:
            self.link = value
        else:
            edit.bind(self.local_directory, value)

    def refresh_link(self):
        if not self.remote_only:
            self.panel.refresh_remote_copy()
            text = T("服务器 {h}:{p}", h=self.source.client.host, p=self.directory)
            if self.create_pending and self.revision is None:
                text += " — " + T("确认上传时创建")
            self.source_label.setText(text)

    def choose_inputs(self):
        path = QFileDialog.getExistingDirectory(self, T("选择输入文件所在目录"), str(Path.home()))
        if path:
            self.local = path
            self.load()

    def preview_text(self, value):
        text = value["changes"] + "\n" + T("输入文件由服务器检查；实际运行前将再次检查。")
        if not self.remote_only:
            if self.link:
                text = T("更新 {h}:{p} 的流程", h=self.source.client.host, p=self.directory) + "\n" + text
            if self.create_pending and self.revision is None:
                text += "\n" + T("确认上传时创建")
            if not self.selected():
                text += "\n" + T("只写入流程，不传输输入文件")
                attached = {row["name"] for row in self.configuration.get("files", [])}
                missing = value["checked"]["missing"] + [row for row in value["checked"]["pending"]
                                                       if row["file"] not in attached]
                text += "\n" + T("缺少输入文件：{names}", names=", ".join(row["file"] for row in missing) or "—")
        if value.get("run_settings_warning"):
            text += "\n" + value["run_settings_warning"]
        if self.remote_only:
            text += "\n" + "\n".join(f"{i}. {config.make_job(job)['title']}"
                                      for i, job in enumerate(self.configuration["jobs"], 1))
            text += "\n" + T("服务器缺少程序：{names}", names=", ".join(value["missing_programs"]) or "—")
            text += "\n" + T("附带文件：{names}",
                               names=", ".join(f["name"] for f in self.configuration.get("files", [])) or "—")
            text += "\n" + (summary.check_text(value["checked"]) if value["checked"]["issues"] else T("输入预检通过。"))
            text += "\n" + T("待上传：{names}", names=", ".join(r["file"] for r in value["checked"]["pending"]) or "—")
            if value["custom_commands"]:
                text += "\n" + T("自定义命令") + "\n" + json.dumps(value["custom_commands"], ensure_ascii=False)
        return text

    def controls(self, busy):
        super().controls(busy)
        self.policy.setEnabled(False)
        self.fetch_button.setEnabled(not busy)
        self.unlink_button.setEnabled(not busy)
        self.input_button.setEnabled(not busy and not self.pending)
        self.open_button.setEnabled(self.completed and not busy)

    def load(self):
        if self.busy:
            return
        self.ready = False
        self.files_loaded = False
        self.preview_generation += 1
        self.start_button.setEnabled(False)
        self.generation += 1
        generation = self.generation
        def scan():
            try:
                data = transfer.scan(self.local) if self.local else dict(files=[], skipped=[])
                data["files"] = edit.input_rows(data["files"])
                self.events.put(("files", (generation, data)))
            except (OSError, ValueError) as error:
                self.events.put(("load_error", (generation, error)))
        threading.Thread(target=scan, daemon=True).start()

    def changed(self, *args):
        super().changed(*args)
        self.start_button.setEnabled(self.ready and not self.busy and not self.pending)
        if self.ready and not self.busy and not self.pending and not self.written and not self.remote_only:
            if hasattr(self, "last_preview"):
                self.report.setPlainText(self.preview_text(self.last_preview))
            if not self.selected():
                self.status.setText(T("只写入流程，不传输输入文件"))
            elif self.status.text() == T("只写入流程，不传输输入文件"):
                self.status.setText("")

    def reset_checks(self):
        super().reset_checks()
        self.table.blockSignals(True)
        for i, row in enumerate(self.rows):
            if row["name"] in self.existing or row["name"] in self.generated:
                self.table.item(i, 0).setCheckState(Qt.Unchecked)
        self.table.blockSignals(False)
        self.changed()

    def show_files(self, data):
        super().show_files(data)
        self.files_loaded = True
        if not self.pending:
            rows = self.selected() if self.remote_only else self.rows
            self.preview([row["name"] for row in rows], self.show_preview)
        else:
            self.reconcile()

    def preview(self, names, callback):
        if edit.CAPABILITY not in getattr(self.source, "server", {}).get("capabilities", []):
            callback(None, ValueError(T("服务器端版本较旧，请用“安装 / 更新服务器版…”更新。")))
            return
        self.preview_generation += 1
        generation = self.preview_generation
        def received(value, error):
            if not self.closed and generation == self.preview_generation:
                callback(value, error)
        args = dict(directory=self.directory, configuration=self.configuration, revision=self.revision, uploads=names)
        if (getattr(self.source, "server_type", "workstation") == "workstation" and
                "run_settings_v1" in getattr(self.source, "server", {}).get("capabilities", [])):
            saved = core.load_config().get("remote_hosts", {}).get(self.source.client.host, {}).get("run")
            if saved is not None:
                args["run_settings"] = saved
        self.source.submit("preview_upload", args, received)

    def show_preview(self, value, error):
        if self.closed:
            return
        if not error and self.remote_only and "missing_programs" not in value:
            error = ValueError(T("服务器端版本较旧，请用“安装 / 更新服务器版…”更新。"))
        if error:
            self.ready = False
            self.status.setText(str(error))
        else:
            self.last_preview = value
            self.directory = value["directory"]
            self.target.setText(self.directory)
            self.existing = set(value["existing"])
            self.ready = self.files_loaded
            self.report.setPlainText(self.preview_text(value))
            self.reset_checks()
        self.changed()

    def start_transfer(self):
        if self.busy or not self.ready:
            return
        if self.pending:
            self.reconcile()
            return
        rows = list(self.selected())
        self.controls(True)
        def checked(value, error):
            if self.closed:
                return
            if not error and self.remote_only and "missing_programs" not in value:
                error = ValueError(T("服务器端版本较旧，请用“安装 / 更新服务器版…”更新。"))
            if error:
                self.controls(False)
                self.status.setText(str(error))
                return
            report = value["checked"]
            self.last_preview = value
            text = self.preview_text(value) + "\n" + self.counts.text() + "\n" + "\n".join(r["name"] for r in rows)
            if not self.remote_only:
                text += "\n" + (summary.check_text(report) if report["issues"] else T("输入预检通过。"))
                if report["pending"]:
                    text += "\n" + T("待上传：{names}", names=", ".join(r["file"] for r in report["pending"]))
            kept = sorted(set(value["kept_files"]) | set(value["existing"]))
            text += "\n" + T("服务器同名文件将保留：{names}", names=", ".join(kept))
            if report["invalid"]:
                self.controls(False)
                QMessageBox.warning(self, self.windowTitle(), text)
                return
            custom = bool(value["custom_commands"])
            if custom and QMessageBox.question(self, T("自定义命令"),
                    json.dumps(value["custom_commands"], ensure_ascii=False, indent=1),
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                self.controls(False)
                return
            text = T("服务器 {h}:{p}", h=self.source.client.host, p=self.directory) + "\n" + text
            if QMessageBox.question(self, self.windowTitle(), text, QMessageBox.Yes | QMessageBox.No,
                                    QMessageBox.No) != QMessageBox.Yes:
                self.controls(False)
                return
            if self.written:
                self.send_files(rows)
                return
            self.previous = self.saved_link()
            self.intent = uuid.uuid4().hex
            self.save_link(dict(host=self.source.client.host, directory=self.directory,
                      revision=self.revision, pending=True, fingerprint=edit.fingerprint(self.configuration),
                      intent=self.intent))
            args = dict(directory=self.directory, configuration=self.configuration, allow_custom=custom)
            method = "create_workdir" if self.revision is None else "update_workdir"
            if self.revision is not None:
                args["revision"] = self.revision
            self.source.submit(method, args, lambda result, exc: self.record_written(result, exc, rows))
        self.preview([row["name"] for row in rows], checked)

    def record_written(self, value, error, rows):
        if self.closed:
            return
        if error:
            self.controls(False)
            if isinstance(error, ssh.UnknownOutcome):
                self.pending = True
                self.status.setText(T("写入结果未确认；先读取服务器流程核对，不能重发。"))
                self.reconcile()
            else:
                current = self.saved_link()
                if current and current.get("intent") == self.intent:
                    self.save_link(self.previous)
                self.status.setText(str(error))
            self.changed()
            return
        self.revision, self.written = value["revision"], True
        self.save_link(dict(host=self.source.client.host, directory=self.directory, revision=self.revision))
        self.refresh_link()
        if value.get("warnings"):
            QMessageBox.warning(self, T("远程流程"), "\n".join(value["warnings"]))
        self.send_files(rows)

    def send_files(self, rows):
        if rows:
            self.begin_transfer(rows)
        else:
            self.controls(False)
            self.completed_upload(dict(count=0))

    def completed_upload(self, value):
        self.status.setText(T("流程已写入；已处理 {n} 个输入文件。", n=value["count"]))
        self.ready = False
        self.start_button.setEnabled(False)
        if self.remote_only:
            self.remoteCreated.emit(self.directory, self.run_after)
            self.done(QDialog.Accepted)
        else:
            self.completed = True
            self.controls(False)
            if not value["count"]:
                text = self.status.text() + "\n" + self.preview_text(self.last_preview)
                self.status.setText(text)

    def open_remote(self):
        if self.remote_only or not self.completed or self.busy:
            return
        parent = self.panel.window()
        for win in getattr(parent, "remote_windows", []):
            if (win.isVisible() and win.source.client.host == self.source.client.host and
                    win.directory == self.directory):
                win.calc.load()
                win.waves.scan()
                win.levels.scan()
                win.showNormal()
                win.raise_()
                win.activateWindow()
                if win.source is self.source:
                    self.keep_source = True
                    self.source.setParent(win)
                self.done(QDialog.Accepted)
                return
        win = RemoteWindow(self.source, self.directory, parent)
        self.keep_source = True
        if hasattr(parent, "remote_windows"):
            parent.remote_windows.append(win)
        win.show()
        self.done(QDialog.Accepted)

    def failed_upload(self, message):
        if self.written:
            self.status.setText(T("流程记录已写入，输入传输未完成；可重试补传，请勿删除服务器记录。") + "\n" + message)

    def reconcile(self):
        self.pending = True
        self.start_button.setEnabled(False)
        if edit.CAPABILITY not in getattr(self.source, "server", {}).get("capabilities", []):
            self.status.setText(T("服务器端版本较旧，请用“安装 / 更新服务器版…”更新。"))
            return
        def received(value, error):
            if self.closed:
                return
            saved = self.saved_link() or self.link or {}
            if error and saved.get("revision") is None:
                # 读取失败本身不能证明新建未执行；必须另行证明目标仍不存在或为空。
                def empty_checked(preview, exc):
                    if exc:
                        self.status.setText(str(exc))
                    else:
                        self.allow_retry(saved, preview)
                self.preview([row["name"] for row in self.rows], empty_checked)
                return
            if not error and edit.fingerprint(value["config"]) != saved.get("fingerprint"):
                if saved.get("revision") is not None and value["revision"] == saved["revision"]:
                    self.allow_retry(saved)
                    return
                self.status.setText(T("服务器流程与待确认写入不一致或没有记录；请人工核对或重新取出，不能重发。"))
                return
            if error:
                self.status.setText(T("服务器流程与待确认写入不一致或没有记录；请人工核对或重新取出，不能重发。"))
                return
            self.revision, self.pending = value["revision"], False
            # 重新打开窗口时本机可能又有修改；核对上次写入不等于当前快照也已写入。
            self.written = edit.fingerprint(self.configuration) == saved.get("fingerprint")
            self.save_link(dict(host=self.source.client.host, directory=self.directory, revision=self.revision))
            self.refresh_link()
            self.status.setText(T("已核对上次写入；请再次确认当前上传内容。"))
            if self.files_loaded:
                self.preview([row["name"] for row in self.rows], self.show_preview)
        self.source.submit("get_pipeline", dict(directory=self.directory), received)

    def allow_retry(self, saved, preview=None):
        """只解除待核对状态；不重发写入，仍经过下一次用户确认及服务器检查。"""
        self.revision = saved.get("revision")
        self.pending, self.written, self.ready = False, False, False
        link = dict(host=self.source.client.host, directory=self.directory, revision=self.revision)
        self.save_link(link if self.revision is not None else None)
        self.refresh_link()
        self.status.setText(T("写入未执行，可以再次确认上传。"))
        self.changed()
        if preview is not None:
            self.show_preview(preview, None)
        elif self.files_loaded:
            self.preview([row["name"] for row in self.rows], self.show_preview)

    def fetch_again(self):
        if self.busy:
            return
        def opened(path):
            if path == self.panel.cur_workdir:
                self.panel.open_workdir(path)
            else:
                self.panel.workdirRequested.emit(path)
            self.done(QDialog.Rejected)
        take_out(self.source, self.directory, self, opened, self.local_directory)

    def unlink(self):
        if self.busy:
            return
        if QMessageBox.question(self, T("解除本机关联…"),
                T("只解除本机副本关联，不改变服务器目录。结果未确认的请求须先人工核对。是否继续？"),
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
            self.save_link(None)
            self.refresh_link()
            self.done(QDialog.Rejected)


def upload(panel):
    if getattr(panel, "upload_dialog", None) is not None:
        panel.upload_dialog.show()
        panel.upload_dialog.raise_()
        return
    if not panel.need_pipeline() or not panel.save_session(require_saved=True):
        return
    try:
        cfg = edit.configuration(panel.cur_workdir, panel.pipeline)
    except (OSError, ValueError) as error:
        QMessageBox.warning(panel, T("上传到服务器…"), str(error))
        return
    link = edit.association(panel.cur_workdir)
    target = TargetDialog(panel, panel.cur_workdir, link, cfg)
    if target.exec() != QDialog.Accepted:
        if target.checkout_path:
            panel.workdirRequested.emit(target.checkout_path)
            panel.message.emit(T("修改完成后，在计算页点“更新服务器流程…”。"))
        return
    dialog = UploadDialog(panel, target.source, target.directory, cfg, link, create_pending=target.pending_target)
    panel.upload_dialog = dialog
    def finished(result):
        panel.upload_dialog = None
        dialog.deleteLater()
    dialog.finished.connect(finished)
    dialog.show()
