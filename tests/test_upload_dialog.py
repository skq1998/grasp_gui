"""已确认的上传交互：单目标、批量选择、流程更新和窗口会话交接。"""

from pathlib import Path
import copy
import threading
import time
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.gui
pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtCore import QEvent, QObject, Qt, Signal  # noqa: E402
from PySide6.QtWidgets import QDialog, QInputDialog, QMainWindow, QMessageBox, QWidget  # noqa: E402

import grasp_config as config  # noqa: E402
import grasp_core as core  # noqa: E402
import grasp_remote as remote  # noqa: E402
import grasp_remote_edit as edit  # noqa: E402
import grasp_transfer as transfer  # noqa: E402
import remote_edit_panel as ui  # noqa: E402
from transfer_panel import TransferDialog  # noqa: E402


class Source(QObject):
    notice = Signal(str)
    push = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.client = SimpleNamespace(host="fixture", program="", closed=threading.Event())
        self.server = dict(capabilities=[edit.CAPABILITY, "run_range_v1"], version=core.VERSION,
                           server_types=["workstation", "slurm"])
        self.server_type, self.calls = "workstation", []
        self.operation = remote.Operations()

    def submit(self, method, args, callback, **kwargs):
        self.calls.append((method, args))
        if method == "watch":
            callback(dict(state=None, text=""), None)
            return
        try:
            result = getattr(self.operation, method)(**args)
        except Exception as error:
            callback(None, error)
        else:
            callback(result, None)

    def close(self):
        self.client.closed.set()

    def reconnect(self):
        pass


def wait(qapp, predicate):
    deadline = time.monotonic() + 5
    while not predicate():
        assert time.monotonic() < deadline
        qapp.processEvents()
        time.sleep(0.01)


@pytest.fixture
def environment(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(core, "CONFIG_FILE", str(tmp_path / "preferences.json"))
    monkeypatch.setattr(ui.ssh, "hosts", lambda: [])
    monkeypatch.setattr(transfer, "capabilities", lambda program="rsync": dict(
        path=program, protect=True, progress2=True, version="fixture"))
    cfg = dict(format=config.FORMAT, version=config.VERSION, jobs=[
        dict(step="rnucleus", vals=config.STEPS["rnucleus"].defaults()),
        dict(step="rwfnestimate", vals=config.STEPS["rwfnestimate"].defaults())])
    local, server = tmp_path / "local", tmp_path / "server"
    config.install(cfg, str(local))
    class Panel(QWidget):
        workdirRequested = Signal(str)
        message = Signal(str)
        cur_workdir = str(local)
        def refresh_remote_copy(self):
            self.link = edit.association(local)
    window = QMainWindow()
    window.opened = []
    window.open_remote_workspace = lambda received, directory, **options: window.opened.append(
        (received, directory, options))
    panel = Panel(window)
    window.setCentralWidget(panel)
    source = Source()
    dialogs = []
    def target(path=server, link=None):
        dialog = ui.TargetDialog(panel, str(local), link, cfg)
        dialogs.append(dialog)
        dialog.host.setCurrentText("fixture")
        dialog.source = source
        source.setParent(dialog)
        dialog.server_type.setCurrentIndex(dialog.server_type.findData("workstation"))
        dialog.path.setText(str(path))
        dialog.connect_server()
        return dialog
    def upload(linked=False):
        for name in ("manual.txt", "rcsf.inp", "isodata"):
            (local / name).write_text("fixture")
        for name in ("one.w", "two.w"):
            with (local / name).open("wb") as stream:
                stream.truncate(101_000_000)
        link = None
        if linked:
            result = source.operation.create_workdir(str(server), cfg)
            (server / "manual.txt").write_text("keep")
            link = dict(host="fixture", directory=str(server), revision=result["revision"])
            edit.bind(local, link)
        dialog = ui.UploadDialog(panel, source, str(server), cfg, link, create_pending=not linked)
        dialogs.append(dialog)
        wait(qapp, lambda: dialog.ready)
        return dialog
    yield SimpleNamespace(root=tmp_path, local=local, server=server, configuration=cfg, source=source,
                          panel=panel, window=window, target=target, upload=upload, dialogs=dialogs)
    for dialog in dialogs:
        if isinstance(dialog, TransferDialog):
            dialog.shutdown()
        else:
            dialog.reject()
        dialog.deleteLater()
    window.close()
    window.deleteLater()
    qapp.sendPostedEvents(None, QEvent.DeferredDelete)


@pytest.mark.parametrize("language", ["zh", "en"])
def test_missing_typed_target_is_created_only_after_upload_confirmation(environment, language):
    e = environment
    core.set_lang(language)
    dialog = e.target()
    assert dialog.pending_target and dialog.directory == str(e.server)
    assert core.T("确认上传时创建") in dialog.target_info.text()
    assert not hasattr(dialog, "target")
    assert not e.server.exists()
    dialog.accept()
    assert dialog.result() == QDialog.Accepted
    assert any(method == "get_pipeline" for method, _ in e.source.calls)
    assert not any(method in remote.MUTATIONS for method, _ in e.source.calls)
    assert not e.server.exists()


@pytest.mark.parametrize("name", ["", " ", ".", "..", "a/b"])
def test_new_subdirectory_rejects_invalid_name_without_requests(environment, monkeypatch, name):
    e = environment
    e.server.mkdir()
    dialog = e.target()
    warnings = []
    monkeypatch.setattr(QInputDialog, "getText", lambda *args, **kwargs: (name, True))
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[-1]))
    before = len(e.source.calls)
    dialog.new_button.click()
    assert warnings and len(e.source.calls) == before
    assert dialog.directory == str(e.server)


@pytest.mark.parametrize("exists", [False, True])
def test_new_subdirectory_uses_one_path_and_enters_existing_directory(environment, monkeypatch, exists):
    e = environment
    e.server.mkdir()
    child = e.server / "local"
    if exists:
        child.mkdir()
    dialog = e.target()
    defaults = []
    def choose(*args, **kwargs):
        defaults.append(kwargs["text"])
        return "local", True
    monkeypatch.setattr(QInputDialog, "getText", choose)
    dialog.new_button.click()
    assert defaults == ["local"]
    assert dialog.path.text() == dialog.directory == str(child)
    assert dialog.pending_target is not exists
    assert child.exists() is exists
    dialog.parent_directory()
    assert dialog.directory == str(e.server) and not dialog.pending_target


def test_linked_target_cannot_change_host_or_directory(environment):
    e = environment
    result = e.source.operation.create_workdir(str(e.server), e.configuration)
    link = dict(host="fixture", directory=str(e.server), revision=result["revision"])
    dialog = e.target(link=link)
    assert not dialog.host.isEnabled() and dialog.path.isReadOnly() and not dialog.files.isEnabled()
    assert not dialog.new_button.isEnabled()
    dialog.parent_directory()
    assert dialog.directory == str(e.server)
    dialog.accept()
    assert dialog.result() == QDialog.Accepted


def test_changed_target_ignores_old_browser_and_pipeline_answers(environment, monkeypatch):
    e = environment
    e.server.mkdir()
    other = e.root / "other"
    other.mkdir()
    waiting = []
    monkeypatch.setattr(e.source, "submit", lambda method, args, callback, **kw:
                        waiting.append((method, args, callback)))
    dialog = e.target()
    old = waiting.pop()
    dialog.path.setText(str(other))
    dialog.connect_server()
    current = waiting.pop()
    current[2](e.source.operation.list_dir(str(other)), None)
    old[2](e.source.operation.list_dir(str(e.server)), None)
    assert dialog.directory == str(other)
    dialog.accept()
    pipeline = waiting.pop()
    assert pipeline[0] == "get_pipeline"
    dialog.path.setText(str(e.server))
    dialog.path.textEdited.emit(str(e.server))
    pipeline[2](dict(config=e.configuration, revision="old"), None)
    assert dialog.directory is None and not dialog.use.isEnabled()


@pytest.mark.parametrize("upload", [False, True])
def test_bulk_selection_updates_large_checkbox_and_restores_defaults(environment, qapp, upload):
    e = environment
    directory = e.local if upload else e.server
    e.server.mkdir(exist_ok=True)
    directory.mkdir(exist_ok=True)
    (directory / "small").write_text("small")
    for name in ("one.w", "two.w"):
        with (directory / name).open("wb") as stream:
            stream.truncate(101_000_000)
    dialog = TransferDialog(e.source, str(e.server), str(e.local) if upload else None, parent=e.panel)
    e.dialogs.append(dialog)
    wait(qapp, lambda: len(dialog.rows) >= 3)
    defaults = {row["name"] for row in dialog.selected()}
    dialog.all_button.click()
    assert len(dialog.selected()) == len(dialog.rows) and dialog.large.checkState() == Qt.Checked
    index = next(i for i, row in enumerate(dialog.rows) if row["name"] == "one.w")
    dialog.table.item(index, 0).setCheckState(Qt.Unchecked)
    assert dialog.large.checkState() == Qt.PartiallyChecked
    dialog.none_button.click()
    assert dialog.selected() == [] and dialog.large.checkState() == Qt.Unchecked
    dialog.defaults_button.click()
    assert {row["name"] for row in dialog.selected()} == defaults
    dialog.controls(True)
    assert not any(button.isEnabled() for button in (dialog.all_button, dialog.none_button, dialog.defaults_button))


def test_upload_default_selection_still_excludes_generated_and_existing_files(environment):
    dialog = environment.upload(linked=True)
    assert {row["name"] for row in dialog.selected()} == {"rcsf.inp"}
    dialog.all_button.click()
    assert len(dialog.selected()) == len(dialog.rows)
    dialog.defaults_button.click()
    assert {row["name"] for row in dialog.selected()} == {"rcsf.inp"}
    assert not dialog.policy.isEnabled() and dialog.policy.currentData() == "skip"


@pytest.mark.parametrize("language", ["zh", "en"])
def test_pipeline_only_upload_reports_missing_inputs_and_does_not_transfer(environment, monkeypatch, language):
    e = environment
    core.set_lang(language)
    dialog = e.upload()
    dialog.none_button.click()
    assert core.T("只写入流程，不传输输入文件") in dialog.status.text()
    confirmations = []
    def confirm(*args):
        confirmations.append(args)
        return QMessageBox.Yes
    monkeypatch.setattr(QMessageBox, "question", confirm)
    monkeypatch.setattr(dialog, "begin_transfer", lambda *args: pytest.fail("no input transfer expected"))
    dialog.start_button.click()
    assert len(confirmations) == 1 and confirmations[0][-1] == QMessageBox.No
    assert core.T("只写入流程，不传输输入文件") in confirmations[0][2]
    assert core.T("确认上传时创建") in confirmations[0][2] and "rcsf.inp" in confirmations[0][2]
    assert core.T("只写入流程，不传输输入文件") in dialog.status.text()
    assert dialog.open_button.isEnabled() and not dialog.busy
    assert (e.server / core.SESSION_FILE).is_file() and not (e.server / "rcsf.inp").exists()
    assert not any(method in ("run", "run_plan") for method, _ in e.source.calls)


def test_open_remote_hands_the_connection_to_a_workspace_and_never_runs(environment, monkeypatch):
    e = environment
    dialog = e.upload()
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Yes)
    dialog.none_button.click()
    dialog.start_transfer()
    assert e.window.opened == []
    dialog.open_button.click()
    assert e.window.opened == [(e.source, str(e.server), dict(refresh=True))]
    assert dialog.keep_source and not dialog.isVisible()
    assert not any(method in ("run", "run_plan") for method, _ in e.source.calls)


@pytest.mark.parametrize("language", ["zh", "en"])
def test_existing_unlinked_calculation_offers_safe_checkout_before_upload(environment, monkeypatch, language):
    e = environment
    core.set_lang(language)
    server_cfg = copy.deepcopy(e.configuration)
    server_cfg["jobs"][0]["vals"]["z"] = "3"
    e.source.operation.create_workdir(str(e.server), server_cfg)
    original = (e.local / core.SESSION_FILE).read_bytes()
    destination = e.root / "taken-out"
    defaults, messages = [], []
    def choose(*args, **kwargs):
        defaults.append(kwargs["text"])
        return str(destination), True
    def review(box):
        messages.append(box.text())
        assert box.defaultButton().text() == core.T("取消")
        assert len(box.buttons()) == 2
        box.choice = next(b for b in box.buttons() if b.text() == core.T("取出到本机编辑…"))
        return 0
    monkeypatch.setattr(QInputDialog, "getText", choose)
    monkeypatch.setattr(QMessageBox, "exec", review)
    monkeypatch.setattr(QMessageBox, "clickedButton", lambda box: box.choice)
    dialog = e.target()
    dialog.accept()
    assert dialog.checkout_path == str(destination) and dialog.result() == QDialog.Rejected
    assert len(defaults) == 1 and defaults[0] == transfer.download_directory("fixture", str(e.server))
    assert defaults[0] != str(e.local)
    assert ("不会带到" if language == "zh" else "will not be carried") in messages[0]
    assert ("导出配置" if language == "zh" else "Export configuration") in messages[0]
    assert (e.local / core.SESSION_FILE).read_bytes() == original and edit.association(e.local) is None
    assert edit.association(destination)["directory"] == str(e.server)
    assert not any(method in remote.MUTATIONS for method, _ in e.source.calls)
    assert [m for m, _ in e.source.calls].count("get_pipeline") == 2
    assert core.T("修改完成后，在计算页点“更新服务器流程…”。") in e.window.statusBar().currentMessage()


@pytest.mark.parametrize("language", ["zh", "en"])
def test_nonempty_directory_without_valid_calculation_remains_unchanged(environment, language):
    e = environment
    core.set_lang(language)
    e.server.mkdir()
    (e.server / "notes.txt").write_text("keep")
    dialog = e.target()
    dialog.accept()
    assert dialog.result() != QDialog.Accepted
    assert dialog.status.text() == core.T(
        "此目录不是空的，也没有计算记录，不能上传到这里。请选择空目录，或点“新建子目录…”。")
    assert (e.server / "notes.txt").read_text() == "keep"
    assert not any(method in remote.MUTATIONS for method, _ in e.source.calls)


@pytest.mark.parametrize("client_language,server_language", [("zh", "en"), ("en", "zh")])
def test_nonempty_target_message_handles_different_server_language(environment, monkeypatch,
                                                                 client_language, server_language):
    e = environment
    core.set_lang(client_language)
    e.server.mkdir()
    (e.server / "notes.txt").write_text("keep")
    dialog = e.target()
    def reply(method, args, callback, **kwargs):
        key = "此目录没有工作目录记录" if method == "get_pipeline" else "目标目录不是空的；已有流程请先在本机取出编辑。"
        callback(None, ValueError(core.EN[key] if server_language == "en" else key))
    monkeypatch.setattr(e.source, "submit", reply)
    dialog.accept()
    assert dialog.status.text() == core.T(
        "此目录不是空的，也没有计算记录，不能上传到这里。请选择空目录，或点“新建子目录…”。")
    assert dialog.result() != QDialog.Accepted


@pytest.mark.parametrize("method", ["get_pipeline", "preview_upload"])
@pytest.mark.parametrize("error", [PermissionError("permission denied"), ConnectionError("connection lost"),
                                 ValueError("damaged record or invalid configuration")])
def test_other_target_failures_preserve_the_original_reason(environment, monkeypatch, method, error):
    e = environment
    e.server.mkdir()
    (e.server / "notes.txt").write_text("keep")
    dialog = e.target()
    original = e.source.submit
    def reply(operation, args, callback, **kwargs):
        if operation == method:
            callback(None, error)
        else:
            original(operation, args, callback, **kwargs)
    monkeypatch.setattr(e.source, "submit", reply)
    dialog.accept()
    assert str(error) in dialog.status.text() and dialog.result() != QDialog.Accepted


@pytest.mark.parametrize("browsed", [False, True])
def test_pending_target_keeps_an_existing_default_directory(environment, monkeypatch, browsed):
    e = environment
    e.source.server["home"] = str(e.root)
    if browsed:
        e.server.mkdir()
        dialog = e.target()
        monkeypatch.setattr(QInputDialog, "getText", lambda *args, **kwargs: ("local", True))
        dialog.new_button.click()
        expected = str(e.server)
    else:
        dialog = e.target(e.server / "nested")
        expected = str(e.root)
    pending = dialog.directory
    assert dialog.pending_target
    dialog.accept()
    assert dialog.result() == QDialog.Accepted and dialog.directory == pending
    assert core.load_config()["remote_hosts"]["@fixture:22"]["directory"] == expected
    assert not Path(pending).exists()
    assert not any(method in remote.MUTATIONS for method, _ in e.source.calls)


@pytest.mark.parametrize("language", ["zh", "en"])
@pytest.mark.parametrize("linked", [False, True])
def test_upload_preview_keeps_run_settings_warning_and_upload_notices(environment, monkeypatch, language, linked):
    e = environment
    core.set_lang(language)
    e.source.server["capabilities"].append("run_settings_v1")
    core.save_config(remote_hosts=dict(fixture=dict(run=dict(bindir="/missing/grasp/bin", np=1))))
    dialog = e.upload(linked)
    dialog.none_button.click()
    warning = dialog.last_preview["run_settings_warning"]
    notices = [warning, core.T("只写入流程，不传输输入文件"), "rcsf.inp"]
    notices.append(core.T("更新 {h}:{p} 的流程", h="fixture", p=str(e.server)) if linked else
                   core.T("确认上传时创建"))
    assert all(text in dialog.report.toPlainText() for text in notices)
    confirmations = []
    monkeypatch.setattr(QMessageBox, "question", lambda *args: confirmations.append(args) or QMessageBox.No)
    dialog.start_transfer()
    assert all(text in confirmations[0][2] for text in notices)


def test_linked_upload_displays_update_and_server_changes(environment, monkeypatch):
    e = environment
    dialog = e.upload(linked=True)
    desired = copy.deepcopy(dialog.configuration)
    desired["jobs"][0]["vals"]["z"] = "26"
    dialog.configuration = desired
    dialog.none_button.click()
    preview = e.source.operation.preview_upload(str(e.server), desired, dialog.revision, uploads=[])
    confirmations = []
    monkeypatch.setattr(QMessageBox, "question", lambda *args: confirmations.append(args) or QMessageBox.Yes)
    dialog.start_transfer()
    title = core.T("更新 {h}:{p} 的流程", h="fixture", p=str(e.server))
    assert dialog.windowTitle() == confirmations[0][1] == title
    assert title in confirmations[0][2] and preview["changes"] in confirmations[0][2]
    assert [m for m, _ in e.source.calls].count("update_workdir") == 1
    assert (e.server / "manual.txt").read_text() == "keep"


@pytest.mark.parametrize("language", ["zh", "en"])
def test_calculation_upload_button_follows_association(environment, qapp, language):
    from test_gui import window
    e = environment
    core.set_lang(language)
    w = window(e.local)
    try:
        assert w.calc.upload_button.text() == core.T("上传到服务器…")
        edit.bind(e.local, dict(host="fixture", directory=str(e.server), revision="revision"))
        w.calc.refresh_remote_copy()
        assert w.calc.upload_button.text() == core.T("更新服务器流程…")
        edit.bind(e.local, None)
        w.calc.refresh_remote_copy()
        assert w.calc.upload_button.text() == core.T("上传到服务器…")
    finally:
        assert w.close()
