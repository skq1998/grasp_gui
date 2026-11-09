"""文件清单、大小选择、SSH 复用参数和界面进度。"""

from pathlib import Path
import threading
import time
from types import SimpleNamespace

import pytest

import grasp_core as core
import grasp_remote as remote
import grasp_transfer as transfer


def caps():
    return dict(path="/usr/bin/rsync", progress2=True, protect=True)


class Client:
    host = "server"
    def argv(self):
        return ["ssh", "-o", "ControlPath=/private/socket", "-J", "jump", "--", self.host, "startup"]


def test_recursive_selection_excludes_links_and_duplicates(tmp_path):
    (tmp_path / "dir").mkdir()
    (tmp_path / "dir/a").write_bytes(b"abc")
    (tmp_path / "link").symlink_to(tmp_path / "dir", target_is_directory=True)
    (tmp_path / "outside").symlink_to("/etc/passwd")
    data = transfer.scan(tmp_path, ["dir", "dir/a", "link", "outside"])
    assert [row["name"] for row in data["files"]] == ["dir/a"]
    assert data["files"][0]["size"] == 3 and set(data["skipped"]) == {"link", "outside"}
    for invalid in ("../escape", "/absolute", ".", 123, "bad\0name"):
        with pytest.raises(ValueError):
            transfer.relative(invalid)
    with pytest.raises(ValueError, match="符号链接"):
        transfer.safe_targets(tmp_path, ["link/a"])
    transfer.safe_targets(tmp_path / "new", ["dir/a"])


def test_server_inventory_and_destination_link_check(tmp_path, monkeypatch):
    (tmp_path / "small").write_text("hello")
    monkeypatch.setattr(transfer, "capabilities", lambda program: dict(caps(), path=program))
    result = remote.Operations().transfer_files(str(tmp_path), rsync="/opt/rsync")
    assert result["files"][0]["name"] == "small" and result["rsync"]["path"] == "/opt/rsync"
    (tmp_path / "target").symlink_to("/tmp", target_is_directory=True)
    with pytest.raises(ValueError, match="符号链接"):
        remote.Operations().transfer_files(str(tmp_path), names=[], targets=["target/file"])


@pytest.mark.parametrize("upload", [False, True])
@pytest.mark.parametrize("policy,flag", [("skip", "--ignore-existing"), ("resume", "--partial"),
                                        ("overwrite", "--whole-file")])
def test_explicit_manifest_and_existing_ssh_options(tmp_path, upload, policy, flag):
    args, manifest = transfer.arguments(Client(), tmp_path, "/remote/space 'quote", ["a b", "dir/file"],
                                        caps(), dict(caps(), path="/opt/rsync space/rsync"), upload, policy)
    assert flag in args and "--checksum" in args and "--partial-dir=.grasp-rsync-partial" in args
    assert not any(arg in args for arg in ("--delete", "-L", "--copy-links"))
    assert manifest == b"a b\0dir/file\0" and "-s" in args
    rsh = args[args.index("-e") + 1]
    assert "ControlPath=/private/socket" in rsh and "-J" not in rsh
    assert "ProxyCommand=false" in rsh and "ControlMaster=no" in rsh and "BatchMode=yes" in rsh
    assert args[-2 if upload else -1] == str(tmp_path.resolve()) + "/"
    assert "--rsync-path='/opt/rsync space/rsync'" in args


def test_old_options_and_missing_rsync_are_explicit(tmp_path, monkeypatch):
    old = dict(caps(), progress2=False, protect=False)
    args, _ = transfer.arguments(Client(), tmp_path, "/remote", ["file"], old, old)
    assert "--progress" in args and "--info=progress2" not in args
    with pytest.raises(ValueError, match="protect-args"):
        transfer.arguments(Client(), tmp_path, "/remote space", ["file"], old, old)
    monkeypatch.setattr(transfer.shutil, "which", lambda name: None)
    with pytest.raises(ValueError, match="找不到 rsync"):
        transfer.capabilities()
    monkeypatch.setattr(transfer.shutil, "which", lambda name: "/bin/sh")
    monkeypatch.setattr(transfer.subprocess, "run", lambda *a, **k:
                        SimpleNamespace(stdout="openrsync: protocol version 27\n-r --recursive", stderr=""))
    with pytest.raises(ValueError, match="GNU rsync"):
        transfer.capabilities()


def test_totals_boundary_and_default_destination():
    rows = [dict(name="small", size=100_000_000), dict(name="large", size=200_000_000),
            dict(name="other", size=300_000_000)]
    assert transfer.totals(rows, {"small", "large"}) == dict(small=dict(count=1, size=100_000_000),
        large=dict(count=1, size=200_000_000), skipped_large=dict(count=1, size=300_000_000))
    assert transfer.download_directory("host", "/srv/calc").endswith("/grasp_calcs/remote/host/calc")
    assert transfer.progress(" 1,234,567 42% 10.2MB/s 0:00:05") == dict(
        bytes=1234567, percent=42, speed="10.2MB/s", eta="0:00:05")
    assert transfer.progress("filename") is None


def test_changed_or_cancelled_preflight_never_starts_transfer(tmp_path, monkeypatch):
    socket = tmp_path / "control"
    socket.touch()
    client = SimpleNamespace(fallback=False, connected=True, control_path=str(socket))
    monkeypatch.setattr(transfer, "capabilities", lambda: caps())
    rows = [dict(name="file", size=1, mtime_ns=1)]
    value = dict(files=[dict(name="file", size=2, mtime_ns=1)], rsync=caps())
    client.request = lambda *args: SimpleNamespace(result=lambda timeout: value)
    destination = tmp_path / "new"
    with pytest.raises(ValueError, match="已改变"):
        transfer.Transfer(client).run(str(destination), "/remote", rows)
    assert not destination.exists()
    job = transfer.Transfer(client)
    def cancelled(timeout):
        job.cancel()
        return value
    client.request = lambda *args: SimpleNamespace(result=cancelled)
    with pytest.raises(InterruptedError, match="已取消"):
        job.run(str(destination), "/remote", rows)
    assert not destination.exists()


@pytest.mark.gui
def test_gui_threshold_confirmation_progress_and_cancel(qapp, monkeypatch, tmp_path):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QMessageBox
    from transfer_panel import TransferDialog
    monkeypatch.setattr(core, "CONFIG_FILE", str(tmp_path / "settings.json"))
    rows = [dict(name="small", size=3, mtime_ns=1), dict(name="large.w", size=200_000_001, mtime_ns=2)]
    class Source:
        client = SimpleNamespace(host="fixture")
        def submit(self, method, args, callback):
            callback(dict(files=rows, skipped=[], rsync=caps()), None)
    seen, finished = [], threading.Event()
    class Job:
        def __init__(self, client):
            self.cancelled = threading.Event()
        def run(self, local, directory, selected, upload, policy, rsync, notify):
            seen.append((selected, upload, policy, rsync))
            notify(dict(percent=40, speed="2MB/s", eta="0:00:30"))
            self.cancelled.wait(2)
            finished.set()
            raise InterruptedError("传输已取消")
        def cancel(self):
            self.cancelled.set()
    monkeypatch.setattr(transfer, "Transfer", Job)
    dialog = TransferDialog(Source(), "/server/calc")
    try:
        assert dialog.threshold.value() == 100 and dialog.policy.currentData() == "skip"
        assert [row["name"] for row in dialog.selected()] == ["small"]
        assert dialog.total["skipped_large"]["count"] == 1
        assert dialog.table.item(1, 1).text() == "200.00 MB"
        dialog.large.click()
        assert len(dialog.selected()) == 2
        dialog.threshold.setValue(250)
        assert len(dialog.selected()) == 2 and dialog.total["large"]["count"] == 0
        dialog.threshold.setValue(50)
        assert [row["name"] for row in dialog.selected()] == ["small"]
        dialog.table.item(1, 0).setCheckState(Qt.Checked)
        assert dialog.total["large"]["size"] == 200_000_001
        monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.No)
        dialog.start_transfer()
        assert seen == []
        monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Yes)
        dialog.policy.setCurrentIndex(1)
        dialog.start_transfer()
        deadline = time.monotonic() + 2
        while not seen and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(.01)
        dialog.poll()
        assert dialog.progress.value() == 40 and "2MB/s" in dialog.status.text()
        assert dialog.busy and seen[0][2] == "resume" and len(seen[0][0]) == 2
        dialog.cancel_transfer()
        assert finished.wait(2)
        deadline = time.monotonic() + 2
        while dialog.busy and time.monotonic() < deadline:
            dialog.poll()
            time.sleep(.01)
        assert not dialog.busy and "已取消" in dialog.status.text()
        assert core.load_config()["remote_hosts"]["fixture"]["transfer_threshold_mb"] == 50
    finally:
        dialog.reject()


@pytest.mark.gui
def test_transfer_close_requires_confirmation(qapp, monkeypatch, tmp_path):
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QMessageBox
    from transfer_panel import TransferDialog

    monkeypatch.setattr(core, "CONFIG_FILE", str(tmp_path / "settings.json"))
    source = SimpleNamespace(client=SimpleNamespace(host="fixture"), submit=lambda *args: None)
    dialog = TransferDialog(source, "/server")
    cancelled, questions = [], []
    dialog.job = SimpleNamespace(cancel=lambda: cancelled.append(True))
    dialog.controls(True)
    dialog.show()
    def decline(*args):
        questions.append(args)
        assert args[-1] == QMessageBox.No
        return QMessageBox.No
    monkeypatch.setattr(QMessageBox, "question", decline)
    try:
        dialog.close()
        assert dialog.isVisible() and not dialog.closed and not cancelled
        QTest.keyClick(dialog, Qt.Key_Escape)
        assert len(questions) == 2 and dialog.timer.isActive() and not cancelled
        monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Yes)
        dialog.close()
        assert dialog.closed and not dialog.isVisible() and cancelled == [True]
        assert not dialog.timer.isActive()
    finally:
        if not dialog.closed:
            dialog.shutdown()
        dialog.deleteLater()
        qapp.sendPostedEvents(None, QEvent.DeferredDelete)


@pytest.mark.gui
def test_modeless_transfer_keeps_windows_usable_and_parent_close_cancels(qapp, monkeypatch, tmp_path):
    from PySide6.QtCore import QEvent, QObject, Qt, Signal
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QMainWindow, QMessageBox, QWidget
    import remote_panel
    from transfer_panel import TransferDialog

    monkeypatch.setattr(core, "CONFIG_FILE", str(tmp_path / "settings.json"))
    class Source(QObject):
        notice = Signal(str)
        client = SimpleNamespace(host="fixture")
        def submit(self, *args):
            pass
        def reconnect(self):
            pass
        def close(self):
            self.closed = True
    class Panel(QWidget):
        message = Signal(str)
        created = Signal(str, bool)
        def __init__(self, *args):
            super().__init__()
        def shutdown(self):
            pass
    for name in ("RemoteCalculation", "RemoteWaves", "RemoteLevels"):
        monkeypatch.setattr(remote_panel, name, Panel)
    monkeypatch.setattr(TransferDialog, "exec", lambda self: pytest.fail("transfer must be modeless"))
    monkeypatch.setattr(QMessageBox, "question", lambda *args: pytest.fail("parent close must cancel directly"))
    main = QMainWindow()
    action = main.menuBar().addAction("test")
    invoked, cancelled = [], []
    action.triggered.connect(lambda: invoked.append(True))
    source = Source()
    win = remote_panel.RemoteWindow(source, "/server", main)
    main.show()
    win.show()
    try:
        win.open_transfer()
        dialog = win.transfer_dialogs[0]
        dialog.job = SimpleNamespace(cancel=lambda: cancelled.append(True))
        dialog.controls(True)
        assert dialog.isVisible() and not dialog.isModal()
        assert QApplication.activeModalWidget() is None
        QTest.mouseClick(win.tabs.tabBar(), Qt.LeftButton, pos=win.tabs.tabBar().tabRect(1).center())
        assert win.tabs.currentIndex() == 1
        QTest.mouseClick(main.menuBar(), Qt.LeftButton, pos=main.menuBar().actionGeometry(action).center())
        assert invoked == [True]
        dialog.events.put(("progress", dict(percent=40, speed="2MB/s", eta="0:00:30")))
        dialog.poll()
        assert "2MB/s" in win.statusBar().currentMessage()
        win.open_transfer()
        idle = win.transfer_dialogs[-1]
        idle.reject()
        assert win.transfer_dialogs == [dialog]
        win.close()
        assert cancelled == [True] and not win.transfer_dialogs and source.closed
    finally:
        win.close()
        main.close()
        main.deleteLater()
        qapp.sendPostedEvents(None, QEvent.DeferredDelete)


@pytest.mark.gui
def test_upload_scan_and_stale_lists(qapp, tmp_path):
    from transfer_panel import TransferDialog
    (tmp_path / "small").write_text("small")
    with (tmp_path / "large").open("wb") as stream:
        stream.truncate(200_000_001)
    callbacks = []
    source = SimpleNamespace(client=SimpleNamespace(host="fixture"), submit=lambda m, a, cb: callbacks.append(cb))
    dialog = TransferDialog(source, "/server", str(tmp_path), ["small", "large"])
    try:
        dialog.load()
        callbacks[0](dict(files=[], skipped=[]), None)
        assert not dialog.rows
        callbacks[1]({}, None)
        deadline = time.monotonic() + 3
        while not dialog.rows and time.monotonic() < deadline:
            dialog.poll()
            time.sleep(.01)
        assert [row["name"] for row in dialog.selected()] == ["small"]
        dialog.reject()
        callbacks[1](dict(files=[], skipped=[]), None)
        assert len(dialog.rows) == 2
    finally:
        dialog.reject()
