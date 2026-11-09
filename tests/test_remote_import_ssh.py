"""可选真实 SSH：配置直接写入服务器、可选输入上传，以及原运行路径的能量核对。"""

import os
from pathlib import Path
import subprocess

import pytest

import grasp_config as config
import grasp_core as core
from test_remote_edit_ssh import call, energies, peer, target, wait_done

pytestmark = [pytest.mark.gui, pytest.mark.grasp,
              pytest.mark.skipif(os.environ.get("GRASP_REMOTE_EDIT_TEST") != "1"
                                 or not os.environ.get("GRASP_SSH_TEST_HOST"),
                                 reason="dedicated remote-import SSH fixture not configured")]


@pytest.mark.parametrize("with_input", [False, True])
def test_real_ssh_clipboard_create_upload_and_reference(qapp, tmp_path, monkeypatch, peer, grasp_bindir, with_input):
    import time
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QDialog, QFileDialog, QMessageBox, QWidget
    from calc_panel import ConfigDialog
    from remote_edit_panel import UploadDialog
    from remote_panel import Source
    cfg, _ = config.load(Path(__file__).resolve().parents[1] / "demos/example1.json")
    inputs = tmp_path / "inputs"
    if with_input:
        inputs.mkdir()
        script = core.shell_script([config.make_job(cfg["jobs"][0])], grasp_bindir, ".")
        subprocess.run(["sh", "-c", script], cwd=inputs, check=True, capture_output=True, timeout=60)
        cfg = dict(cfg, jobs=cfg["jobs"][1:])
    QGuiApplication.clipboard().setText(config.dumps(cfg))
    chooser = ConfigDialog(None, [], "", remote_directory=os.environ["GRASP_SSH_TEST_DIRECTORY"])
    assert chooser.open_clipboard()
    directory = target().replace("/edit-", "/import-")
    chooser.dir.setText(directory)
    chooser.accept()
    assert chooser.result() == QDialog.Accepted
    parent = QWidget()
    source = Source(peer, parent)
    source.server = peer.request("hello", dict(protocol=1)).result(60)
    dialog = UploadDialog(parent, source, chooser.target(), chooser.entry()["cfg"], None,
                          remote_only=True, own_source=False)
    opened = []
    dialog.remoteCreated.connect(lambda *args: opened.append(args))
    def wait(predicate):
        deadline = time.monotonic() + 180
        while not predicate():
            assert time.monotonic() < deadline
            qapp.processEvents()
            time.sleep(0.01)
    try:
        wait(lambda: dialog.ready or bool(dialog.status.text() and "更新" in dialog.status.text()))
        assert dialog.ready, dialog.status.text()
        if with_input:
            monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *args: str(inputs))
            dialog.choose_inputs()
            wait(lambda: dialog.ready)
            assert any(row["name"] == "isodata" for row in dialog.selected())
        monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Yes)
        dialog.start_transfer()
        wait(lambda: bool(opened))
        assert opened == [(directory, False)] and dialog.local_directory is None
        planned = call(peer, "run_plan", directory)
        assert not planned["custom_commands"]
        call(peer, "run", directory, revision=planned["revision"], accept_logout=True)
        wait_done(peer, directory)
        for ref in cfg["reference"]:
            assert energies(peer, directory, ref["file"]) == pytest.approx(ref["energies"], abs=1e-7)
    finally:
        dialog.shutdown()
        source.close()
        chooser.deleteLater()
        parent.deleteLater()
