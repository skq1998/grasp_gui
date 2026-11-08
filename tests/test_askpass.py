"""OpenSSH ASKPASS 的主机确认、秘密输入与无回答通知。"""

import os
from pathlib import Path
import subprocess
import sys

import pytest

QtWidgets = pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtCore import Qt

import grasp_askpass as askpass
import grasp_core as core
from conftest import ROOT

pytestmark = pytest.mark.gui
FINGERPRINT = "SHA256:abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG"
HOST_PROMPT = ("The authenticity of host 'cluster.example (192.0.2.1)' can't be established.\n"
               "ED25519 key fingerprint is " + FINGERPRINT + ".\n"
               "Are you sure you want to continue connecting (yes/no/[fingerprint])? ")


@pytest.fixture
def prompt_env(monkeypatch):
    monkeypatch.delenv("SSH_ASKPASS_PROMPT", raising=False)
    monkeypatch.setattr(core, "load_config", lambda: {"lang": "zh"})
    monkeypatch.setattr(sys, "argv", ["grasp_gui", "_askpass", HOST_PROMPT])


@pytest.mark.parametrize("prompt", [HOST_PROMPT, HOST_PROMPT.replace("/[fingerprint]", ""),
    "Please type 'yes', 'no' or the fingerprint: ", "Please type 'yes' or 'no': "])
def test_host_key_confirmation_is_visible(qapp, monkeypatch, capsys, prompt_env, prompt):
    sys.argv[2] = prompt
    def enter(parent, title, message, echo):
        assert title == "SSH 主机确认"
        assert prompt in message and "核对主机指纹" in message
        assert echo == QtWidgets.QLineEdit.Normal
        if prompt == HOST_PROMPT:
            assert FINGERPRINT in message
        return FINGERPRINT, True
    monkeypatch.setattr(QtWidgets.QInputDialog, "getText", enter)
    assert askpass.main() == 0
    assert capsys.readouterr().out == FINGERPRINT + "\n"


@pytest.mark.parametrize("prompt", ["user@cluster's password: ", "Enter passphrase for key '/key': ",
                                   "Verification code: "])
def test_password_and_codes_stay_hidden(qapp, monkeypatch, capsys, prompt_env, prompt):
    sys.argv[2] = prompt
    def enter(parent, title, message, echo):
        assert message == prompt and echo == QtWidgets.QLineEdit.Password
        return "test-secret", True
    monkeypatch.setattr(QtWidgets.QInputDialog, "getText", enter)
    assert askpass.main() == 0
    assert capsys.readouterr().out == "test-secret\n"


def test_cancel_host_key_has_no_answer(qapp, monkeypatch, capsys, prompt_env):
    monkeypatch.setattr(QtWidgets.QInputDialog, "getText", lambda *a: ("", False))
    assert askpass.main() == 1
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("accepted", [True, False])
def test_confirm_hint_keeps_default_no(qapp, monkeypatch, capsys, prompt_env, accepted):
    monkeypatch.setenv("SSH_ASKPASS_PROMPT", "confirm")
    def confirm(parent, title, prompt, buttons, default):
        assert default == QtWidgets.QMessageBox.No
        return QtWidgets.QMessageBox.Yes if accepted else QtWidgets.QMessageBox.No
    monkeypatch.setattr(QtWidgets.QMessageBox, "question", confirm)
    assert askpass.main() == (0 if accepted else 1)
    assert capsys.readouterr().out == ("yes\n" if accepted else "no\n")


def test_none_hint_is_notification_without_input_or_answer(qapp, monkeypatch, capsys, prompt_env):
    prompt = "Confirm user presence for key ED25519-SK SHA256:example"
    sys.argv[2] = prompt
    monkeypatch.setenv("SSH_ASKPASS_PROMPT", "none")
    def no_input(*args):
        pytest.fail("notifications must not ask for input")
    monkeypatch.setattr(QtWidgets.QInputDialog, "getText", no_input)
    def shown(app):
        notices = [w for w in app.topLevelWidgets() if w.isVisible() and w.windowTitle() == "SSH 提示"]
        assert len(notices) == 1
        notice = notices[0]
        assert not notice.isModal()
        assert not notice.findChildren(QtWidgets.QLineEdit)
        assert not notice.findChildren(QtWidgets.QAbstractButton)
        label = notice.findChild(QtWidgets.QLabel)
        assert label.text() == prompt and label.textFormat() == Qt.PlainText
        notice.close()
        return 0
    monkeypatch.setattr(QtWidgets.QApplication, "exec", shown)
    assert askpass.main() == 0
    assert capsys.readouterr().out == ""


def test_none_process_can_be_ended_by_ssh_without_answer():
    env = dict(os.environ, SSH_ASKPASS_PROMPT="none")
    proc = subprocess.Popen([sys.executable, str(Path(ROOT) / "grasp_gui.py"), "_askpass", "Touch security key"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            proc.communicate(timeout=1)
        proc.terminate()
        out, err = proc.communicate(timeout=5)
        assert out == b"" and b"Traceback" not in err
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate(timeout=5)
