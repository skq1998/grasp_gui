"""主窗口工作区、重建期间的异步结果和 SSH 别名。"""

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

import grasp_config as config
import grasp_core as core
import grasp_remote as remote
import grasp_ssh as ssh
from test_gui import spin, window
from test_remote import make_files, wavefunction
from test_remote_edit_ssh import call, energies, peer, target, wait_done  # noqa: F401

QtCore = pytest.importorskip("PySide6.QtCore")
QtTest = pytest.importorskip("PySide6.QtTest")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")
pytestmark = pytest.mark.gui


class Source(QtCore.QObject):
    push = QtCore.Signal(object)
    notice = QtCore.Signal(str)
    connectionChanged = QtCore.Signal()

    def __init__(self, host, identity="user@fixture:22"):
        super().__init__()
        self.client = SimpleNamespace(host=host, connected=True)
        self.workspace_identity = identity
        self.server = dict(capabilities=["workdir_write_v1"])
        self.pending, self.closes, self.reconnects = [], 0, 0

    def submit(self, method, args, callback, **options):
        self.pending.append((method, args, callback))

    def respond(self, method, response, **params):
        index = next(i for i, (name, args, _) in enumerate(self.pending)
                     if name == method and all(args.get(key) == value for key, value in params.items()))
        _, args, callback = self.pending.pop(index)
        callback(response(**args) if callable(response) else response, None)

    def close(self):
        self.closes += 1

    def reconnect(self):
        self.reconnects += 1


@pytest.fixture
def desk(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(core, "CONFIG_FILE", str(tmp_path / "preferences.json"))
    monkeypatch.setattr(core, "LANG", "en")
    workdir = tmp_path / "local"
    workdir.mkdir()
    main = window(workdir, "convergence")
    main.show()
    main.activateWindow()
    qapp.processEvents()
    yield main
    main.close()
    main.deleteLater()
    qapp.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)


def test_tabs_deduplicate_routes_and_restore_only_local(desk, qapp, monkeypatch):
    from remote_panel import ConnectDialog

    a, alias, b = Source("alpha"), Source("other-alias"), Source("alice@alpha", "alice@fixture:22")
    first = desk.open_remote_workspace(a, "/srv/a")
    second = desk.open_remote_workspace(b, "/srv/a")
    assert first is not second and desk.workspaces.count() == 3
    assert desk.open_remote_workspace(alias, "/srv/./a") is first and alias.closes == 1
    assert desk.active_remote() is first
    assert desk.current_tab() == "convergence"
    assert all(not action.isEnabled() for action in desk.local_actions)
    assert not desk.view_actions["convergence"].isEnabled()
    assert not desk.viewer_actions["convergence"].isEnabled()
    assert desk.workspaces.tabToolTip(1).endswith("Workstation")
    assert first.connection.text() == "Workstation · Connected"
    a.client.connected = False
    a.connectionChanged.emit()
    assert first.connection.text() == "Workstation · Disconnected"
    desk.view_actions["levels"].trigger()
    assert first.tabs.currentIndex() == 2 and desk.current_tab() == "convergence"
    viewer = desk.open_viewer("wfn")
    assert viewer.isVisible()
    desk.close_remote_tab(1)
    assert not viewer.isVisible() and a.closes == 1 and b.closes == 0
    desk.workspaces.setCurrentIndex(0)
    assert desk.view_actions["convergence"].isEnabled()
    assert all(action.isEnabled() for action in desk.local_actions)
    local_view = desk.open_viewer("wfn")
    assert local_view.isVisible()
    local_view.close()
    desk.close_remote_tab(0)
    assert desk.workspaces.count() == 2
    calls = []
    monkeypatch.setattr(ConnectDialog, "connect_server", lambda self: calls.append(self.path.text()))
    prompt = ConnectDialog(desk)
    assert prompt.recent.count() == 3 and calls == []
    prompt.select_recent(1)
    assert calls == ["/srv/a"]
    prompt.reject()
    assert desk.close()
    reopened = window(desk.cur_workdir, "")
    try:
        assert reopened.workspaces.count() == 1 and reopened.current_tab() == "convergence"
    finally:
        reopened.close()
        reopened.deleteLater()


def test_recent_server_directories_accept_legacy_entries_and_deduplicate_aliases(desk, monkeypatch):
    from remote_panel import ConnectDialog

    core.save_config(remote_recent=[["old", "/srv/a"], ["later", "/srv/b"], "bad", ["one"], 7,
                                   ["alias", 4], ["alias", "/srv", 2]])
    first = Source("old")
    workspace = desk.open_remote_workspace(first, "/srv/a")
    desk.close_remote_tab(desk.workspaces.indexOf(workspace))
    other = Source("new")
    other.workspace_identity = None
    other.client.identity = "user@fixture:22"
    monkeypatch.setattr(ssh, "connection_identity", lambda *a, **kw: pytest.fail("resolution ran from the GUI"))
    desk.open_remote_workspace(other, "/srv/./a")
    history = core.load_config()["remote_recent"]
    assert history == [["new", "/srv/a", "user@fixture:22"], ["later", "/srv/b", None]]
    dialog = ConnectDialog(desk)
    try:
        assert [dialog.recent.itemText(i) for i in range(1, dialog.recent.count())] == ["new:/srv/a", "later:/srv/b"]
    finally:
        dialog.reject()
        dialog.deleteLater()
    core.save_config(remote_recent="bad")
    dialog = ConnectDialog(desk)
    try:
        assert dialog.recent.count() == 1
    finally:
        dialog.reject()
        dialog.deleteLater()


def test_workspace_shortcuts_take_priority_over_focused_widgets(desk, qapp):
    first = desk.open_remote_workspace(Source("alpha"), "/srv/a")
    other = desk.open_remote_workspace(Source("beta"), "/srv/b")
    first.calc.details.setCurrentWidget(first.calc.preview)
    for focus, page in ((first.tabs.tabBar(), 0), (first.waves.table, 1), (first.calc.preview, 0)):
        desk.workspaces.setCurrentWidget(first)
        first.tabs.setCurrentIndex(page)
        desk.activateWindow()
        focus.setFocus()
        qapp.processEvents()
        assert qapp.focusWidget() is focus
        QtTest.QTest.keyClick(focus, QtCore.Qt.Key_PageDown, QtCore.Qt.ControlModifier)
        assert desk.active_remote() is other and first.tabs.currentIndex() == page
        QtTest.QTest.keyClick(other.tabs.tabBar(), QtCore.Qt.Key_PageUp, QtCore.Qt.ControlModifier)
        assert desk.active_remote() is first


def test_server_actions_and_created_or_uploaded_directories(desk, tmp_path, monkeypatch):
    from remote_edit_panel import UploadDialog

    transport = Source("fixture")
    workspace = desk.open_remote_workspace(transport, "/srv/empty")
    button = next(item for item in workspace.findChildren(QtWidgets.QToolButton) if item.text() == "Server actions")
    actions = {action.text(): action for action in button.menu().actions()}
    opened = []
    monkeypatch.setattr(workspace.calc, "edit_local", lambda: opened.append("edit"))
    monkeypatch.setattr(workspace.calc, "create_calculation", lambda: opened.append("create"))
    monkeypatch.setattr(QtWidgets.QFileDialog, "getOpenFileNames", lambda *args: ([str(tmp_path / "inputs.c")], ""))
    monkeypatch.setattr(QtWidgets.QFileDialog, "getExistingDirectory", lambda *args: str(tmp_path / "inputs"))
    actions["Download to this computer…"].trigger()
    actions["Upload from this computer…"].trigger()
    actions["Upload local directory…"].trigger()
    assert [(item.upload, item.names) for item in workspace.transfer_dialogs] == [
        (False, None), (True, ["inputs.c"]), (True, ["inputs"])]
    actions["Edit locally…"].trigger()
    actions["Create calculation here…"].trigger()
    actions["Reconnect"].trigger()
    assert opened == ["edit", "create"] and transport.reconnects == 1
    workspace.open_created("/srv/new", False)
    created = desk.active_remote()
    assert workspace.closed and created.directory == "/srv/new"
    assert transport.parent() is created and transport.closes == 0 and desk.workspaces.count() == 2
    upload = Source("alias")
    cfg = dict(format=config.FORMAT, version=config.VERSION, jobs=[])
    dialog = UploadDialog(desk.calc, upload, "/srv/new", cfg, None)
    dialog.completed = True
    dialog.open_remote()
    assert desk.active_remote() is created and upload.closes == 1 and transport.closes == 0
    assert not any(name == "run" for name, _, _ in transport.pending)
    dialog.deleteLater()


def test_server_workspace_cleanup_refreshes_pipeline_and_state(desk, tmp_path, monkeypatch):
    from calc_panel import ClearDialog

    step = config.STEPS["rnucleus"]
    cfg = dict(format=config.FORMAT, version=config.VERSION, jobs=[dict(step=step.key, vals=step.defaults())])
    server = tmp_path / "server"
    config.install(cfg, str(server))
    (server / "isodata").write_text("generated")
    operation = remote.Operations()
    transport = Source("fixture")
    transport.server["capabilities"].append("workdir_clean_v1")
    workspace = desk.open_remote_workspace(transport, str(server))
    transport.respond("pipeline", operation.pipeline)
    transport.respond("watch", dict(text="before cleaning", state=None))
    assert workspace.calc.clean_button.isEnabled()

    def confirm(dialog):
        assert dialog.selected_names() == {"isodata"}
        dialog.accept()
        return dialog.result()

    monkeypatch.setattr(ClearDialog, "exec", confirm)
    monkeypatch.setattr(QtWidgets.QMessageBox, "question", lambda *args: QtWidgets.QMessageBox.Yes)
    workspace.calc.preview_clean()
    transport.respond("preview_clean", operation.preview_clean)
    transport.respond("clean_workdir", operation.clean_workdir)
    assert workspace.statusBar().currentMessage() == core.T("清理完成")
    transport.respond("pipeline", operation.pipeline)
    transport.respond("watch", dict(text="after cleaning", state=None))
    assert workspace.calc.output.toPlainText() == "after cleaning"
    assert workspace.calc.steps.count() == 1 and not (server / "isodata").exists()


def test_rebuild_replaces_controls_and_delivers_pending_results(desk, tmp_path, qapp, monkeypatch):
    path = tmp_path / "server"
    path.mkdir()
    make_files(path, 10, 3)
    wavefunction(path / "state.w")
    transport = Source("fixture")
    workspace = desk.open_remote_workspace(transport, str(path))
    workspace.open_transfer()
    dialog = workspace.transfer_dialogs[0]
    transport.respond("transfer_files", dict(files=[dict(name="state.w", size=10)], skipped=[]))
    job = SimpleNamespace(cancel=lambda: None)
    dialog.job = job
    dialog.controls(True)
    dialog.events.put(("progress", dict(percent=40, speed="2MB/s", eta="0:00:30")))
    dialog.poll()
    output, levels = workspace.calc.output, workspace.levels.level_table
    table, progress = workspace.waves.table, dialog.progress
    items = len(transport.pending)
    desk.set_language("zh")
    desk.change_font_size(1)
    qapp.processEvents()
    assert desk.active_remote() is workspace and workspace.source is transport and workspace.client is transport.client
    assert transport.reconnects == 0 and len(transport.pending) == items
    assert workspace.tabs.tabText(1) == "径向波函数"
    assert workspace.calc.details.tabText(2) == "程序输出"
    assert workspace.levels.level_table.horizontalHeaderItem(0).text() == "文件"
    assert workspace.waves.table.horizontalHeaderItem(1).text() == "轨道"
    assert all(old is not new for old, new in ((output, workspace.calc.output), (levels, workspace.levels.level_table),
               (table, workspace.waves.table), (progress, dialog.progress)))
    assert dialog.progress.value() == 40 and dialog.job is job and dialog.busy
    assert "剩余" in dialog.status.text()
    operation = remote.Operations()
    transport.respond("view", operation.view, kind="levels")
    transport.respond("view", operation.view, kind="wfn")
    transport.respond("watch", dict(text="after rebuild", state=None))
    assert workspace.calc.output.toPlainText() == "after rebuild"
    assert workspace.levels.level_table.rowCount() == 3 and workspace.waves.table.rowCount() == 1
    checks = []
    monkeypatch.setattr(QtWidgets.QMessageBox, "question",
                        lambda *args: checks.append(args[-1]) or QtWidgets.QMessageBox.No)
    desk.close_remote_tab(1)
    assert desk.active_remote() is workspace and transport.closes == 0
    assert checks == [QtWidgets.QMessageBox.No]
    dialog.events.put(("done", dict(count=1)))
    dialog.poll()
    assert dialog.progress.value() == 100 and dialog.status.text() == "已按所选规则处理 1 个文件"
    desk.close_remote_tab(1)
    assert desk.workspaces.count() == 1 and transport.closes == 1


def test_ssh_identity_uses_resolved_user_host_and_port(tmp_path):
    path = tmp_path / "ssh_config"
    path.write_text("Host old new\n    HostName Example.ORG\n    User researcher\n    Port 2222\n")
    def resolved(alias, *options):
        return ssh.connection_identity(SimpleNamespace(host=alias, options=("-F", str(path), *options)))
    assert resolved("old") == resolved("new") == "researcher@example.org:2222"
    assert resolved("old", "-p", "2223") == "researcher@example.org:2223"
    assert resolved("other@old") == "other@example.org:2222"


@pytest.mark.grasp
@pytest.mark.skipif(os.environ.get("GRASP_REMOTE_EDIT_TEST") != "1" or not os.environ.get("GRASP_SSH_TEST_HOST"),
                    reason="dedicated workspace SSH fixture not configured")
def test_real_ssh_server_workspaces_display_and_run_example1(desk, qapp, peer, accept_server_run_dialog, monkeypatch):
    from remote_panel import Source as SshSource

    messages = []
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning", lambda parent, title, text: messages.append(text))
    cfg, _ = config.load(Path(__file__).resolve().parents[1] / "demos/example1.json")
    first, second = target(), target()
    created = call(peer, "create_workdir", first, configuration=cfg)
    call(peer, "create_workdir", second, configuration=cfg)
    call(peer, "run", first, revision=created["revision"], accept_logout=True)
    wait_done(peer, first)
    a = SshSource(peer)
    b = a.fork()
    assert b.client is not peer
    one = desk.open_remote_workspace(a, call(peer, "list_dir", first)["directory"])
    two = desk.open_remote_workspace(b, call(b.client, "list_dir", second)["directory"])
    spin(qapp, lambda: one.levels.level_table.rowCount() > 0 and one.waves.table.rowCount() > 0)
    spin(qapp, lambda: two.calc.run_button.isEnabled() and len(two.calc.jobs) == len(cfg["jobs"]))
    desk.view_actions["wfn"].trigger()
    assert desk.active_remote() is two and two.tabs.currentIndex() == 1
    desk.view_actions["calc"].trigger()
    two.calc.run_button.click()
    spin(qapp, lambda: messages or (two.calc.runtime_state or {}).get("status") == "ok", timeout=90)
    assert messages == []
    for kind, panel in (("levels", two.levels), ("wfn", two.waves)):
        desk.view_actions[kind].trigger()
        refresh = next(widget for widget in panel.findChildren(QtWidgets.QPushButton) if widget.text() == "Refresh")
        refresh.click()
    spin(qapp, lambda: two.levels.level_table.rowCount() > 0 and two.waves.table.rowCount() > 0)
    assert accept_server_run_dialog and accept_server_run_dialog[-1][0] == second
    for ref in cfg["reference"]:
        assert energies(b.client, second, ref["file"]) == pytest.approx(ref["energies"], abs=1e-7)
    desk.close_remote_tab(desk.workspaces.indexOf(one))
    assert peer.closed.is_set() and not b.client.closed.is_set()
