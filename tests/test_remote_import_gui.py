"""直接新建服务器计算，不建立本机目录或关联；确认及丢失回答复用上传流程。"""

from pathlib import Path, PurePosixPath
import time
from types import SimpleNamespace

import pytest

import grasp_config as config
import grasp_core as core
import grasp_remote as remote
import grasp_remote_edit as edit
import grasp_ssh as ssh

pytestmark = pytest.mark.gui


def spin(qapp, predicate):
    deadline = time.monotonic() + 5
    while not predicate():
        assert time.monotonic() < deadline
        qapp.processEvents()
        time.sleep(0.01)


@pytest.mark.parametrize("origin", ["example", "clipboard", "file"])
def test_three_sources_use_remote_target_and_never_check_local_programs(qapp, tmp_path, monkeypatch, origin):
    from PySide6.QtGui import QGuiApplication
    from calc_panel import ConfigDialog
    cfg = dict(config.list_demos()[0], title="../Li / trial")
    path = tmp_path / "configuration.json"
    config.save(cfg, path)
    def no_local_check(*args):
        pytest.fail("Remote import must check programs on the server")
    monkeypatch.setattr(config, "missing_programs", no_local_check)
    entries = [dict(cfg=cfg, warnings=[], path=None)] if origin == "example" else []
    dialog = ConfigDialog(None, entries, "/unused/local/bin", remote_directory="/srv/calculations")
    try:
        if origin == "clipboard":
            QGuiApplication.clipboard().setText("```json\n" + config.dumps(cfg) + "\n```")
            assert dialog.open_clipboard()
        elif origin == "file":
            assert dialog.open_file(str(path))
        assert dialog.update.isHidden() and dialog.browse.isHidden()
        assert dialog.target() == "/srv/calculations/Li_trial"
        assert dialog.entry()["cfg"]["jobs"] == cfg["jobs"]
        assert not (tmp_path / "Li_trial").exists()
        dialog.dir.setText("a-new-name")
        assert dialog.target() == "/srv/calculations/a-new-name"
        dialog.dir.setText("~/my-calc")
        assert dialog.target() == "~/my-calc"
    finally:
        dialog.deleteLater()


def test_bad_clipboard_keeps_selection_and_long_title_is_safe(qapp, monkeypatch):
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QMessageBox
    from calc_panel import ConfigDialog
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[2]))
    cfg = dict(config.list_demos()[0], title="核" * 200)
    dialog = ConfigDialog(None, [dict(cfg=cfg, warnings=[], path=None)], "", remote_directory="/srv")
    try:
        target = dialog.target()
        assert len(PurePosixPath(target).name.encode("utf-8")) <= 120
        QGuiApplication.clipboard().setText("this is not JSON")
        assert not dialog.open_clipboard()
        assert dialog.target() == target and dialog.entry()["cfg"] == cfg
        assert warnings and "this is not JSON" in warnings[0]
    finally:
        dialog.deleteLater()


@pytest.fixture
def factory(qapp, tmp_path, monkeypatch):
    from PySide6.QtCore import QEvent, QObject
    from PySide6.QtWidgets import QMessageBox, QWidget
    from remote_edit_panel import UploadDialog
    monkeypatch.setattr(core, "CONFIG_FILE", str(tmp_path / "preferences.json"))
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: None)
    def no_association(*args):
        pytest.fail("Direct server creation must not access a local association")
    monkeypatch.setattr(edit, "bind", no_association)
    monkeypatch.setattr(edit, "association", no_association)
    monkeypatch.setattr(edit, "checkout", no_association)
    class Source(QObject):
        server = dict(capabilities=[edit.CAPABILITY])
        client = SimpleNamespace(host="fixture")
        lost, performed, old_preview = False, True, False
        closed = False
        def __init__(self, parent):
            super().__init__(parent)
            self.calls = []
        def submit(self, method, args, callback):
            self.calls.append((method, args))
            try:
                if method == "create_workdir" and self.lost:
                    if self.performed:
                        remote.Operations().create_workdir(**args)
                    raise ssh.UnknownOutcome("lost create reply")
                result = getattr(remote.Operations(), method)(**args)
                if method == "preview_upload" and self.old_preview:
                    result.pop("missing_programs")
            except Exception as error:
                callback(None, error)
            else:
                callback(result, None)
        def close(self):
            self.closed = True
    panels = []
    def make(cfg=None, run=False):
        cfg = cfg or dict(format=config.FORMAT, version=config.VERSION,
                          jobs=[dict(step="rnucleus", vals=config.STEPS["rnucleus"].defaults())],
                          files=[dict(name="notes.txt", text="attached input\n")])
        panel = QWidget()
        panels.append(panel)
        source = Source(panel)
        dialog = UploadDialog(panel, source, str(tmp_path / f"server-{len(panels)}"), cfg, None, run,
                              remote_only=True, own_source=False)
        spin(qapp, lambda: dialog.ready)
        return dialog, source, cfg
    yield make
    for panel in panels:
        for dialog in panel.findChildren(UploadDialog):
            dialog.shutdown()
        panel.deleteLater()
    qapp.sendPostedEvents(None, QEvent.DeferredDelete)


def test_creation_only_opens_remote_directory_and_keeps_shared_source(factory, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    dialog, source, cfg = factory(run=True)
    opened = []
    dialog.remoteCreated.connect(lambda directory, run: opened.append((directory, run)))
    assert dialog.local is None and dialog.local_directory is None and dialog.rows == []
    assert dialog.fetch_button.isHidden() and dialog.unlink_button.isHidden()
    assert "notes.txt" in dialog.report.toPlainText() and "rnucleus" in dialog.report.toPlainText()
    def yes(*args):
        assert args[-1] == QMessageBox.No
        assert "fixture:" in args[2] and dialog.directory in args[2]
        return QMessageBox.Yes
    monkeypatch.setattr(QMessageBox, "question", yes)
    dialog.start_transfer()
    assert opened == [(dialog.directory, True)]
    assert (Path(dialog.directory) / "notes.txt").read_text() == "attached input\n"
    assert not source.closed and source.parent() is dialog.parent()
    assert not any(method in ("update_workdir", "run") for method, _ in source.calls)


@pytest.mark.parametrize("custom", [False, True])
def test_rejecting_default_no_never_creates(factory, monkeypatch, custom):
    from PySide6.QtWidgets import QMessageBox
    cfg = dict(format=config.FORMAT, version=config.VERSION,
               jobs=[dict(step="custom", vals=core.Custom.defaults())]) if custom else None
    dialog, source, _ = factory(cfg)
    questions = []
    def no(*args):
        questions.append(args)
        assert args[-1] == QMessageBox.No
        return QMessageBox.No
    monkeypatch.setattr(QMessageBox, "question", no)
    dialog.start_transfer()
    assert len(questions) == 1
    assert not Path(dialog.directory).exists()
    assert not any(method == "create_workdir" for method, _ in source.calls)


@pytest.mark.parametrize("performed", [False, True])
def test_lost_reply_reads_fingerprint_and_never_replays(factory, monkeypatch, performed):
    from PySide6.QtWidgets import QMessageBox
    dialog, source, _ = factory()
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Yes)
    source.lost, source.performed = True, performed
    dialog.start_transfer()
    assert sum(method == "create_workdir" for method, _ in source.calls) == 1
    assert any(method == "get_pipeline" for method, _ in source.calls)
    assert dialog.written == performed and not dialog.pending
    assert dialog.ready and not dialog.closed
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.No)
    dialog.start_transfer()
    assert sum(method == "create_workdir" for method, _ in source.calls) == 1


def test_nonempty_target_and_old_preview_cannot_write(factory, qapp):
    dialog, source, _ = factory()
    root = Path(dialog.directory)
    root.mkdir()
    (root / "existing.txt").write_text("keep")
    dialog.load()
    dialog.poll()
    # load 在工作线程完成；等待错误预览返回。
    spin(qapp, lambda: dialog.files_loaded)
    assert not dialog.ready and "空" in dialog.status.text()
    assert (root / "existing.txt").read_text() == "keep"
    dialog, source, _ = factory()
    source.old_preview = True
    dialog.preview([], dialog.show_preview)
    assert not dialog.ready and "更新" in dialog.status.text()
    dialog.start_transfer()
    assert not any(method == "create_workdir" for method, _ in source.calls)


def test_missing_input_can_be_left_for_later_or_selected_for_upload(factory, tmp_path, qapp, monkeypatch):
    from PySide6.QtWidgets import QFileDialog, QMessageBox
    cfg = dict(format=config.FORMAT, version=config.VERSION,
               jobs=[dict(step="rangular", vals=dict(config.STEPS["rangular"].defaults(), csf="manual.c"))])
    dialog, source, _ = factory(cfg)
    assert "manual.c" in dialog.report.toPlainText() and dialog.ready
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    (inputs / "manual.c").write_text("fixture CSF")
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *args: str(inputs))
    dialog.choose_inputs()
    spin(qapp, lambda: dialog.ready)
    assert [row["name"] for row in dialog.selected()] == ["manual.c"]
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Yes)
    uploaded = []
    def transfer(rows):
        assert (Path(dialog.directory) / core.SESSION_FILE).exists()
        assert dialog.local == str(inputs) and dialog.local_directory is None
        uploaded.extend(rows)
        dialog.controls(False)
    monkeypatch.setattr(dialog, "begin_transfer", transfer)
    dialog.start_transfer()
    assert [row["name"] for row in uploaded] == ["manual.c"]
    assert any(args.get("uploads") == ["manual.c"] for method, args in source.calls if method == "preview_upload")


def test_old_server_prompts_before_configuration_or_write(qapp, monkeypatch):
    from PySide6.QtWidgets import QMessageBox, QWidget
    from remote_import_panel import create
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[2]))
    parent = QWidget()
    try:
        create(parent, SimpleNamespace(server={}), "/srv", lambda *args: pytest.fail("Unexpected creation"))
        assert len(warnings) == 1 and "更新" in warnings[0]
        assert getattr(parent, "import_dialog", None) is None
    finally:
        parent.deleteLater()


def test_creation_may_leave_missing_input_for_later_upload(factory, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    cfg = dict(format=config.FORMAT, version=config.VERSION,
               jobs=[dict(step="rangular", vals=dict(config.STEPS["rangular"].defaults(), csf="manual.c"))])
    dialog, source, _ = factory(cfg)
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Yes)
    dialog.start_transfer()
    assert dialog.written and not (Path(dialog.directory) / "manual.c").exists()
    assert not any(method == "run" for method, _ in source.calls)


@pytest.mark.parametrize("kind", ["workstation", "slurm"])
def test_created_calculation_uses_normal_run_plan_once(qapp, kind):
    from PySide6.QtCore import QEvent, QObject, Signal
    from remote_panel import RemoteCalculation
    class Source(QObject):
        push = Signal(object)
        server_type = kind
        server = dict(capabilities=[edit.CAPABILITY], server_types=["workstation", "slurm"])
        def __init__(self):
            super().__init__()
            self.calls = []
        def submit(self, method, args, callback):
            self.calls.append((method, args))
            if method == "pipeline":
                job = config.STEPS["rnucleus"].job(config.STEPS["rnucleus"].defaults())
                callback(dict(jobs=[job], revision="fixture"), None)
            elif method == "watch":
                callback(dict(text="", state=None), None)
    source = Source()
    panel = RemoteCalculation(source, "/srv/new", run_after_load=True)
    source.setParent(panel)
    try:
        assert panel.create_button.isHidden()
        panel.load()
        calls = [(method, args) for method, args in source.calls if method == "run_plan"]
        assert len(calls) == 1 and calls[0][1].get("server_type", "workstation") == kind
        assert not any(method == "run" for method, _ in source.calls)
    finally:
        panel.deleteLater()
        qapp.sendPostedEvents(None, QEvent.DeferredDelete)


def test_connection_entry_and_empty_remote_entry_route_to_same_creation(qapp, tmp_path, monkeypatch):
    from PySide6.QtCore import QEvent, QObject, Signal
    from PySide6.QtWidgets import QDialog
    import remote_import_panel
    from remote_panel import ConnectDialog, RemoteCalculation
    monkeypatch.setattr(core, "CONFIG_FILE", str(tmp_path / "preferences.json"))
    calls = []
    def create(parent, source, directory, opened):
        calls.append((directory, source.server_type))
        if isinstance(parent, ConnectDialog):
            opened("/srv/new", True)
    monkeypatch.setattr(remote_import_panel, "create", create)
    class Source(QObject):
        push = Signal(object)
        client = SimpleNamespace(host="cluster")
        server_type = "slurm"
        server = dict(capabilities=[edit.CAPABILITY], server_types=["workstation", "slurm"])
        def close(self):
            pass
        def submit(self, method, args, callback):
            if method == "pipeline":
                callback(dict(jobs=[], revision="empty"), None)
            elif method == "watch":
                callback(dict(text="", state=None), None)
    dialog = ConnectDialog()
    source = Source(dialog)
    dialog.source = source
    try:
        assert not dialog.create_button.isEnabled()
        dialog.host.setCurrentText("cluster")
        dialog.server_type.setCurrentIndex(dialog.server_type.findData("slurm"))
        dialog.loaded(dict(directory="/srv", parent="/", files=[]), None)
        assert dialog.create_button.isEnabled()
        dialog.create_calculation()
        assert dialog.result() == QDialog.Accepted and dialog.run_after_load
        assert dialog.directory == "/srv/new"
        panel = RemoteCalculation(source, "/srv")
        panel.setParent(dialog)
        assert not panel.create_button.isHidden()
        panel.create_calculation()
        assert calls == [("/srv", "slurm"), ("/srv", "slurm")]
    finally:
        dialog.deleteLater()
        qapp.sendPostedEvents(None, QEvent.DeferredDelete)


def test_custom_authorization_is_separate_from_final_no(factory, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    cfg = dict(format=config.FORMAT, version=config.VERSION,
               jobs=[dict(step="custom", vals=core.Custom.defaults())])
    dialog, source, _ = factory(cfg)
    questions = []
    def ask(*args):
        assert args[-1] == QMessageBox.No
        questions.append(args[1])
        return QMessageBox.Yes if len(questions) == 1 else QMessageBox.No
    monkeypatch.setattr(QMessageBox, "question", ask)
    dialog.start_transfer()
    assert len(questions) == 2 and questions[0] == "自定义命令"
    assert not any(method == "create_workdir" for method, _ in source.calls)


@pytest.mark.parametrize("origin", ["example", "file", "clipboard"])
def test_configuration_selection_reaches_shared_uploader(factory, qapp, monkeypatch, tmp_path, origin):
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QDialog, QMessageBox
    from calc_panel import ConfigDialog
    from remote_import_panel import create
    previous, source, _ = factory()
    parent = previous.parent()
    previous.shutdown()
    cfg = config.list_demos()[0]
    path = tmp_path / "config.json"
    config.save(cfg, path)
    def select(self):
        assert self.remote_directory == str(tmp_path / "remote")
        if origin == "file":
            assert self.open_file(str(path))
        elif origin == "clipboard":
            QGuiApplication.clipboard().setText(config.dumps(cfg))
            assert self.open_clipboard()
        self.run_after.setChecked(True)
        return QDialog.Accepted
    monkeypatch.setattr(ConfigDialog, "exec", select)
    opened = []
    create(parent, source, str(tmp_path / "remote"), lambda directory, run: opened.append((directory, run)))
    dialog = parent.import_dialog
    spin(qapp, lambda: dialog.ready)
    assert dialog.configuration["jobs"] == cfg["jobs"] and dialog.run_after
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Yes)
    dialog.start_transfer()
    assert opened == [(dialog.directory, True)] and parent.import_dialog is None
    assert source.closed is False


def test_new_directory_replaces_empty_window_without_reconnecting(qapp, monkeypatch):
    from PySide6.QtCore import QEvent, QObject, Signal
    from PySide6.QtWidgets import QWidget
    import remote_panel
    class Source(QObject):
        push = Signal(object)
        notice = Signal(str)
        client = SimpleNamespace(host="fixture")
        server = dict(capabilities=[edit.CAPABILITY])
        closes = 0
        def submit(self, method, args, callback):
            if method == "pipeline":
                callback(dict(jobs=[], revision="empty"), None)
            elif method == "watch":
                callback(dict(text="", state=None), None)
        def close(self):
            self.closes += 1
        def reconnect(self):
            pass
    class View(QWidget):
        message = Signal(str)
        def __init__(self, *args):
            super().__init__()
        def shutdown(self):
            pass
    monkeypatch.setattr(remote_panel, "RemoteWaves", View)
    monkeypatch.setattr(remote_panel, "RemoteLevels", View)
    parent = QWidget()
    parent.remote_windows = []
    source = Source()
    old = remote_panel.RemoteWindow(source, "/srv", parent)
    try:
        old.show()
        old.open_created("/srv/new", False)
        new = old.created_window
        assert new.directory == "/srv/new" and source.parent() is new
        assert not old.isVisible() and new.isVisible() and source.closes == 0
        assert parent.remote_windows == [new]
        new.close()
        assert source.closes == 1
    finally:
        parent.deleteLater()
        qapp.sendPostedEvents(None, QEvent.DeferredDelete)


@pytest.mark.parametrize("inputs", ["ok", "missing", "pending"])
def test_final_confirmation_has_one_preflight_and_one_pending_section(factory, tmp_path, qapp, monkeypatch, inputs):
    from PySide6.QtWidgets import QMessageBox
    cfg = None if inputs == "ok" else dict(format=config.FORMAT, version=config.VERSION,
        jobs=[dict(step="rangular", vals=dict(config.STEPS["rangular"].defaults(), csf="manual.c"))])
    dialog, source, _ = factory(cfg)
    if inputs == "pending":
        local = tmp_path / "inputs"
        local.mkdir()
        (local / "manual.c").write_text("fixture input")
        dialog.local = str(local)
        dialog.load()
        spin(qapp, lambda: dialog.ready)
    questions = []
    def no(*args):
        assert args[-1] == QMessageBox.No
        questions.append(args[2])
        return QMessageBox.No
    monkeypatch.setattr(QMessageBox, "question", no)
    dialog.start_transfer()
    assert len(questions) == 1
    text = questions[0]
    assert text.count("输入预检通过。") == (0 if inputs == "missing" else 1)
    assert text.count("流程预检发现以下问题：") == (1 if inputs == "missing" else 0)
    assert text.count("待上传：") == 1
    if inputs == "pending":
        assert "待上传：manual.c" in text
    assert not any(method == "create_workdir" for method, _ in source.calls)


def test_remote_create_entry_is_hidden_until_an_empty_pipeline_loads(qapp):
    from PySide6.QtCore import QEvent, QObject, Signal
    from remote_panel import RemoteCalculation
    class Source(QObject):
        push = Signal(object)
        def __init__(self):
            super().__init__()
            self.callbacks = []
        def submit(self, method, args, callback):
            if method == "pipeline":
                self.callbacks.append(callback)
    source = Source()
    panel = RemoteCalculation(source, "/server")
    source.setParent(panel)
    try:
        assert panel.create_button.isHidden()
        source.callbacks.pop()(None, ValueError("fixture error"))
        assert panel.create_button.isHidden()
        panel.load()
        job = config.make_job(dict(step="rnucleus", vals=config.STEPS["rnucleus"].defaults()))
        source.callbacks.pop()(dict(jobs=[job], revision="nonempty"), None)
        assert panel.create_button.isHidden()
        panel.load()
        source.callbacks.pop()(dict(jobs=[], revision="empty"), None)
        assert not panel.create_button.isHidden()
        panel.load()
        assert panel.create_button.isHidden()
        source.callbacks.pop()(None, ValueError("fixture refresh error"))
        assert panel.create_button.isHidden()
    finally:
        source.callbacks.clear()
        panel.deleteLater()
        qapp.sendPostedEvents(None, QEvent.DeferredDelete)
