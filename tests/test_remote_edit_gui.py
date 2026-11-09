"""上传确认、文件默认选择、丢失回答核对，以及原计算页的副本行为。"""

import copy
from pathlib import Path
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


@pytest.fixture
def setup(qapp, tmp_path, monkeypatch):
    from PySide6.QtCore import QEvent, QObject
    from PySide6.QtWidgets import QMessageBox, QWidget
    from remote_edit_panel import UploadDialog

    monkeypatch.setattr(core, "CONFIG_FILE", str(tmp_path / "preferences.json"))
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: None)
    cfg = dict(format=config.FORMAT, version=config.VERSION,
               jobs=[dict(step="rnucleus", vals=config.STEPS["rnucleus"].defaults())])
    local = tmp_path / "local"
    config.install(cfg, str(local))
    (local / "manual.txt").write_text("input")
    (local / "isodata").write_text("generated")
    with (local / "large.w").open("wb") as stream:
        stream.truncate(101_000_000)
    class Panel(QWidget):
        cur_workdir = str(local)
        remote_windows = []
        def refresh_remote_copy(self):
            self.link = edit.association(local)
    class Source(QObject):
        server = dict(capabilities=[edit.CAPABILITY])
        server_type = "workstation"
        client = SimpleNamespace(host="fixture")
        lost, performed = False, True
        def __init__(self):
            super().__init__()
            self.calls = []
        def submit(self, method, args, callback):
            self.calls.append((method, args))
            try:
                if method in ("create_workdir", "update_workdir") and self.lost:
                    if self.performed:
                        getattr(remote.Operations(), method)(**args)
                    callback(None, ssh.UnknownOutcome("lost reply"))
                    return
                value = getattr(remote.Operations(), method)(**args)
            except Exception as error:
                callback(None, error)
            else:
                callback(value, None)
        def close(self):
            self.closed = True
    panel, source = Panel(), Source()
    dialog = UploadDialog(panel, source, str(tmp_path / "server"), cfg, None)
    spin(qapp, lambda: dialog.ready)
    yield dialog, source, panel, cfg
    dialog.shutdown()
    panel.deleteLater()
    qapp.sendPostedEvents(None, QEvent.DeferredDelete)


def test_upload_defaults_and_no_confirmation_leave_server_intact(setup, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    dialog, source, panel, cfg = setup
    selected = {row["name"] for row in dialog.selected()}
    assert selected == {"manual.txt"}
    assert all(not row["name"].startswith(".grasp") for row in dialog.rows)
    def no(*args):
        assert args[-1] == QMessageBox.No
        return QMessageBox.No
    monkeypatch.setattr(QMessageBox, "question", no)
    dialog.start_transfer()
    assert not Path(dialog.directory).exists()
    assert not any(method == "create_workdir" for method, _ in source.calls)


def test_record_written_before_rsync_and_link_survives_failure(setup, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    dialog, source, panel, cfg = setup
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Yes)
    transferred = []
    def transfer(rows):
        assert (Path(dialog.directory) / core.SESSION_FILE).exists()
        assert edit.association(panel.cur_workdir)["revision"] == dialog.revision
        transferred.extend(rows)
        dialog.controls(False)
    monkeypatch.setattr(dialog, "begin_transfer", transfer)
    dialog.start_transfer()
    assert [row["name"] for row in transferred] == ["manual.txt"]
    assert sum(method == "create_workdir" for method, _ in source.calls) == 1
    assert dialog.written
    dialog.transferFailed.emit("fixture failure")
    assert "请勿删除服务器记录" in dialog.status.text()
    dialog.start_transfer()
    assert sum(method == "create_workdir" for method, _ in source.calls) == 1
    assert not any(method == "update_workdir" for method, _ in source.calls)


@pytest.mark.parametrize("performed", [False, True])
def test_lost_create_reply_needs_another_confirmation_before_retry(setup, monkeypatch, performed):
    from PySide6.QtWidgets import QMessageBox
    dialog, source, panel, cfg = setup
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Yes)
    source.lost, source.performed = True, performed
    transfers = []
    monkeypatch.setattr(dialog, "begin_transfer", lambda rows: transfers.append(rows))
    dialog.start_transfer()
    assert sum(method == "create_workdir" for method, _ in source.calls) == 1
    assert any(method == "get_pipeline" for method, _ in source.calls)
    assert not transfers
    link = edit.association(panel.cur_workdir)
    if performed:
        assert dialog.written and not link.get("pending") and link["revision"]
        dialog.start_transfer()
        assert transfers
    else:
        assert not dialog.pending and link is None
        assert "写入未执行" in dialog.status.text()
        monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.No)
        dialog.start_transfer()
        assert sum(method == "create_workdir" for method, _ in source.calls) == 1
        monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Yes)
        dialog.start_transfer()
    assert sum(method == "create_workdir" for method, _ in source.calls) == (1 if performed else 2)


@pytest.mark.parametrize("performed", [False, True])
def test_lost_update_with_unchanged_revision_can_be_confirmed_again(setup, monkeypatch, performed):
    from PySide6.QtWidgets import QMessageBox
    dialog, source, panel, cfg = setup
    created = remote.Operations().create_workdir(dialog.directory, cfg)
    dialog.revision = created["revision"]
    edit.bind(panel.cur_workdir, dict(host=source.client.host, directory=dialog.directory, revision=dialog.revision))
    desired = copy.deepcopy(cfg)
    desired["jobs"][0]["vals"]["z"] = "26"
    dialog.configuration = desired
    source.lost, source.performed = True, performed
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Yes)
    transfers = []
    monkeypatch.setattr(dialog, "begin_transfer", lambda rows: transfers.append(rows))
    dialog.start_transfer()
    assert sum(method == "update_workdir" for method, _ in source.calls) == 1
    assert not dialog.pending and not edit.association(panel.cur_workdir).get("pending")
    assert not transfers
    if not performed:
        assert not dialog.written and "写入未执行" in dialog.status.text()
        assert dialog.configuration == desired
        assert dialog.revision == created["revision"]
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.No)
    dialog.start_transfer()
    assert sum(method == "update_workdir" for method, _ in source.calls) == 1
    source.lost = False
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Yes)
    dialog.start_transfer()
    assert sum(method == "update_workdir" for method, _ in source.calls) == (1 if performed else 2)
    actual = remote.Operations().get_pipeline(dialog.directory)["config"]
    assert edit.fingerprint(actual) == edit.fingerprint(desired) and transfers


def test_change_after_unexecuted_update_check_still_conflicts(setup, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    dialog, source, panel, cfg = setup
    created = remote.Operations().create_workdir(dialog.directory, cfg)
    dialog.revision = created["revision"]
    edit.bind(panel.cur_workdir, dict(host=source.client.host, directory=dialog.directory, revision=dialog.revision))
    desired = copy.deepcopy(cfg)
    desired["jobs"][0]["vals"]["z"] = "26"
    dialog.configuration = desired
    source.lost, source.performed = True, False
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Yes)
    dialog.start_transfer()
    assert not dialog.pending
    other = copy.deepcopy(cfg)
    other["jobs"][0]["vals"]["z"] = "8"
    remote.Operations().update_workdir(dialog.directory, created["revision"], other)
    source.lost = False
    dialog.start_transfer()
    assert sum(method == "update_workdir" for method, _ in source.calls) == 1
    assert edit.fingerprint(remote.Operations().get_pipeline(dialog.directory)["config"]) == edit.fingerprint(other)


def test_missing_record_in_nonempty_directory_does_not_enable_retry(setup):
    dialog, source, panel, cfg = setup
    target = Path(dialog.directory)
    target.mkdir()
    (target / "keep.txt").write_text("unrelated")
    edit.bind(panel.cur_workdir, dict(host=source.client.host, directory=dialog.directory, revision=None,
              pending=True, fingerprint=edit.fingerprint(cfg)))
    dialog.reconcile()
    assert dialog.pending and edit.association(panel.cur_workdir)["pending"]
    assert not dialog.start_button.isEnabled()


def test_verified_previous_write_does_not_skip_new_local_changes(setup, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    dialog, source, panel, cfg = setup
    remote.Operations().create_workdir(dialog.directory, cfg)
    edit.bind(panel.cur_workdir, dict(host=source.client.host, directory=dialog.directory, revision=None,
              pending=True, fingerprint=edit.fingerprint(cfg)))
    desired = copy.deepcopy(cfg)
    desired["jobs"][0]["vals"]["z"] = "26"
    dialog.configuration = desired
    dialog.reconcile()
    assert not dialog.written and not dialog.pending
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Yes)
    monkeypatch.setattr(dialog, "begin_transfer", lambda rows: None)
    dialog.start_transfer()
    assert sum(method == "update_workdir" for method, _ in source.calls) == 1
    assert edit.fingerprint(remote.Operations().get_pipeline(dialog.directory)["config"]) == edit.fingerprint(desired)


def test_old_server_is_rejected_before_any_write(qapp, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from remote_edit_panel import supported
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[-1]))
    assert not supported(SimpleNamespace(server={}), None)
    assert "安装 / 更新" in warnings[0]


def test_target_dialog_selects_host_and_new_directory(qapp, tmp_path, monkeypatch):
    from PySide6.QtCore import QEvent, QObject
    from PySide6.QtWidgets import QDialog
    from remote_edit_panel import TargetDialog

    monkeypatch.setattr(core, "CONFIG_FILE", str(tmp_path / "preferences.json"))
    class Source(QObject):
        client = SimpleNamespace(host="cluster")
        server = dict(capabilities=[edit.CAPABILITY], server_types=["workstation", "slurm"])
        def close(self):
            pass
    dialog = TargetDialog(None, str(tmp_path / "local"))
    try:
        dialog.host.setCurrentText("cluster")
        dialog.source, dialog.directory = Source(dialog), "/work"
        dialog.server_type.setCurrentIndex(dialog.server_type.findData("slurm"))
        dialog.target.setText("new calculation")
        dialog.accept()
        assert dialog.result() == QDialog.Accepted and dialog.directory == "/work/new calculation"
        assert dialog.source.server_type == "slurm"
    finally:
        dialog.reject()
        dialog.deleteLater()
        qapp.sendPostedEvents(None, QEvent.DeferredDelete)


def test_take_out_opens_original_local_editor_and_preserves_unrelated_files(qapp, tmp_path, monkeypatch):
    from PySide6.QtCore import QEvent, QObject
    from PySide6.QtWidgets import QMessageBox, QWidget
    from remote_edit_panel import take_out

    monkeypatch.setattr(core, "CONFIG_FILE", str(tmp_path / "preferences.json"))
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: None)
    cfg = dict(format=config.FORMAT, version=config.VERSION,
               jobs=[dict(step="rnucleus", vals=config.STEPS["rnucleus"].defaults())])
    server, local = tmp_path / "server", tmp_path / "copy"
    remote.Operations().create_workdir(str(server), cfg)
    class Source(QObject):
        client = SimpleNamespace(host="cluster")
        server = dict(capabilities=[edit.CAPABILITY])
        def submit(self, method, args, callback):
            callback(getattr(remote.Operations(), method)(**args), None)
    parent = QWidget()
    source = Source(parent)
    opened = []
    try:
        take_out(source, str(server), parent, opened.append, str(local))
        assert opened == [str(local)] and edit.association(local)["host"] == "cluster"
        blocked = tmp_path / "other"
        blocked.mkdir()
        (blocked / "keep.txt").write_text("unrelated")
        take_out(source, str(server), parent, opened.append, str(blocked))
        assert opened == [str(local)] and (blocked / "keep.txt").read_text() == "unrelated"
    finally:
        parent.deleteLater()
        qapp.sendPostedEvents(None, QEvent.DeferredDelete)


def test_upload_and_run_reuses_remote_window_start_flow(setup, monkeypatch):
    from PySide6.QtWidgets import QMessageBox, QWidget
    import remote_edit_panel
    dialog, source, panel, cfg = setup
    dialog.run_after = True
    calls = []
    class Window(QWidget):
        def __init__(self, received, directory, parent, run_after_load):
            super().__init__(parent)
            calls.append((received, directory, run_after_load))
    monkeypatch.setattr(remote_edit_panel, "RemoteWindow", Window)
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Yes)
    monkeypatch.setattr(dialog, "begin_transfer", lambda rows: dialog.completed_upload(dict(count=len(rows))))
    dialog.start_transfer()
    assert calls == [(source, dialog.directory, True)]


def test_remote_run_after_pipeline_load_occurs_once(qapp):
    from PySide6.QtCore import QEvent, QObject, Signal
    from remote_panel import RemoteCalculation
    class Source(QObject):
        push = Signal(object)
        def submit(self, method, args, callback):
            if method == "pipeline":
                job = config.STEPS["rnucleus"].job(config.STEPS["rnucleus"].defaults())
                callback(dict(jobs=[job], revision="revision"), None)
            elif method == "watch":
                callback(dict(text="", state=None), None)
            elif method == "run_plan":
                self.calls += 1
        calls = 0
    source = Source()
    panel = RemoteCalculation(source, "/server", run_after_load=True)
    source.setParent(panel)
    try:
        assert source.calls == 1
        panel.load()
        assert source.calls == 1
    finally:
        panel.deleteLater()
        qapp.sendPostedEvents(None, QEvent.DeferredDelete)


def test_server_copy_indicator_preflight_and_local_run_default_no(qapp, tmp_path, monkeypatch):
    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import QMessageBox
    import grasp_bg as bg
    import grasp_gui

    monkeypatch.setattr(core, "CONFIG_FILE", str(tmp_path / "preferences.json"))
    cfg = dict(format=config.FORMAT, version=config.VERSION,
               jobs=[dict(step="rangular", vals=dict(config.STEPS["rangular"].defaults(), csf="remote.c"))])
    root = tmp_path / "copy"
    config.install(cfg, str(root))
    edit.bind(root, dict(host="cluster", directory="/server/calc", revision="revision"))
    win = grasp_gui.MainWindow(str(root), "calc")
    shown = []
    stopped = []
    monkeypatch.setattr(QMessageBox, "information", lambda *args: shown.append(args[-1]))
    monkeypatch.setattr(bg, "launch", lambda *args, **kw: pytest.fail("local run must be confirmed"))
    try:
        assert "cluster:/server/calc" in win.windowTitle()
        win.calc.check_inputs()
        assert "由服务器检查" in shown[-1] and "remote.c" in shown[-1]
        def no(*args):
            assert args[-1] == QMessageBox.No
            return QMessageBox.No
        monkeypatch.setattr(QMessageBox, "question", no)
        win.calc.run_pipeline()
        win.calc.upload_dialog = SimpleNamespace(shutdown=lambda: stopped.append(True))
    finally:
        win.close()
        win.deleteLater()
        qapp.sendPostedEvents(None, QEvent.DeferredDelete)
    assert stopped == [True]
