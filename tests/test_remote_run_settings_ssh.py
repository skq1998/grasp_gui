"""可选专用 sshd 验收：服务器无配置 / GRASP PATH，桌面设置后运行串行与 MPI。"""

import os
from pathlib import Path
import subprocess
import time

import pytest

import grasp_config as config
import grasp_core as core
from test_remote_edit_ssh import call, energies, peer, target, wait_done  # noqa: F401

pytestmark = [pytest.mark.gui, pytest.mark.grasp,
              pytest.mark.skipif(os.environ.get("GRASP_REMOTE_RUN_TEST") != "1"
                                 or not os.environ.get("GRASP_SSH_TEST_HOST"),
                                 reason="dedicated run-settings SSH fixture not configured")]


@pytest.mark.parametrize("count", [1, 2])
def test_real_ssh_settings_dialog_run_and_unchanged_server_preferences(qapp, tmp_path, monkeypatch, peer, count):
    from PySide6.QtWidgets import QDialog
    from remote_panel import ServerRunDialog, Source
    monkeypatch.setattr(core, "CONFIG_FILE", str(tmp_path / "desktop.json"))
    command = 'if test -e "$HOME/.config/grasp_gui.json"; then echo present; else echo absent; fi'
    def preferences():
        result = subprocess.run(peer.argv()[:-1] + [command], env=peer.environment(),
                                text=True, capture_output=True, check=True, timeout=30)
        return result.stdout.strip()
    assert preferences() == "absent", "Use a clean dedicated server account; do not remove a user's configuration"
    source = Source(peer)
    source.server = peer.request("hello", dict(protocol=1)).result(60)
    assert "run_settings_v1" in source.server["capabilities"], "Install this branch's server build"
    cfg, _ = config.load(Path(__file__).resolve().parents[1] / "demos/example1.json")
    directory = target().replace("/edit-", "/run-settings-")
    created = call(peer, "create_workdir", directory, configuration=cfg)
    plan = call(peer, "run_plan", directory)
    assert plan["missing_programs"], "The fixture's noninteractive PATH must not contain GRASP"
    dialog = ServerRunDialog(None, source, directory, plan, {})
    def wait(predicate):
        deadline = time.monotonic() + 60
        while not predicate():
            assert time.monotonic() < deadline, dialog.report.toPlainText()
            qapp.processEvents()
            time.sleep(0.01)
    try:
        wait(lambda: dialog.checked_values is not None)
        assert dialog.cancel.isDefault()
        dialog.settings.bindir_edit.setText(os.environ["GRASP_SSH_TEST_BINDIR"])
        dialog.settings.np_spin.setValue(count)
        dialog.settings.mpirun_edit.setText(os.environ.get("GRASP_SSH_TEST_MPIRUN", "/usr/bin/mpirun"))
        dialog.check()
        wait(lambda: dialog.checked_values == dialog.settings.values())
        assert not dialog.plan["missing_programs"] and not dialog.plan["checked"]["issues"]
        dialog.ok.click()
        assert dialog.result() == QDialog.Accepted
        assert preferences() == "absent"
        call(peer, "run", directory, revision=created["revision"], run_settings=dialog.settings.values(),
             accept_logout=True)
        wait_done(peer, directory)
        for ref in cfg["reference"]:
            assert energies(peer, directory, ref["file"]) == pytest.approx(ref["energies"], abs=1e-7)
        if count == 2:
            log = call(peer, "tail_log", directory, lines=1000)["text"].lower()
            assert any(program in log for program in ("rangular_mpi", "rmcdhf_mpi", "rci_mpi"))
        assert preferences() == "absent"
    finally:
        dialog.reject()
        dialog.deleteLater()
        source.close()
