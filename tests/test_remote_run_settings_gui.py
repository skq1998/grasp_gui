"""服务器运行设置的真实控件、异步检查与公共启动入口。"""

from concurrent.futures import Future
from copy import deepcopy
from queue import Queue
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.gui
pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtCore import QEvent, QEventLoop, QObject, QTimer, Signal  # noqa: E402
from PySide6.QtWidgets import QDialog  # noqa: E402

import grasp_core as core  # noqa: E402
import grasp_config as config  # noqa: E402
from remote_panel import (RemoteCalculation, ServerRunDialog, Source as RemoteSource,  # noqa: E402
                          show_server_run_settings)


class Source(QObject):
    push = Signal(object)

    def __init__(self, modern=True, host="fixture"):
        super().__init__()
        self.client = SimpleNamespace(host=host)
        self.server_type = "workstation"
        self.server = dict(capabilities=["run_range_v1"] + (["run_settings_v1"] if modern else []))
        self.calls, self.pending = [], []
        self.defer, self.missing = False, []
        self.settings = dict(bindir="/server/bin", np=256, mpirun="/server/mpirun", mpitmp="server-tmp", nice=False)
        self.jobs = [config.STEPS["rnucleus"].job(config.STEPS["rnucleus"].defaults())]

    def plan(self, args=None):
        return dict(revision="rev", custom_commands=[], logout_kills=False, lingering=False,
                    run_settings=dict(self.settings, **(args or {}).get("run_settings", {})),
                    missing_programs=list(self.missing),
                    checked=dict(issues=[], invalid=[], missing=[], skipped=[]))

    def submit(self, method, args, callback):
        self.calls.append((method, deepcopy(args)))
        if method == "pipeline":
            callback(dict(jobs=self.jobs, revision="rev"), None)
        elif method == "watch":
            callback(dict(text="", state=None), None)
        elif method == "run_plan":
            if self.defer:
                self.pending.append(callback)
            else:
                callback(self.plan(args), None)


@pytest.fixture
def preferences(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "CONFIG_FILE", str(tmp_path / "preferences.json"))
    return tmp_path / "preferences.json"


def dispose(qapp, dialog):
    dialog.close()
    dialog.deleteLater()
    qapp.sendPostedEvents(None, QEvent.DeferredDelete)


@pytest.mark.parametrize("saved", [False, True])
def test_initial_settings_prefer_host_and_never_overwrite_local_values(qapp, preferences, saved):
    source = Source()
    profiles = dict(fixture=dict(run=dict(np=1024, mpitmp="host-tmp")), other=dict(run=dict(np=7))) if saved else {}
    core.save_config(bindir="/desktop/bin", np=1, remote_hosts=profiles)
    original = preferences.read_bytes()
    dialog = ServerRunDialog(None, source, "/server/calc", source.plan(), {})
    try:
        assert dialog.settings.np() == (1024 if saved else 256)
        assert dialog.settings.mpirun() == "/server/mpirun"
        dialog.settings.bindir_edit.setText("/changed")
        dialog.settings.bindir_edit.editingFinished.emit()
        dialog.settings.save()
        dialog.reject()
        assert preferences.read_bytes() == original
    finally:
        dispose(qapp, dialog)


def test_check_reports_missing_programs_and_defaults_to_cancel(qapp, preferences):
    source = Source()
    source.missing = ["rangular_mpi", "/server/mpirun"]
    dialog = ServerRunDialog(None, source, "/server/calc", source.plan(), dict(start=2, end=3))
    try:
        dialog.check_button.click()
        assert "rangular_mpi" in dialog.report.toPlainText()
        assert dialog.cancel.isDefault() and not dialog.ok.isDefault()
        assert source.calls[-1][1]["start"] == 2 and source.calls[-1][1]["end"] == 3
        dialog.ok.click()
        assert dialog.result() == QDialog.Accepted
        assert core.load_config()["remote_hosts"]["@fixture:22"]["run"]["np"] == 256
    finally:
        dispose(qapp, dialog)


def test_dirty_confirmation_rechecks_and_stale_or_closed_answers_are_ignored(qapp, preferences):
    source = Source()
    source.defer = True
    dialog = ServerRunDialog(None, source, "/server/calc", source.plan(), {})
    try:
        dialog.check()
        old = source.pending.pop()
        dialog.settings.np_spin.setValue(2)
        old(source.plan(), None)
        assert dialog.checked_values is None
        dialog.ok.click()
        assert not dialog.ok.isEnabled() and dialog.result() != QDialog.Accepted
        current = source.pending.pop()
        current(source.plan(dict(run_settings=dialog.settings.values())), None)
        assert dialog.result() == QDialog.Accepted
        assert core.load_config()["remote_hosts"]["@fixture:22"]["run"]["np"] == 2
        saved = preferences.read_bytes()
        old(None, ValueError("late reply"))
        assert preferences.read_bytes() == saved
    finally:
        dispose(qapp, dialog)


def test_new_warnings_during_automatic_check_need_another_confirmation(qapp, preferences):
    source = Source()
    dialog = ServerRunDialog(None, source, "/server/calc", source.plan(), {})
    try:
        source.missing = ["rmcdhf_mpi"]
        dialog.settings.np_spin.setValue(2)
        dialog.ok.click()
        assert dialog.result() != QDialog.Accepted and dialog.cancel.isDefault()
        assert not preferences.exists()
        dialog.ok.click()
        assert dialog.result() == QDialog.Accepted
    finally:
        dispose(qapp, dialog)


@pytest.mark.parametrize("modern", [False, True])
@pytest.mark.parametrize("after_load", [False, True])
def test_all_runs_use_the_dialog_and_legacy_requests_remain_unchanged(qapp, preferences, modern, after_load):
    source = Source(modern)
    dialogs = []
    def confirm():
        dialog = qapp.activeModalWidget()
        assert isinstance(dialog, ServerRunDialog)
        dialogs.append(dialog)
        if modern:
            dialog.settings.np_spin.setValue(2)
        else:
            assert not dialog.settings.isEnabled()
            assert "只读" in dialog.report.toPlainText()
        dialog.ok.click()
    QTimer.singleShot(0, confirm)
    panel = RemoteCalculation(source, "/server/calc", run_after_load=after_load)
    source.setParent(panel)
    try:
        if not after_load:
            panel.prepare_run(1)
        requests = [args for method, args in source.calls if method == "run"]
        assert len(dialogs) == len(requests) == 1
        assert ("run_settings" in requests[0]) is modern
        if modern:
            assert requests[0]["run_settings"]["np"] == 2
        else:
            assert not preferences.exists()
            assert all("run_settings" not in args for _, args in source.calls)
    finally:
        dispose(qapp, panel)


def test_standalone_settings_can_be_saved_without_starting(qapp, preferences):
    source = Source()
    panel = RemoteCalculation(source, "/server/calc")
    source.setParent(panel)
    def save():
        dialog = qapp.activeModalWidget()
        assert dialog.run is False
        dialog.settings.mpitmp_edit.setText("standalone")
        dialog.ok.click()
    QTimer.singleShot(0, save)
    try:
        panel.edit_run_settings()
        assert core.load_config()["remote_hosts"]["@fixture:22"]["run"]["mpitmp"] == "standalone"
        assert not any(method == "run" for method, _ in source.calls)
    finally:
        dispose(qapp, panel)


class AsyncClient:
    host = "fixture"

    def __init__(self):
        self.events = Queue()
        self.fixture = Source()
        self.calls = []

    def request(self, method, args, cached=False):
        self.calls.append((method, args))
        future = Future()
        future.request_id = len(self.calls)
        if method == "run_plan":
            answer = self.fixture.plan(args)
        elif method == "pipeline":
            answer = dict(jobs=self.fixture.jobs, revision="rev")
        elif method == "hold":
            return future
        else:
            answer = dict(text="", state=None)
        QTimer.singleShot(0, lambda: future.set_result(answer) if not future.cancelled() else None)
        return future

    def close(self, wait=False):
        pass


@pytest.mark.parametrize("run", [False, True])
def test_settings_entry_points_receive_async_answers_during_modal_dialog(qapp, preferences, run):
    client = AsyncClient()
    source = RemoteSource(client)
    source.server = client.fixture.server
    panel = RemoteCalculation(source, "/server/calc") if run else None
    if panel is not None:
        panel.jobs, panel.revision = client.fixture.jobs, "rev"
    loop, monitor = QEventLoop(), QTimer()
    result = {}

    def accept_checked():
        dialog = qapp.activeModalWidget()
        if not isinstance(dialog, ServerRunDialog) or dialog.checked_values is None:
            return
        if dialog.settings.bindir() != "/new/bin":
            dialog.settings.bindir_edit.setText("/new/bin")
            dialog.check()
            return
        result["checked"] = dialog.plan["run_settings"]["bindir"]
        dialog.ok.click()
        if not run:
            loop.quit()

    def timeout():
        dialog = qapp.activeModalWidget()
        if isinstance(dialog, ServerRunDialog):
            dialog.reject()
        loop.quit()

    def observe():
        accept_checked()
        if run and any(method == "run" for method, _ in client.calls):
            loop.quit()

    try:
        monitor.timeout.connect(observe)
        monitor.start(10)
        deadline = QTimer()
        deadline.setSingleShot(True)
        deadline.timeout.connect(timeout)
        deadline.start(1000)
        if run:
            panel.prepare_run(1)
        else:
            show_server_run_settings(None, source, "/server/calc")
        (getattr(loop, "exec", None) or loop.exec_)()
        assert result == {"checked": "/new/bin"}
        assert core.load_config()["remote_hosts"]["@fixture:22"]["run"]["bindir"] == "/new/bin"
        if run:
            assert [args["run_settings"]["bindir"] for method, args in client.calls if method == "run"] == ["/new/bin"]
    finally:
        monitor.stop()
        source.close()
        if panel is not None:
            dispose(qapp, panel)


def test_modal_callback_keeps_queued_responses_progress_and_push_events_moving(qapp):
    client = AsyncClient()
    source = RemoteSource(client)
    received = []
    source.push.connect(lambda event: received.append(event["event"]))
    source.notice.connect(received.append)
    outer_loop = QEventLoop()
    outer_deadline = QTimer()
    outer_deadline.setSingleShot(True)
    outer_deadline.timeout.connect(outer_loop.quit)

    def modal_callback(*_):
        dialog = QDialog()
        source.submit("watch", {}, lambda *_: dialog.accept())
        client.events.put(dict(event="progress", id=progress_id, data="progress"))
        client.events.put(dict(event="log"))
        client.events.put(dict(event="state"))
        client.events.put(dict(event="notice", text="notice"))
        deadline = QTimer(dialog)
        deadline.setSingleShot(True)
        deadline.timeout.connect(dialog.reject)
        deadline.start(1000)
        received.append(dialog.exec())
        deadline.stop()
        outer_loop.quit()

    try:
        source.submit("watch", {}, modal_callback)
        source.submit("watch", {}, lambda *_: received.append("response"))
        progress_id = source.submit("hold", {}, lambda *_: None, received.append)
        outer_deadline.start(2000)
        (getattr(outer_loop, "exec", None) or outer_loop.exec_)()
        assert received == ["response", "progress", "log", "state", "notice", QDialog.Accepted]
    finally:
        outer_deadline.stop()
        source.close()


def test_cancel_after_poll_queues_response_and_progress(qapp):
    client = AsyncClient()
    source = RemoteSource(client)
    source.timer.stop()
    received = []
    try:
        ident = source.submit("watch", {}, lambda *_: received.append("response"), received.append)
        qapp.processEvents()
        assert source.waiting[ident][0].done()
        client.events.put(dict(event="progress", id=ident, data="progress"))
        source.poll()
        source.poll()
        source.cancel(ident)
        qapp.processEvents()
        assert received == [] and not source.waiting
    finally:
        source.close()
