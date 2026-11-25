"""连接标识、旧设置迁移及配置文件保护。"""

import builtins
import json
import os
from pathlib import Path
import subprocess
import sys
from threading import Event
from types import SimpleNamespace

import pytest

import grasp_core as core
import grasp_ssh as ssh


@pytest.fixture
def preferences(monkeypatch, tmp_path):
    path = tmp_path / "grasp_gui.json"
    monkeypatch.setattr(core, "CONFIG_FILE", str(path))
    return path


def test_aliases_share_settings_and_legacy_migrates_on_use(preferences):
    first = SimpleNamespace(host="alpha", identity="user@host:22")
    second = SimpleNamespace(host="beta", identity=first.identity)
    core.save_config(remote_hosts=dict(alpha=dict(server_type="slurm", slurm=dict(partition="gpu")),
                                       beta=dict(server_type="workstation", obsolete=True), unused=dict(np=8)))
    assert not ssh.migrate_host_profile(first)
    assert ssh.migrate_host_profile(second)
    ssh.save_host_profile(second, run=dict(np=4))
    assert ssh.host_profile(first) == ssh.host_profile(second) == {
        "aliases": ["beta", "alpha"], "server_type": "slurm", "slurm": {"partition": "gpu"}, "run": {"np": 4},
    }
    assert ssh.profile_for_alias("alpha")[0] == first.identity
    assert set(ssh.host_profiles()) == {first.identity, "unused"}
    assert ssh.host_profiles()["unused"] == {"np": 8}


@pytest.mark.parametrize(("profile_key", "expected_program"), [
    ("user@old:22", ""), ("alias", "/old/bin"), (None, "/old/bin"),
])
def test_worker_chooses_program_before_connecting_to_unconfigured_identity(
        preferences, monkeypatch, profile_key, expected_program):
    connected = Event()
    attempts = []

    def connect(client):
        attempts.append(client.program)
        client.closed.set()
        connected.set()

    monkeypatch.setattr(ssh, "connection_identity", lambda client, *, resolve=True: "user@new:22")
    monkeypatch.setattr(ssh.Client, "_connect", connect)
    monkeypatch.setattr(ssh, "cache_root", lambda: preferences.parent / "cache")
    client = ssh.Client("alias", "/old/bin", profile_key=profile_key)
    try:
        assert connected.wait(5)
        assert attempts == [expected_program]
    finally:
        client.close()


@pytest.mark.parametrize("raw", (b"{lost", b"[]", b"\xff"))
def test_damaged_settings_kept_once(preferences, capsys, raw):
    preferences.write_bytes(raw)
    preferences.chmod(0o640)
    assert core.load_config() == core.load_config() == {}
    core.save_config(lang="en")
    backup, = preferences.parent.glob("grasp_gui.json.broken-*")
    assert backup.read_bytes() == raw and backup.stat().st_mode & 0o777 == 0o640
    assert core.load_config() == {"lang": "en"}
    assert len(capsys.readouterr().err.splitlines()) == 1


def test_unreadable_settings_are_never_replaced(preferences, monkeypatch, capsys):
    preferences.write_text('{"lang":"zh"}')
    old_open = builtins.open

    def denied(path, *args, **kwargs):
        if os.fspath(path) == os.fspath(preferences):
            raise PermissionError("fixture denial")
        return old_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", denied)
    assert core.load_config() == {}
    core.save_config(lang="en")
    core.save_config(lang="en")
    assert preferences.read_text() == '{"lang":"zh"}'
    assert not list(preferences.parent.glob("*.broken-*"))
    assert len(capsys.readouterr().err.splitlines()) == 2


def test_competing_processes_preserve_both_updates_and_one_backup(preferences):
    preferences.write_text("{")
    code = """
import sys
import time
import grasp_core as core
core.CONFIG_FILE = sys.argv[1]
def update(profiles):
    time.sleep(.08)
    profiles[sys.argv[2]] = {"aliases": [sys.argv[2]]}
core.update_config("remote_hosts", update)
"""
    children = [subprocess.Popen([sys.executable, "-c", code, str(preferences), name], stderr=subprocess.PIPE,
                                 text=True, cwd=Path(__file__).resolve().parents[1]) for name in ("alpha", "beta")]
    errors = [child.communicate(timeout=10)[1] for child in children]
    assert [child.returncode for child in children] == [0, 0]
    assert len([line for error in errors for line in error.splitlines()]) == 1
    assert set(json.loads(preferences.read_text())["remote_hosts"]) == {"alpha", "beta"}
    assert len(list(preferences.parent.glob("*.broken-*"))) == 1


@pytest.mark.gui
@pytest.mark.parametrize("damaged", (True, False))
def test_desktop_reports_bad_preferences_at_startup(preferences, damaged):
    if damaged:
        preferences.write_text("{")
    code = """
import errno, sys
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox
import grasp_core as core
import grasp_gui
import grasp_session

core.CONFIG_FILE, damaged, workdir = sys.argv[1:]
damaged = damaged == "True"
asserted = []
original_show = grasp_gui.MainWindow.show

def warning(parent, title, message):
    asserted.append(message)

def show(window):
    original_show(window)
    def check():
        if damaged:
            assert len(asserted) == 1 and ".broken-" in asserted[0], asserted
        else:
            assert "Could not save settings" in window.statusBar().currentMessage()
        print("settings notice checked")
        QApplication.instance().quit()
    QTimer.singleShot(650, check)

def deny(*args):
    raise PermissionError(errno.EACCES, "fixture denial")

QMessageBox.warning = staticmethod(warning)
grasp_gui.MainWindow.show = show
if not damaged:
    grasp_session.atomic = deny
sys.argv = ["grasp_gui.py", "--lang", "en", workdir]
grasp_gui.main()
"""
    result = subprocess.run([sys.executable, "-c", code, str(preferences), str(damaged), str(preferences.parent)],
                            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True,
                            env=dict(os.environ, QT_QPA_PLATFORM="offscreen"), timeout=10)
    assert result.returncode == 0 and "settings notice checked" in result.stdout, result.stderr


@pytest.mark.gui
def test_connect_uses_resolved_identity_and_explicit_settings(preferences, qapp, monkeypatch):
    from remote_panel import ConnectDialog

    core.save_config(remote_hosts={"user@wrong:22": dict(aliases=["alias"], directory="/old", program="/old/bin",
                                                         server_type="workstation"),
                                   "user@correct:22": dict(aliases=["correct"], directory="/new",
                                                           program="/new/bin", server_type="slurm")})
    dialog = ConnectDialog()
    monkeypatch.setattr(ssh, "connection_identity", lambda *args, **kwargs: pytest.fail("SSH lookup in GUI"))
    try:
        dialog.host.setCurrentText("alias")
        assert dialog.path.text() == "/old" and dialog.program.text() == "/old/bin"
        assert dialog.server_type.currentData() == "workstation"
        client = SimpleNamespace(host="alias", identity="user@correct:22", program="/new/bin", close=lambda: None)
        dialog.source = SimpleNamespace(client=client, server={"version": core.VERSION}, close=lambda: None)
        requests = []
        monkeypatch.setattr(dialog, "connect_server", lambda: requests.append(dialog.path.text()))
        assert dialog.apply_connected_profile()
        assert requests == ["/new"]
        assert dialog.program.text() == "/new/bin" and dialog.server_type.currentData() == "slurm"
        assert "user@correct:22" in dialog.profile_message
        assert ssh.profile_for_alias("alias")[0] == "user@correct:22"
    finally:
        dialog.reject()


@pytest.mark.gui
def test_alias_retargeted_to_unconfigured_host_refreshes_program_and_directory(preferences, qapp, monkeypatch):
    from remote_panel import ConnectDialog

    core.save_config(remote_hosts={"user@old:22": dict(aliases=["alias"], directory="/old", program="/old/bin",
                                                      server_type="workstation")})
    dialog = ConnectDialog()
    try:
        dialog.host.setCurrentText("alias")
        assert dialog.program.text() == "/old/bin"
        client = SimpleNamespace(host="alias", identity="user@new:22", program="")
        dialog.source = SimpleNamespace(client=client, server={"version": core.VERSION}, close=lambda: None)
        requests = []
        monkeypatch.setattr(dialog, "connect_server", lambda: requests.append(dialog.path.text()))
        assert dialog.apply_connected_profile()
        assert dialog.program.text() == "" and requests == ["~"]
        assert "user@new:22" in dialog.profile_status.text()
        assert dialog.server_type.currentData() == ""
        assert ssh.host_profiles()["user@old:22"]["program"] == "/old/bin"
    finally:
        dialog.reject()
