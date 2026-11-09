"""复用本机计算页和 rsync 的流程上传、取出及结果未确认处理。"""

import json
from pathlib import Path, PurePosixPath
import threading
import uuid

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QDialog, QFileDialog, QFormLayout, QInputDialog, QLineEdit, QMessageBox, QPlainTextEdit

import grasp_core as core
import grasp_config as config
import grasp_remote_edit as edit
import grasp_ssh as ssh
import grasp_summary as summary
import grasp_transfer as transfer
from grasp_core import T
from gui_common import button
from remote_panel import ConnectDialog, RemoteWindow
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
        try:
            path = edit.checkout(local, source.client.host, directory, value, replace=True)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(parent, T("在本机编辑…"), str(exc))
            return
        if value.get("warnings"):
            QMessageBox.warning(parent, T("在本机编辑…"), json.dumps(value["warnings"], ensure_ascii=False, indent=1))
        opened(path)
    source.submit("get_pipeline", dict(directory=directory), received)


class TargetDialog(ConnectDialog):
    def __init__(self, parent, local, link=None):
        super().__init__(parent)
        self.setWindowTitle(T("选择流程上传目标"))
        self.create_button.hide()
        self.local, self.link = local, link
        self.target = QLineEdit(link["directory"] if link else "")
        form = QFormLayout()
        form.addRow(T("上传目标目录（可新建）"), self.target)
        self.layout().insertLayout(2, form)
        if link:
            self.host.setCurrentText(link["host"])
            self.host.setEnabled(False)
            self.target.setReadOnly(True)

    def loaded(self, value, error):
        super().loaded(value, error)
        if not error and not self.link and not self.target.isModified():
            self.target.setText(str(PurePosixPath(value["directory"]) / Path(self.local).name))

    def accept(self):
        if not self.source or not self.directory or not self.target.text().strip():
            return
        if not supported(self.source, self):
            return
        target = self.target.text().strip()
        if not target.startswith(("/", "~")):
            target = str(PurePosixPath(self.directory) / target)
        super().accept()
        if self.result() == QDialog.Accepted:
            self.directory = target


class UploadDialog(TransferDialog):
    remoteCreated = Signal(str, bool)

    def __init__(self, panel, source, directory, configuration, link, run=False, *, remote_only=False,
                 own_source=True):
        self.panel, self.configuration, self.link = panel, configuration, link
        self.revision = (link or {}).get("revision")
        self.remote_only = remote_only
        self.local_directory = None if remote_only else panel.cur_workdir
        self.ready, self.written, self.keep_source = False, False, False
        self.files_loaded, self.preview_generation = False, 0
        self.existing, self.generated = set(), edit.generated_files(configuration["jobs"])
        self.run_after = run
        self.pending = bool((link or {}).get("pending"))
        self.previous = link
        super().__init__(source, directory, self.local_directory, parent=panel, upload=True)
        if own_source:
            source.setParent(self)
        self.setWindowTitle(T("上传并运行…") if run else T("上传到服务器…"))
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

    def choose_inputs(self):
        path = QFileDialog.getExistingDirectory(self, T("选择输入文件所在目录"), str(Path.home()))
        if path:
            self.local = path
            self.load()

    def preview_text(self, value):
        text = value["changes"] + "\n" + T("输入文件由服务器检查；实际运行前将再次检查。")
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
        self.source.submit("preview_upload", dict(directory=self.directory, configuration=self.configuration,
                           revision=self.revision, uploads=names), received)

    def show_preview(self, value, error):
        if self.closed:
            return
        if not error and self.remote_only and "missing_programs" not in value:
            error = ValueError(T("服务器端版本较旧，请用“安装 / 更新服务器版…”更新。"))
        if error:
            self.ready = False
            self.status.setText(str(error))
        else:
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
        elif self.run_after:
            parent = self.panel.window()
            win = RemoteWindow(self.source, self.directory, parent, run_after_load=True)
            self.source.setParent(win)
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


def upload(panel, run=False):
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
    target = TargetDialog(panel, panel.cur_workdir, link)
    if target.exec() != QDialog.Accepted:
        return
    dialog = UploadDialog(panel, target.source, target.directory, cfg, link, run)
    panel.upload_dialog = dialog
    def finished(result):
        panel.upload_dialog = None
        dialog.deleteLater()
    dialog.finished.connect(finished)
    dialog.show()
