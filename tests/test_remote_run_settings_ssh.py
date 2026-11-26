"""可选专用 sshd 验收：服务器无配置 / GRASP PATH，桌面设置后运行串行与 MPI。"""

import os
from pathlib import Path
import subprocess
import time

import pytest

import grasp_config as config
import grasp_core as core
import grasp_ssh as ssh
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


def test_real_ssh_modal_settings_entry_points_and_example1(qapp, tmp_path, monkeypatch, peer):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QMessageBox
    from remote_panel import ConnectDialog, RemoteWorkspace, ServerRunDialog, Source
    from test_gui import spin

    monkeypatch.setattr(core, "CONFIG_FILE", str(tmp_path / "desktop.json"))
    bindir = os.environ["GRASP_SSH_TEST_BINDIR"]
    cfg, _ = config.load(Path(__file__).resolve().parents[1] / "demos/example1.json")
    directory = target()
    call(peer, "create_workdir", directory, configuration=cfg)

    connection = ConnectDialog()
    monkeypatch.setattr(connection, "open_source", lambda host, program: Source(peer, connection))
    connection.host.setCurrentText(peer.host)
    connection.program.setText(peer.program)
    connection.path.setText(os.environ["GRASP_SSH_TEST_DIRECTORY"])
    connection.show()
    workspace = None
    observed = []
    monitor = QTimer()

    def confirm():
        dialog = qapp.activeModalWidget()
        if isinstance(dialog, QMessageBox) and dialog.windowTitle() == core.T("断线与计算"):
            next(button for button in dialog.buttons() if button.text() == core.T("照常启动")).click()
        if not isinstance(dialog, ServerRunDialog) or dialog.checked_values is None:
            return
        if dialog.settings.bindir() != bindir:
            dialog.settings.bindir_edit.setText(bindir)
            dialog.settings.np_spin.setValue(1)
            dialog.check()
            return
        if dialog.checked_values == dialog.settings.values() and not dialog.plan.get("missing_programs"):
            observed.append((dialog.directory, dialog.run))
            dialog.ok.click()

    def expect(entry, action):
        def dismiss():
            dialog = qapp.activeModalWidget()
            if isinstance(dialog, (ServerRunDialog, QMessageBox)):
                dialog.reject()
        deadline = QTimer()
        deadline.setSingleShot(True)
        deadline.timeout.connect(dismiss)
        deadline.start(15000)
        try:
            action()
            spin(qapp, lambda: entry in observed, timeout=15)
        finally:
            deadline.stop()

    monitor.timeout.connect(confirm)
    monitor.start(20)
    try:
        connection.connect_button.click()
        spin(qapp, lambda: connection.directory and connection.grasp_settings.isVisible())
        # 连接对话框在无流程目录中使用 list_dir 检查。
        expect((connection.directory, False), connection.grasp_settings.click)
        saved = core.T("此主机保存的设置")
        spin(qapp, lambda: saved in connection.grasp_status.text())
        assert ssh.host_profile(peer)["run"]["bindir"] == bindir
        connection.server_type.setCurrentIndex(connection.server_type.findData("workstation"))
        connection.accept()
        workspace = RemoteWorkspace(connection.source, directory)
        workspace.show()
        # 工作区设置和运行确认都使用 run_plan 异步检查。
        spin(qapp, lambda: workspace.calc.run_button.isEnabled() and saved in workspace.grasp_status.text())
        expect((directory, False), workspace.calc.edit_run_settings)
        expect((directory, True), workspace.calc.run_button.click)
        spin(qapp, lambda: (workspace.calc.runtime_state or {}).get("status") == "ok", timeout=90)
        for ref in cfg["reference"]:
            assert energies(peer, directory, ref["file"]) == pytest.approx(ref["energies"], abs=1e-7)
    finally:
        monitor.stop()
        if workspace is not None:
            workspace.shutdown()
            workspace.close()
        else:
            connection.reject()
