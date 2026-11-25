"""受限 SSH 夹具中的服务器工作区编辑与草稿验收。"""

import copy
import os
from pathlib import Path
import subprocess

import pytest

import grasp_config as config
import grasp_core as core
import grasp_drafts as drafts
from conftest import DEMOS
from test_gui import spin
from test_remote_edit_ssh import call, energies, peer, target  # noqa: F401
from test_remote_workspace_edit import answers, desk  # noqa: F401

pytestmark = [pytest.mark.gui, pytest.mark.grasp,
              pytest.mark.skipif(os.environ.get("GRASP_REMOTE_EDIT_TEST") != "1"
                                 or not os.environ.get("GRASP_SSH_TEST_HOST"),
                                 reason="dedicated remote-workspace SSH fixture not configured")]


def open_example(desk, peer, qapp):
    from remote_panel import Source

    cfg, _ = config.load(Path(DEMOS) / "example1.json")
    directory = target()
    created = call(peer, "create_workdir", directory, configuration=cfg)
    workspace = desk.open_remote_workspace(Source(peer), created["directory"])
    spin(qapp, lambda: workspace.calc.editor is not None and workspace.calc.run_button.isEnabled())
    return cfg, workspace


def change_rci(workspace):
    editor = workspace.calc.editor
    index = next(i for i, job in enumerate(editor.pipeline) if job["step"] == "rci")
    editor.plist.setCurrentRow(index)
    editor.edit_job()
    form = editor.nav.currentWidget()
    old = form.values()["trans"]
    form.set_values(dict(form.values(), trans=not old))
    editor.update_job()
    assert editor.draft and editor.pipeline[index]["vals"]["trans"] is not old
    return editor, index


def test_save_and_run_matches_local_rci(desk, qapp, peer, answers, accept_server_run_dialog, grasp_bindir, tmp_path):
    from PySide6.QtWidgets import QMessageBox

    cfg, workspace = open_example(desk, peer, qapp)
    editor, index = change_rci(workspace)
    directory = workspace.directory
    expected = editor.draft["configuration"]
    answers.replies.append(QMessageBox.Yes)
    workspace.calc.save_button.click()
    spin(qapp, lambda: editor.draft is None or bool(answers.boxes))
    assert not answers.boxes and drafts.load(workspace.key) is None
    assert "●" not in desk.workspaces.tabText(desk.workspaces.indexOf(workspace))
    saved = call(peer, "get_pipeline", directory)
    assert saved["config"]["jobs"][index]["vals"]["trans"] is expected["jobs"][index]["vals"]["trans"]
    workspace.calc.run_button.click()
    spin(qapp, lambda: (workspace.calc.runtime_state or {}).get("status") == "ok", timeout=90)
    assert accept_server_run_dialog and accept_server_run_dialog[-1][0] == directory
    local = tmp_path / "local-reference"
    local.mkdir()
    config.install(expected, str(local))
    script = core.shell_script(config.session(expected)["pipeline"], grasp_bindir, ".")
    subprocess.run(["sh", "-c", "set -e\n" + script], cwd=local, check=True, capture_output=True, timeout=180)
    for ref in cfg["reference"]:
        result = config.reference_energies(str(local / ref["file"]))
        assert energies(peer, directory, ref["file"]) == pytest.approx(result, abs=1e-7)


def test_second_connection_change_rejects_save_and_keeps_draft(desk, qapp, peer, answers):
    from PySide6.QtWidgets import QMessageBox

    _, workspace = open_example(desk, peer, qapp)
    editor, index = change_rci(workspace)
    directory = workspace.directory
    original = editor.draft
    other = workspace.source.fork()
    try:
        previous = call(other.client, "get_pipeline", directory)
        cfg = copy.deepcopy(previous["config"])
        cfg["jobs"][index]["vals"]["log"] = "other_rci.log"
        updated = call(other.client, "update_workdir", directory, revision=previous["revision"], configuration=cfg)
        assert updated["revision"] != original["base_revision"]
        answers.replies.append(QMessageBox.Yes)
        workspace.calc.save_button.click()
        spin(qapp, lambda: bool(answers.boxes) or editor.draft is None)
        assert answers.boxes and "draft" in answers.boxes[-1][0].lower()
        assert answers.boxes[-1][2] == "Close"
        persisted = drafts.load(workspace.key)
        assert persisted["base_revision"] == original["base_revision"]
        assert persisted["fingerprint"] == original["fingerprint"] and "pending" not in persisted
        assert "●" in desk.workspaces.tabText(desk.workspaces.indexOf(workspace))
        current = call(other.client, "get_pipeline", directory)
        assert current["revision"] == updated["revision"]
        assert current["config"]["jobs"][index]["vals"]["log"] == "other_rci.log"
    finally:
        other.close()
