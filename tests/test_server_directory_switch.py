"""同一个服务器工作区标签和 SSH 会话中的目录切换。"""

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

import grasp_config as config
import grasp_core as core
import grasp_drafts as drafts
import grasp_ssh as ssh

QtWidgets = pytest.importorskip("PySide6.QtWidgets")
from remote_panel import BrowseServerDirectory  # noqa: E402
from test_gui import spin  # noqa: E402
from test_remote_edit_ssh import call, energies, peer, target, wait_done  # noqa: F401, E402
from test_remote_workspace_edit import KEY, answers, edit, open_editable, preview  # noqa: F401, E402
from test_workspace_gui import Source, desk  # noqa: F401, E402

pytestmark = pytest.mark.gui


def listing(path, *names, truncated=False):
    return dict(directory=path, parent=str(Path(path).parent), truncated=truncated,
                files=[dict(name=name, directory=True, type="directory", size=0) for name in names])


class TrackedSource(Source):
    def __init__(self):
        super().__init__("fixture")
        self.requests, self.cancelled = {}, set()

    def submit(self, method, args, callback, **options):
        super().submit(method, args, callback, **options)
        request = len(self.requests) + 1
        self.requests[request] = (method, args, callback)
        return request

    def cancel(self, request):
        self.cancelled.add(request)


def test_typed_path_replaces_directory_in_place_and_cancels_old_reads(desk, qapp):
    source = TrackedSource()
    workspace = desk.open_remote_workspace(source, "/srv/a")
    other = desk.open_remote_workspace(Source("other", "user@other:22"), "/srv/x")
    desk.workspaces.setCurrentWidget(workspace)
    workspace.show_tab("wfn")
    viewer = workspace.open_view("levels")
    old_requests = dict(source.requests)
    write_results = []
    write_request = workspace.submit("update_workdir", dict(directory="/srv/a"), lambda *args: write_results.append(args))
    workspace.directory_edit.setText("bad")
    workspace.directory_edit.returnPressed.emit()
    method, args, received = source.pending.pop()
    assert (method, args) == ("list_dir", {"directory": "bad"})
    received(None, ValueError("not a directory"))
    assert workspace.status.currentMessage() == "not a directory" and workspace.directory_edit.text() == "/srv/a"
    workspace.directory_edit.setText("~/next")
    workspace.directory_edit.returnPressed.emit()
    desk.set_language("zh")
    desk.change_font_size(1)
    assert not workspace.directory_edit.isEnabled()
    source.respond("list_dir", listing("/srv/b"), directory="~/next")
    opened = desk.active_remote()
    assert opened.directory_edit.text() == "/srv/b"
    assert opened.tabs.currentIndex() == 1 and opened.tabs.tabText(1) == "径向波函数"
    assert opened.source is source and source.parent() is opened and source.closes == source.reconnects == 0
    assert desk.workspaces.indexOf(opened) == 1 and desk.workspaces.indexOf(other) == 2
    assert workspace.closed and set(old_requests) <= source.cancelled and not viewer.isVisible()
    assert write_request not in source.cancelled
    source.respond("update_workdir", dict(revision="r2"), directory="/srv/a")
    assert write_results == []
    for method, _, callback in old_requests.values():
        if method == "watch":
            callback(dict(text="old log", state=None), None)
    assert "old log" not in opened.calc.output.toPlainText()
    source.respond("watch", dict(text="new log", state=None), directory="/srv/b")
    assert opened.calc.output.toPlainText() == "new log"
    source.client.connected = False
    source.connectionChanged.emit()
    assert all(not control.isEnabled() for control in opened.directory_controls)


def test_browser_siblings_recent_and_existing_tab(desk, monkeypatch):
    source = Source("fixture")
    workspace = desk.open_remote_workspace(source, "/srv/a")

    def browse(dialog):
        assert dialog.windowTitle() == "Browse working directory on server"
        assert dialog.use.text() == "Open this working directory"
        source.respond("list_dir", listing("/srv/a"), directory="/srv/a")
        dialog.up.click()
        source.respond("list_dir", listing("/srv", "a", "b"), directory="/srv")
        dialog.files.itemDoubleClicked.emit(dialog.files.item(1))
        source.respond("list_dir", listing("/srv/b"), directory="/srv/b")
        return QtWidgets.QDialog.Accepted

    monkeypatch.setattr(BrowseServerDirectory, "exec", browse)
    workspace.browse_button.click()
    current = desk.active_remote()
    assert current.directory == "/srv/b" and current.source is source
    current.load_siblings()
    assert current.siblings_menu.actions()[0].text() == "Loading…"
    source.respond("list_dir", listing("/srv", "c", "b", "a", truncated=True), directory="/srv")
    items = current.siblings_menu.actions()
    assert [item.text() for item in items] == ["a", "b", "c", "Showing only the first 3 entries"]
    assert items[1].isChecked()
    items[2].trigger()
    source.respond("list_dir", listing("/srv/c"), directory="/srv/c")
    current = desk.active_remote()
    assert current.directory == "/srv/c" and current.source is source
    alias = Source("second")
    existing = desk.open_remote_workspace(alias, "/srv/a")
    desk.workspaces.setCurrentWidget(current)
    current.load_recent()
    assert current.recent_menu.actions()[-1].text() == "Clear recent working directories for this host"
    [item for item in current.recent_menu.actions() if item.text() == "/srv/a"][0].trigger()
    source.respond("list_dir", listing("/srv/a"), directory="/srv/a")
    assert desk.active_remote() is existing and not current.closed and current.directory_edit.text() == "/srv/c"
    assert source.closes == alias.closes == 0
    core.save_config(remote_recent=[["fixture", "/srv/a", current.key[0]], ["other", "/srv/z", "u@other:22"]])
    current.load_recent()
    current.recent_menu.actions()[-1].trigger()
    assert core.load_config()["remote_recent"] == [["other", "/srv/z", "u@other:22"]]


@pytest.mark.parametrize("choice", ("keep", "discard", "save", "save-failed"))
def test_switch_uses_existing_draft_choices(desk, answers, monkeypatch, choice):
    from remote_editor import DraftsDialog

    workspace, source = open_editable(desk)
    edit(workspace)

    def choose(dialog):
        box = dialog.choices[0][1]
        assert box.currentData() == "keep"
        box.setCurrentIndex(box.findData("save" if choice == "save-failed" else choice))
        return QtWidgets.QDialog.Accepted

    monkeypatch.setattr(DraftsDialog, "exec", choose)
    workspace.request_directory("/srv/b")
    source.respond("list_dir", listing("/srv/b"))
    if choice.startswith("save"):
        assert desk.active_remote() is workspace
        answers.replies.append(QtWidgets.QMessageBox.Yes)
        source.respond("preview_upload", preview())
        if choice == "save-failed":
            source.pending.pop()[2](None, ValueError("write failed"))
            assert desk.active_remote() is workspace and drafts.load(KEY) is not None
            assert workspace.directory_edit.isEnabled()
            return
        source.respond("update_workdir", dict(revision="r2", warnings=[]))
    assert desk.active_remote().directory == "/srv/b"
    assert (drafts.load(KEY) is not None) == (choice == "keep")


@pytest.mark.parametrize("success", (True, False))
def test_switch_waits_for_a_save_already_in_progress(desk, answers, monkeypatch, success):
    from remote_editor import DraftsDialog

    workspace, source = open_editable(desk)
    edit(workspace)
    answers.replies.append(QtWidgets.QMessageBox.Yes)
    workspace.calc.save_button.click()
    source.respond("preview_upload", preview())

    def choose(dialog):
        box = dialog.choices[0][1]
        box.setCurrentIndex(box.findData("save"))
        return QtWidgets.QDialog.Accepted

    monkeypatch.setattr(DraftsDialog, "exec", choose)
    workspace.request_directory("/srv/b")
    source.respond("list_dir", listing("/srv/b"))
    assert desk.active_remote() is workspace and workspace.switching_directory
    assert sum(method == "update_workdir" for method, _, _ in source.pending) == 1
    if success:
        source.respond("update_workdir", dict(revision="r2", warnings=[]))
        assert desk.active_remote().directory == "/srv/b" and drafts.load(KEY) is None
    else:
        source.pending.pop()[2](None, ValueError("write failed"))
        assert desk.active_remote() is workspace and not workspace.switching_directory
        assert drafts.load(KEY) is not None


def test_active_transfer_defaults_to_staying(desk, monkeypatch):
    workspace = desk.open_remote_workspace(Source("fixture"), "/srv/a")
    workspace.transfer_dialogs.append(SimpleNamespace(busy=True))
    defaults = []
    monkeypatch.setattr(QtWidgets.QMessageBox, "question",
                        lambda *args: defaults.append(args[-1]) or QtWidgets.QMessageBox.No)
    workspace.open_directory("/srv/b")
    assert desk.active_remote() is workspace and workspace.directory_edit.isEnabled()
    assert defaults == [QtWidgets.QMessageBox.No]
    workspace.transfer_dialogs.clear()


@pytest.mark.grasp
@pytest.mark.skipif(os.environ.get("GRASP_REMOTE_EDIT_TEST") != "1" or not os.environ.get("GRASP_SSH_TEST_HOST"),
                    reason="dedicated workspace SSH fixture not configured")
def test_real_ssh_sibling_switch_displays_both_calculations(desk, qapp, peer, monkeypatch):
    from remote_panel import Source as SshSource

    demos = Path(__file__).resolve().parents[1] / "demos"
    directories = {}
    for name in ("example1", "example9"):
        cfg, _ = config.load(demos / f"{name}.json")
        if name == "example9":
            # 最后一个自定义命令只导出跃迁；本次验收需要的是此前已生成的能级和波函数。
            cfg["jobs"] = cfg["jobs"][:-1]
        assert not config.custom_commands(cfg)
        directory = target()
        directories[directory] = cfg["reference"]
        created = call(peer, "create_workdir", directory, configuration=cfg)
        call(peer, "run", directory, revision=created["revision"], accept_logout=True)
        wait_done(peer, directory)
    first, second = directories
    source = SshSource(peer)
    desk.open_remote_workspace(source, first)
    process, generation = peer.process, peer.generation
    monkeypatch.setattr(ssh, "Client", lambda *_a, **_k: pytest.fail("created another SSH session"))

    def check(path):
        workspace = desk.active_remote()
        spin(qapp, lambda: workspace.levels.level_table.rowCount() > 0 and workspace.waves.table.rowCount() > 0)
        assert workspace.source is source and workspace.directory == path
        assert peer.process is process and peer.generation == generation
        assert workspace.levels.remote_directory == workspace.waves.remote_directory == path
        for reference in directories[path]:
            assert energies(peer, path, reference["file"]) == pytest.approx(reference["energies"], abs=1e-7)
        return workspace

    current = check(first)
    current.show_tab("levels")
    for path in (second, first):
        name = Path(path).name
        current.load_siblings()
        spin(qapp, lambda: any(action.text() == name for action in current.siblings_menu.actions()))
        next(action for action in current.siblings_menu.actions() if action.text() == name).trigger()
        spin(qapp, lambda: desk.active_remote().directory == path)
        current = check(path)
        assert current.tabs.currentIndex() == 2
