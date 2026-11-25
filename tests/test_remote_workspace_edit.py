"""服务器工作区的编辑、本机草稿、保存到服务器及冲突与失去回答的处理 (#52 第二阶段)。"""

import hashlib
import json
import os
import stat

import pytest

import grasp_config as config
import grasp_core as core
import grasp_drafts as drafts
import grasp_ssh as ssh

KEY = ("user@fixture:22", "/srv/a")


def configuration(order=("rnucleus", "rcsfgenerate")):
    jobs = [dict(step=key, vals=config.STEPS[key].defaults()) for key in order]
    return dict(format=config.FORMAT, version=config.VERSION, title="a", jobs=jobs)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "CONFIG_FILE", str(tmp_path / "preferences.json"))
    return tmp_path


def test_draft_file_format_permissions_and_bad_files(home):
    cfg, changed = configuration(), configuration(("rcsfgenerate", "rnucleus"))
    raw = json.dumps(list(KEY), ensure_ascii=True, separators=(",", ":"))
    assert drafts.path(KEY) == home / "grasp_gui" / "drafts" / (hashlib.sha256(raw.encode()).hexdigest() + ".json")
    assert drafts.load(KEY) is None
    saved = drafts.save(KEY, "r1", drafts.fingerprint(cfg), changed)
    assert stat.S_IMODE(os.stat(drafts.directory()).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(drafts.path(KEY)).st_mode) == 0o600
    assert drafts.load(KEY) == saved
    assert set(saved) == {"version", "host", "directory", "base_revision", "base_fingerprint", "configuration",
                          "fingerprint", "updated"}
    assert saved["version"] == 1 and saved["fingerprint"] == drafts.fingerprint(changed)
    pending = dict(revision="r1", fingerprint=saved["fingerprint"])
    assert drafts.save(KEY, "r1", drafts.fingerprint(cfg), changed, pending)["pending"] == pending
    assert drafts.load(KEY)["pending"] == pending
    assert drafts.fingerprint(dict(cfg, jobs=[])) != drafts.fingerprint(cfg)
    for broken in (dict(saved, version=2), dict(saved, fingerprint="0" * 64), dict(saved, directory="/srv/b")):
        text = json.dumps(broken)
        drafts.path(KEY).write_text(text)
        with pytest.raises(drafts.DraftError):
            drafts.load(KEY)
        assert drafts.path(KEY).read_text() == text
    drafts.remove(KEY)
    assert drafts.load(KEY) is None
    drafts.remove(KEY)


QtCore = pytest.importorskip("PySide6.QtCore")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")
from test_workspace_gui import Source, desk  # noqa: E402, F401


class Answers:
    """替代消息框：按顺序回答，并记录标题、正文和默认按钮。"""

    def __init__(self, monkeypatch):
        self.asked, self.replies, self.boxes, self.choices = [], [], [], []
        box = QtWidgets.QMessageBox
        monkeypatch.setattr(box, "question", staticmethod(self.question))
        for name in ("information", "warning"):
            monkeypatch.setattr(box, name, staticmethod(lambda parent, title, text, *a: self.asked.append(
                (title, text, None))))
        monkeypatch.setattr(box, "exec", lambda item: self.exec(item))
        monkeypatch.setattr(box, "clickedButton", lambda item: item.choice)

    def question(self, parent, title, text, buttons=None, default=None):
        self.asked.append((title, text, default))
        return self.replies.pop(0) if self.replies else QtWidgets.QMessageBox.No

    def exec(self, item):
        names = [button.text() for button in item.buttons()]
        self.boxes.append((item.text(), names, item.defaultButton().text()))
        wanted = self.choices.pop(0) if self.choices else item.defaultButton().text()
        item.choice = next(button for button in item.buttons() if button.text() == wanted)
        return 0


@pytest.fixture
def answers(monkeypatch):
    from remote_editor import DraftsDialog, PreviewDialog

    reply = Answers(monkeypatch)
    # 默认全部“保留草稿”；需要其他选择的测试另行替换
    monkeypatch.setattr(DraftsDialog, "exec", lambda dialog: QtWidgets.QDialog.Accepted)
    reply.previews = []
    monkeypatch.setattr(PreviewDialog, "exec", lambda dialog: reply.previews.append(
        dialog.findChild(QtWidgets.QPlainTextEdit).toPlainText()) or 1)
    return reply


def open_editable(desk, source=None, path="/srv/a", cfg=None):
    source = source or Source("fixture")
    cfg = cfg or configuration()
    workspace = desk.open_remote_workspace(source, path)
    source.respond("pipeline", dict(jobs=config.session(cfg)["pipeline"], revision="r1", directory=path))
    source.respond("get_pipeline", dict(directory=path, revision="r1", config=cfg, warnings=[]))
    return workspace, source


def preview(**changes):
    value = dict(directory="/srv/a", revision="r1", changes="2 → 2", kept_files=[], custom_commands=[], warnings=[],
                 checked=dict(invalid=[], missing=[], pending=[], issues=[], skipped=[]), missing_programs=[],
                 existing=[])
    value.update(changes)
    return value


def edit(workspace):
    editor = workspace.calc.editor
    editor.plist.setCurrentRow(0)
    editor.move(1)
    return editor


def methods(source):
    return [method for method, _, _ in source.pending]


def test_edit_marks_tab_and_restores_on_reopen(desk, answers, tmp_path):
    workspace, source = open_editable(desk)
    assert workspace.key == KEY and workspace.calc.editor is not None
    assert desk.workspaces.tabText(1) == "fixture:a"
    editor = edit(workspace)
    assert desk.workspaces.tabText(1) == "● fixture:a"
    saved = drafts.load(KEY)
    assert saved["base_revision"] == "r1" and [job["step"] for job in saved["configuration"]["jobs"]] == [
        "rcsfgenerate", "rnucleus"]
    assert saved["base_fingerprint"] == drafts.fingerprint(configuration())
    assert workspace.calc.save_button.isEnabled()
    editor.plist.setCurrentRow(1)
    editor.move(-1)
    assert drafts.load(KEY) is None and desk.workspaces.tabText(1) == "fixture:a"
    edit(workspace)
    desk.close_remote_tab(1)
    assert drafts.load(KEY) is not None and desk.workspaces.count() == 1
    answers.choices.append("Restore draft")
    workspace, source = open_editable(desk)
    assert "draft saved" in answers.boxes[-1][0] and answers.boxes[-1][2] == "Restore draft"
    assert [job["step"] for job in workspace.calc.editor.pipeline] == ["rcsfgenerate", "rnucleus"]
    assert desk.workspaces.tabText(1) == "● fixture:a"
    desk.set_language("zh")
    assert desk.workspaces.tabText(1) == "● fixture:a" and workspace.calc.save_button.text() == "保存到服务器…"
    assert [job["step"] for job in workspace.calc.editor.pipeline] == ["rcsfgenerate", "rnucleus"]
    desk.set_language("en")
    desk.close_remote_tab(1)
    answers.choices.append("Discard draft")
    workspace, _ = open_editable(desk)
    assert drafts.load(KEY) is None and desk.workspaces.tabText(1) == "fixture:a"
    assert [job["step"] for job in workspace.calc.editor.pipeline] == ["rnucleus", "rcsfgenerate"]


def test_unreadable_draft_is_kept_and_editing_stays_off(desk, answers):
    drafts.directory().mkdir(parents=True)
    drafts.path(KEY).write_text('{"version": 9}')
    workspace, _ = open_editable(desk)
    assert "draft" in answers.asked[-1][1].lower() and drafts.path(KEY).read_text() == '{"version": 9}'
    assert not workspace.calc.editor.add_button.isEnabled()
    workspace.calc.editor.store()
    assert drafts.path(KEY).read_text() == '{"version": 9}'


def test_save_previews_confirms_and_writes_by_revision(desk, answers):
    workspace, source = open_editable(desk)
    edit(workspace)
    workspace.calc.save_button.click()
    source.respond("preview_upload", preview(), revision="r1", uploads=[])
    assert "Server fixture:/srv/a" in answers.previews[-1] and "2 → 2" in answers.previews[-1]
    title, text, default = answers.asked[-1]
    assert "fixture:/srv/a" in text and default == QtWidgets.QMessageBox.No
    assert "update_workdir" not in methods(source) and "pending" not in drafts.load(KEY)
    answers.replies.append(QtWidgets.QMessageBox.Yes)
    workspace.calc.save_button.click()
    source.respond("preview_upload", preview())
    method, args, _ = source.pending[-1]
    assert method == "update_workdir" and args["revision"] == "r1" and not args["allow_custom"]
    assert args["configuration"]["jobs"][0]["step"] == "rcsfgenerate"
    assert drafts.load(KEY)["pending"] == dict(revision="r1", fingerprint=drafts.fingerprint(args["configuration"]))
    assert not workspace.calc.save_button.isEnabled() and not workspace.calc.run_button.isEnabled()
    source.respond("update_workdir", dict(revision="r2", warnings=[]))
    assert drafts.load(KEY) is None and desk.workspaces.tabText(1) == "fixture:a"
    assert workspace.calc.revision == "r2" and workspace.calc.editor.base["revision"] == "r2"


def test_custom_commands_need_their_own_confirmation(desk, answers):
    workspace, source = open_editable(desk)
    edit(workspace)
    answers.replies.append(QtWidgets.QMessageBox.No)
    workspace.calc.editor.save_to_server()
    source.respond("preview_upload", preview(custom_commands=[[1, "rm -rf x"]]))
    title, text, default = answers.asked[-1]
    assert title == "Custom commands" and "rm -rf x" in text and default == QtWidgets.QMessageBox.No
    assert "update_workdir" not in methods(source) and drafts.load(KEY) is not None


@pytest.mark.parametrize("stage", ["preview_upload", "update_workdir"])
def test_conflicts_and_running_refuse_and_keep_draft(desk, answers, stage):
    workspace, source = open_editable(desk)
    edit(workspace)
    answers.replies.append(QtWidgets.QMessageBox.Yes)
    workspace.calc.editor.save_to_server()
    if stage == "update_workdir":
        source.respond("preview_upload", preview())
    refusal = "The working directory record was changed by another program"
    _, _, callback = source.pending.pop(next(i for i, item in enumerate(source.pending) if item[0] == stage))
    callback(None, RuntimeError(refusal))
    text, names, default = answers.boxes[-1]
    assert refusal in text and default == "Close"
    assert sorted(names) == ["Close", "Export draft as configuration…", "Reload server pipeline (discard draft)"]
    saved = drafts.load(KEY)
    assert saved is not None and "pending" not in saved and desk.workspaces.tabText(1) == "● fixture:a"
    assert workspace.calc.save_button.isEnabled()


def test_lost_reply_is_reconciled_without_resending(desk, answers):
    workspace, source = open_editable(desk)
    editor = edit(workspace)
    answers.replies.append(QtWidgets.QMessageBox.Yes)
    editor.save_to_server()
    source.respond("preview_upload", preview())
    _, args, callback = source.pending.pop()
    callback(None, ssh.UnknownOutcome("lost"))
    assert methods(source)[-1] == "get_pipeline" and drafts.load(KEY)["pending"]
    source.respond("get_pipeline", dict(directory="/srv/a", revision="r1", config=configuration(), warnings=[]))
    saved = drafts.load(KEY)
    assert saved is not None and "pending" not in saved and workspace.calc.save_button.isEnabled()
    answers.replies.append(QtWidgets.QMessageBox.Yes)
    editor.save_to_server()
    source.respond("preview_upload", preview())
    _, args, callback = source.pending.pop()
    callback(None, ssh.UnknownOutcome("lost"))
    source.respond("get_pipeline", dict(directory="/srv/a", revision="r2", config=args["configuration"], warnings=[]))
    assert drafts.load(KEY) is None and workspace.calc.revision == "r2"
    assert methods(source).count("update_workdir") == 0


def test_pending_draft_is_checked_on_reopen_and_others_changes_are_kept(desk, answers):
    changed = configuration(("rcsfgenerate", "rnucleus"))
    pending = dict(revision="r1", fingerprint=drafts.fingerprint(changed))
    drafts.save(KEY, "r1", drafts.fingerprint(configuration()), changed, pending)
    source = Source("fixture")
    workspace = desk.open_remote_workspace(source, "/srv/a")
    other = configuration(("rnucleus",))
    source.respond("pipeline", dict(jobs=config.session(other)["pipeline"], revision="r9", directory="/srv/a"))
    source.respond("get_pipeline", dict(directory="/srv/a", revision="r9", config=other, warnings=[]))
    assert answers.boxes == [] and "update_workdir" not in methods(source)
    assert "modified" in answers.asked[-1][1]
    saved = drafts.load(KEY)
    assert saved["fingerprint"] == pending["fingerprint"] and "pending" not in saved
    assert [job["step"] for job in workspace.calc.editor.pipeline] == ["rcsfgenerate", "rnucleus"]


def test_disconnect_keeps_draft_and_reconnect_allows_saving(desk, answers):
    workspace, source = open_editable(desk)
    edit(workspace)
    source.client.connected = False
    source.connectionChanged.emit()
    assert workspace.connection.text().endswith("Disconnected") and not workspace.reconnect_button.isHidden()
    assert drafts.load(KEY) is not None and desk.workspaces.tabText(1) == "● fixture:a"
    workspace.reconnect_button.click()
    assert source.reconnects == 1
    source.client.connected = True
    source.connectionChanged.emit()
    assert workspace.reconnect_button.isHidden()
    workspace.calc.save_button.click()
    assert methods(source)[-1] == "preview_upload"


@pytest.mark.parametrize("choice", ["keep", "discard", "save"])
def test_closing_a_tab_with_a_draft(desk, answers, monkeypatch, choice):
    from remote_editor import DraftsDialog

    def chosen(dialog):
        assert [box.currentData() for _, box in dialog.choices] == ["keep"]
        box = dialog.choices[0][1]
        box.setCurrentIndex(box.findData(choice))
        return QtWidgets.QDialog.Accepted
    monkeypatch.setattr(DraftsDialog, "exec", chosen)
    workspace, source = open_editable(desk)
    edit(workspace)
    answers.replies.append(QtWidgets.QMessageBox.Yes)
    desk.close_remote_tab(1)
    if choice == "save":
        assert desk.workspaces.count() == 2
        source.respond("preview_upload", preview())
        source.respond("update_workdir", dict(revision="r2", warnings=[]))
    assert desk.workspaces.count() == 1
    assert (drafts.load(KEY) is not None) == (choice == "keep")


def test_running_with_a_draft_asks_to_save_first(desk, answers):
    workspace, source = open_editable(desk)
    edit(workspace)
    workspace.calc.can_run = True
    workspace.calc.update_run_buttons()
    workspace.calc.run_button.click()
    title, text, default = answers.asked[-1]
    assert "save" in text and default == QtWidgets.QMessageBox.No
    assert "run_plan" not in methods(source) and "preview_upload" not in methods(source)
    answers.replies += [QtWidgets.QMessageBox.Yes, QtWidgets.QMessageBox.Yes]
    workspace.calc.run_button.click()
    source.respond("preview_upload", preview())
    source.respond("update_workdir", dict(revision="r2", warnings=[]))
    method, args, _ = source.pending[-1]
    assert method == "run_plan" and args["directory"] == "/srv/a"


def test_old_server_stays_read_only(desk, answers):
    source = Source("fixture")
    source.server = dict(capabilities=[])
    workspace = desk.open_remote_workspace(source, "/srv/a")
    source.respond("pipeline", dict(jobs=config.session(configuration())["pipeline"], revision="r1",
                                    directory="/srv/a"))
    assert workspace.calc.editor is None and "get_pipeline" not in methods(source)
    assert "Install / update server" in workspace.calc.readonly_hint.text()
    assert workspace.calc.steps.count() == 2


def test_quitting_lists_every_draft(desk, answers, monkeypatch):
    from remote_editor import DraftsDialog

    first, source = open_editable(desk)
    edit(first)
    second, other = open_editable(desk, Source("fixture"), "/srv/b")
    edit(second)
    plans = [["save", "discard"], ["keep", "discard"]]
    answers.replies.append(QtWidgets.QMessageBox.Yes)

    def chosen(dialog):
        assert [box.currentData() for _, box in dialog.choices] == ["keep", "keep"]
        for (_, box), value in zip(dialog.choices, plans.pop(0)):
            box.setCurrentIndex(box.findData(value))
        return QtWidgets.QDialog.Accepted
    monkeypatch.setattr(DraftsDialog, "exec", chosen)
    desk.close()
    assert desk.isVisible() and drafts.load(KEY) is not None
    assert drafts.load(("user@fixture:22", "/srv/b")) is None
    source.respond("preview_upload", preview())
    assert desk.isVisible()
    callback = source.pending.pop()[2]
    callback(None, RuntimeError("refused"))
    assert desk.isVisible() and drafts.load(KEY) is not None
    edit(second)
    desk.close()
    assert not desk.isVisible() and drafts.load(KEY) is not None
