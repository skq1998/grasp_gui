"""服务器工作区中的流程编辑：沿用本机的程序页和流程编辑，修改先存为本机草稿，手动保存到服务器。"""

import json

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
                               QHBoxLayout, QLabel, QListWidget, QMessageBox, QPlainTextEdit, QVBoxLayout, QWidget)

import grasp_config as config
import grasp_core as core
import grasp_drafts as drafts
import grasp_ssh as ssh
import grasp_summary as summary
from calc_panel import ButtonColumn, PipelineEditor, fixed_font
from grasp_core import T
from gui_common import button


class ServerRun:
    """服务器上的程序目录和 MPI 设置在运行时确认；表单和输入预览不用本机设置。"""
    @staticmethod
    def bindir():
        return ""

    @staticmethod
    def mpi_prefix():
        return ""


class RemoteEditor(PipelineEditor):
    """服务器流程的编辑部分；修订号、草稿和写入由所在的 RemoteCalculation 提供连接。"""

    def __init__(self, calc, key):
        super().__init__(ServerRun)
        self.calc, self.key = calc, key
        self.base = None           # 服务器流程：revision、config、fingerprint
        self.draft = None          # 与草稿文件一致；没有修改时为 None
        self.blocked = None        # 草稿文件无法读取时的说明；此时不编辑，以免覆盖原文件
        self.saving = False

    # ---- 界面 ----
    def build(self):
        """返回程序页部分和流程部分；切换语言或字号时由 RemoteCalculation 重新调用。"""
        forms = QWidget()
        lay = QVBoxLayout(forms)
        lay.setContentsMargins(0, 0, 0, 0)
        self.build_forms(lay)
        bar = QHBoxLayout()
        bar.addWidget(button("预览输入", self.preview))
        check = button("检查输入文件", self.check_inputs)
        check.setToolTip(T("由服务器按服务器目录检查流程。"))
        bar.addWidget(check)
        self.add_button = button("加入流程 ↓", self.add_to_pipeline)
        bar.addWidget(self.add_button)
        bar.addStretch()
        lay.addLayout(bar)
        steps = QWidget()
        pl = QHBoxLayout(steps)
        pl.setContentsMargins(0, 0, 0, 0)
        self.plist = QListWidget()
        self.plist.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.plist.itemSelectionChanged.connect(self.update_run_buttons)
        self.plist.currentRowChanged.connect(self.preview_row_changed)
        self.plist.itemDoubleClicked.connect(self.show_job)
        pl.addWidget(self.plist, 1)
        self.edit_buttons = [button("编辑", self.edit_job), button("更新所选", self.update_job),
                             button("上移", lambda: self.move(-1)), button("下移", lambda: self.move(1)),
                             button("删除", self.remove_job), button("清空", self.clear_pipeline)]
        pl.addWidget(ButtonColumn([self.edit_buttons[:2] + [button("查看", self.show_job)], self.edit_buttons[2:4],
                                   self.edit_buttons[4:]]))
        self.prev = QPlainTextEdit()
        self.prev.setReadOnly(True)
        self.prev.setFont(fixed_font())
        self.refresh_list()
        self.update_editable()
        return forms, steps

    def fields(self):
        self.flush_group()
        return dict(steps={f.step.key: f.values() for f in self.forms}, groups=self.groups, group=self.group,
                    pipeline=self.pipeline, tab=self.nav.currentWidget().step.key)

    def update_editable(self):
        editable = self.blocked is None and not self.saving and self.base is not None
        for item in self.edit_buttons + [self.add_button]:
            item.setEnabled(editable)

    def update_run_buttons(self):
        self.calc.update_run_buttons()

    def show_preview(self):
        self.calc.details.setCurrentWidget(self.prev)

    def pipeline_changed(self):
        self.store()

    # ---- 服务器流程与草稿 ----
    def show_config(self, cfg):
        self.apply_fields(config.session(cfg))

    def start(self, value):
        """收到 get_pipeline 的回答：更新服务器流程；第一次时检查本机草稿。"""
        cfg = value["config"]
        self.base = dict(revision=value["revision"], config=cfg, fingerprint=drafts.fingerprint(cfg))
        if self.draft is None and self.blocked is None:
            try:
                saved = drafts.load(self.key)
            except drafts.DraftError as error:
                self.blocked = str(error)
                self.show_config(cfg)
                QMessageBox.warning(self.calc, T("草稿"), T("{e}\n文件已保留；在此之前服务器流程只读。", e=error))
                self.update_editable()
                return
            if saved is not None:
                self.found(saved)
                return
        if self.draft is None:
            self.show_config(cfg)
        self.update_editable()
        self.calc.draft_changed()

    def found(self, saved):
        if saved.get("pending"):
            # 上次写入的结果没有确认：只读取服务器流程核对，不重发。
            self.draft = saved
            self.show_draft()
            self.reconcile(value=dict(revision=self.base["revision"], config=self.base["config"]))
            return
        text = T("此服务器目录有 {time} 保存的草稿。", time=saved["updated"])
        if saved["base_revision"] != self.base["revision"]:
            text += "\n" + T("服务器流程在此之后已改变；恢复后保存会被拒绝，可导出草稿。")
        box = QMessageBox(QMessageBox.Question, T("恢复草稿"), text, parent=self.calc)
        restore = box.addButton(T("恢复草稿"), QMessageBox.AcceptRole)
        discard = box.addButton(T("放弃草稿"), QMessageBox.DestructiveRole)
        box.setDefaultButton(restore)
        box.exec()
        if box.clickedButton() is discard:
            self.discard()
            return
        self.draft = saved
        self.show_draft()

    def show_draft(self):
        try:
            self.show_config(self.draft["configuration"])
        except (KeyError, TypeError, ValueError) as error:
            self.blocked = str(error)
            self.draft = None
            QMessageBox.warning(self.calc, T("草稿"), T("{e}\n文件已保留；在此之前服务器流程只读。", e=error))
            self.show_config(self.base["config"])
        self.update_editable()
        self.calc.draft_changed()

    def configuration(self):
        base = self.draft["configuration"] if self.draft else self.base["config"]
        return drafts.configuration(self.pipeline, base)

    def store(self, pending=None):
        """把当前流程写入草稿；与服务器流程相同时删除草稿。"""
        if self.base is None or self.blocked is not None:
            return
        cfg = self.configuration()
        revision = self.draft["base_revision"] if self.draft else self.base["revision"]
        fingerprint = self.draft["base_fingerprint"] if self.draft else self.base["fingerprint"]
        try:
            if pending is None and drafts.fingerprint(cfg) == fingerprint and revision == self.base["revision"]:
                drafts.remove(self.key)
                self.draft = None
            else:
                self.draft = drafts.save(self.key, revision, fingerprint, cfg, pending)
        except OSError as error:
            QMessageBox.warning(self.calc, T("草稿"), T("无法保存草稿：{e}", e=error))
        self.calc.draft_changed()

    def discard(self):
        drafts.remove(self.key)
        self.draft = None
        if self.base is not None:
            self.show_config(self.base["config"])
        self.calc.draft_changed()

    def reload(self):
        """重新读取服务器流程，放弃草稿。"""
        if QMessageBox.question(self.calc, T("重新读取服务器流程"),
                                T("将放弃草稿，重新读取服务器流程。可先导出草稿。是否继续？"),
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        self.discard()
        self.calc.load()

    def export_draft(self):
        if self.draft is None:
            return
        path, _ = QFileDialog.getSaveFileName(self.calc, T("导出草稿为配置…"), "draft.json", T("配置文件 (*.json)"))
        if not path:
            return
        if not path.endswith(".json"):
            path += ".json"
        try:
            config.save(self.draft["configuration"], path)
        except OSError as error:
            QMessageBox.warning(self.calc, T("导出草稿为配置…"), str(error))
            return
        self.status(T("已导出配置 {p} ({n} 步)", p=path, n=len(self.draft["configuration"]["jobs"])))

    # ---- 保存到服务器 ----
    def checked_configuration(self, title):
        cfg = self.draft["configuration"] if self.draft else self.configuration()
        try:
            config.check(cfg)
        except config.ConfigError as error:
            QMessageBox.warning(self.calc, title, str(error))
            return None
        return cfg

    def preview_args(self, cfg, revision):
        args = dict(directory=self.calc.directory, configuration=cfg, revision=revision, uploads=[])
        source = self.calc.source
        if (getattr(source, "server_type", "workstation") == "workstation" and
                "run_settings_v1" in getattr(source, "server", {}).get("capabilities", [])):
            saved = core.load_config().get("remote_hosts", {}).get(source.client.host, {}).get("run")
            if saved is not None:
                args["run_settings"] = saved
        return args

    def check_inputs(self):
        cfg = self.checked_configuration(T("检查输入文件"))
        if cfg is None:
            return
        revision = self.draft["base_revision"] if self.draft else self.base["revision"]

        def checked(value, error):
            if error:
                QMessageBox.warning(self.calc, T("检查输入文件"), str(error))
                return
            report = value["checked"]
            text = summary.check_text(report) if report["issues"] else T("没有发现缺失的输入文件或名称问题。")
            if value["missing_programs"]:
                text += "\n" + T("服务器缺少程序：{names}", names=", ".join(value["missing_programs"]))
            QMessageBox.information(self.calc, T("检查输入文件"), text)
        self.calc.source.submit("preview_upload", self.preview_args(cfg, revision), checked)

    def save_to_server(self, done=None):
        """预览差异 → 自定义命令单独确认 → 最终确认 (默认“否”) → update_workdir。done(成功与否)。"""
        finish = done or (lambda ok: None)
        if self.draft is None:
            finish(True)
            return
        if self.saving:
            finish(False)
            return
        if self.draft.get("pending"):
            self.reconcile()
            finish(False)
            return
        title = T("保存到服务器")
        cfg = self.checked_configuration(title)
        if cfg is None:
            finish(False)
            return
        revision = self.draft["base_revision"]
        host, directory = self.calc.source.client.host, self.calc.directory
        self.set_saving(True)

        def previewed(value, error):
            if error:
                self.set_saving(False)
                self.refused(error)
                finish(False)
                return
            report = value["checked"]
            text = T("服务器 {h}:{p}", h=host, p=directory) + "\n" + value["changes"]
            text += "\n" + (summary.check_text(report) if report["issues"] else T("输入预检通过。"))
            if value["kept_files"]:
                text += "\n" + T("服务器同名文件将保留：{names}", names=", ".join(value["kept_files"]))
            if value.get("run_settings_warning"):
                text += "\n" + value["run_settings_warning"]
            if report["invalid"]:
                self.set_saving(False)
                QMessageBox.warning(self.calc, T("名称或参数不合法，不能保存"), text)
                finish(False)
                return
            if not PreviewDialog(self.calc, title, text).exec():
                self.set_saving(False)
                finish(False)
                return
            custom = bool(value["custom_commands"])
            if custom and QMessageBox.question(self.calc, T("自定义命令"),
                    T("流程包含可运行任意程序的自定义命令。确认写入服务器吗？") + "\n" +
                    json.dumps(value["custom_commands"], ensure_ascii=False, indent=1),
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                self.set_saving(False)
                finish(False)
                return
            if QMessageBox.question(self.calc, title, T("将修改写入服务器 {h}:{p}，是否确认？", h=host, p=directory),
                                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                self.set_saving(False)
                finish(False)
                return
            pending = dict(revision=revision, fingerprint=drafts.fingerprint(cfg))
            self.store(pending)
            if not self.draft or self.draft.get("pending") != pending:
                self.set_saving(False)
                finish(False)
                return
            args = dict(directory=directory, revision=revision, configuration=cfg, allow_custom=custom)
            self.calc.source.submit("update_workdir", args, lambda result, exc: written(result, exc))

        def written(value, error):
            self.set_saving(False)
            if isinstance(error, ssh.UnknownOutcome):
                self.status(T("写入结果未确认；先读取服务器流程核对，不能重发。"))
                self.reconcile(done=finish)
                return
            if error:
                self.store()        # 服务器明确拒绝：写入没有执行，保留草稿
                self.refused(error)
                finish(False)
                return
            self.saved(value, cfg)
            finish(True)

        self.calc.source.submit("preview_upload", self.preview_args(cfg, revision), previewed)

    def set_saving(self, saving):
        self.saving = saving
        self.update_editable()
        self.calc.update_run_buttons()

    def saved(self, value, cfg):
        drafts.remove(self.key)
        self.draft = None
        self.base = dict(revision=value["revision"], config=cfg, fingerprint=drafts.fingerprint(cfg))
        self.calc.revision = value["revision"]
        self.calc.jobs = config.session(cfg)["pipeline"]
        self.calc.draft_changed()
        self.status(T("已保存到服务器"))
        if value.get("warnings"):
            QMessageBox.warning(self.calc, T("保存到服务器"), "\n".join(value["warnings"]))
        self.calc.filesChanged.emit()

    def refused(self, error):
        """修订号改变或有计算正在运行：保留草稿，不提供强制覆盖。"""
        box = QMessageBox(QMessageBox.Warning, T("保存到服务器"),
                          str(error) + "\n" + T("草稿已保留。"), parent=self.calc)
        reload = box.addButton(T("重新读取服务器流程 (放弃草稿)"), QMessageBox.DestructiveRole)
        export = box.addButton(T("导出草稿为配置…"), QMessageBox.ActionRole)
        close = box.addButton(T("关闭"), QMessageBox.RejectRole)
        box.setDefaultButton(close)
        box.exec()
        if box.clickedButton() is reload:
            self.reload()
        elif box.clickedButton() is export:
            self.export_draft()

    def reconcile(self, value=None, done=None):
        """失去回答后读取服务器流程，按修订号和指纹判断写入是否已经执行；不重发。"""
        finish = done or (lambda ok: None)
        if value is None:
            self.calc.source.submit("get_pipeline", dict(directory=self.calc.directory),
                                    lambda result, error: self.reconciled(result, error, finish))
        else:
            self.reconciled(value, None, finish)

    def reconciled(self, value, error, finish):
        if self.draft is None or not self.draft.get("pending"):
            finish(self.draft is None)
            return
        if error:
            self.status(T("写入结果未确认；重新连接后将读取服务器流程核对。"))
            finish(False)
            return
        pending, current = self.draft["pending"], drafts.fingerprint(value["config"])
        self.base = dict(revision=value["revision"], config=value["config"], fingerprint=current)
        self.calc.revision = value["revision"]
        if current == pending["fingerprint"]:
            self.saved(dict(revision=value["revision"]), value["config"])
            self.show_config(value["config"])
            self.status(T("已核对：上次保存已写入服务器。"))
            finish(True)
            return
        self.store()
        if value["revision"] == pending["revision"] and current == self.draft["base_fingerprint"]:
            self.status(T("已核对：上次保存没有执行，可以再次保存到服务器。"))
        else:
            QMessageBox.warning(self.calc, T("保存到服务器"),
                                T("服务器流程已被修改，与草稿和上次保存都不一致。草稿已保留，请导出或重新读取。"))
        finish(False)


class PreviewDialog(QDialog):
    def __init__(self, parent, title, text):
        super().__init__(parent)
        self.setWindowTitle(title)
        lay = QVBoxLayout(self)
        view = QPlainTextEdit(text)
        view.setReadOnly(True)
        view.setFont(fixed_font())
        lay.addWidget(view)
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        buttons.addButton(T("继续…"), QDialogButtonBox.AcceptRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self.resize(760, 480)


CHOICES = (("save", "保存到服务器"), ("keep", "保留草稿"), ("discard", "放弃草稿"))


class DraftsDialog(QDialog):
    """关闭标签或退出时：每个有草稿的服务器工作区分别选择，默认“保留草稿”。"""

    def __init__(self, parent, workspaces):
        super().__init__(parent)
        self.setWindowTitle(T("未保存的服务器流程"))
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(T("下列服务器工作区有草稿：")))
        form = QFormLayout()
        self.choices = []
        for workspace in workspaces:
            box = QComboBox()
            for key, text in CHOICES:
                box.addItem(T(text), key)
            box.setCurrentIndex(1)
            label = QLabel(workspace.label)
            label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            form.addRow(label, box)
            self.choices.append((workspace, box))
        lay.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def selected(self):
        return [(workspace, box.currentData()) for workspace, box in self.choices]
