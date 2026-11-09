"""服务器类型保存及 Slurm 提交/状态界面，不建立真实 SSH 连接。"""

from types import SimpleNamespace

import pytest

import grasp_config as config
import grasp_core as core

pytestmark = pytest.mark.gui


def test_host_classification_saved_without_losing_other_preferences(qapp, tmp_path, monkeypatch):
    from PySide6.QtCore import QEvent, QObject
    from PySide6.QtWidgets import QDialog
    from remote_panel import ConnectDialog

    monkeypatch.setattr(core, "CONFIG_FILE", str(tmp_path / "settings.json"))
    core.save_config(remote_hosts={"cluster": dict(server_type="slurm", rsync="/bin/rsync", directory="/calc"),
                                   "desktop": dict(directory="/work")})
    class Source(QObject):
        client = SimpleNamespace(host="cluster")
        server = dict(executable="/bin/server", server_types=["workstation", "slurm"])
        def close(self):
            pass
    dialog = ConnectDialog()
    try:
        dialog.host.setCurrentText("desktop")
        assert dialog.server_type.currentData() == ""
        dialog.host.setCurrentText("cluster")
        assert dialog.server_type.currentData() == "slurm"
        dialog.source, dialog.directory = Source(dialog), "/calc"
        dialog.accept()
        assert dialog.result() == QDialog.Accepted and dialog.source.server_type == "slurm"
        profile = core.load_config()["remote_hosts"]["cluster"]
        assert profile["server_type"] == "slurm" and profile["rsync"] == "/bin/rsync"
        dialog.host.setCurrentText("desktop")
        dialog.server_type.setCurrentIndex(dialog.server_type.findData("workstation"))
        dialog.setResult(QDialog.Rejected)
        dialog.accept()
        assert dialog.result() == QDialog.Rejected and "重新连接" in dialog.status.text()
        dialog.host.setCurrentText("cluster")
        del dialog.source.server["server_types"]
        dialog.setResult(QDialog.Rejected)
        dialog.accept()
        assert dialog.result() == QDialog.Rejected and "Slurm" in dialog.status.text()
    finally:
        dialog.reject()
        dialog.deleteLater()
        qapp.sendPostedEvents(None, QEvent.DeferredDelete)


def test_clear_unknown_job_defaults_no_and_sends_revision(qapp, tmp_path, monkeypatch):
    from PySide6.QtCore import QEvent, QObject, Signal
    from PySide6.QtWidgets import QMessageBox
    from remote_panel import RemoteCalculation

    monkeypatch.setattr(core, "CONFIG_FILE", str(tmp_path / "settings.json"))
    class Source(QObject):
        push = Signal(object)
        server_type = "slurm"
        client = SimpleNamespace(host="cluster")
        def __init__(self):
            super().__init__()
            self.calls = []
        def submit(self, method, args, callback):
            self.calls.append((method, args))
            if method == "pipeline":
                callback(dict(jobs=[], revision="pipeline"), None)
            elif method == "watch":
                callback(dict(text="", state=dict(backend="slurm", status="unknown", active=True,
                              can_cancel=False, can_clear=True, record_revision="record")), None)
    source = Source()
    panel = RemoteCalculation(source, "/calc")
    source.setParent(panel)
    try:
        assert not panel.clear_record.isHidden() and not panel.run_button.isEnabled()
        def no(*args):
            assert args[-1] == QMessageBox.No
            return QMessageBox.No
        monkeypatch.setattr(QMessageBox, "question", no)
        panel.clear_slurm_record()
        assert not any(method == "clear_slurm_record" for method, _ in source.calls)
        monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Yes)
        panel.clear_slurm_record()
        assert source.calls[-1] == ("clear_slurm_record", dict(directory="/calc", revision="record", confirmed=True))
        assert not panel.clear_record.isEnabled()
        panel.show_state(None)
        assert panel.clear_record.isHidden()
    finally:
        panel.close()
        panel.deleteLater()
        qapp.sendPostedEvents(None, QEvent.DeferredDelete)


def test_slurm_submission_confirmation_and_scheduler_button_states(qapp, tmp_path, monkeypatch):
    from PySide6.QtCore import QEvent, QObject, Signal
    from PySide6.QtWidgets import QDialog, QMessageBox
    from calc_panel import SlurmDialog
    from remote_panel import RemoteCalculation

    monkeypatch.setattr(core, "CONFIG_FILE", str(tmp_path / "settings.json"))
    class Source(QObject):
        push = Signal(object)
        server_type = "slurm"
        server = dict(server_types=["workstation", "slurm"])
        client = SimpleNamespace(host="cluster")
        state = None
        def __init__(self):
            super().__init__()
            self.calls = []
        def submit(self, method, args, callback):
            self.calls.append((method, args))
            if method == "pipeline":
                job = config.STEPS["rnucleus"].job(config.STEPS["rnucleus"].defaults())
                callback(dict(jobs=[job], revision="rev"), None)
            elif method == "watch":
                callback(dict(state=self.state, text="log"), None)
            elif method == "run_plan":
                assert args["server_type"] == "slurm"
                callback(dict(revision="rev", custom_commands=[], slurm=dict(core.SLURM_DEFAULTS)), None)
            elif method == "run":
                self.pending = callback
    source = Source()
    panel = RemoteCalculation(source, "/remote/calc")
    monkeypatch.setattr(SlurmDialog, "exec", lambda self: QDialog.Rejected)
    try:
        panel.prepare_run(1)
        assert not any(method == "run" for method, _ in source.calls)
        monkeypatch.setattr(SlurmDialog, "exec", lambda self: QDialog.Accepted)
        def no(*args):
            assert args[-1] == QMessageBox.No
            return QMessageBox.No
        monkeypatch.setattr(QMessageBox, "question", no)
        panel.prepare_run(1)
        assert not any(method == "run" for method, _ in source.calls)
        monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Yes)
        panel.prepare_run(1)
        submitted = [args for method, args in source.calls if method == "run"]
        assert len(submitted) == 1 and submitted[0]["server_type"] == "slurm"
        assert not panel.run_button.isEnabled() and not panel.stop_button.isEnabled()
        source.state = dict(backend="slurm", job_id="42", scheduler_state="PENDING", status="queued",
                            active=True, can_cancel=True)
        source.pending(dict(started=True, job_id="42"), None)
        assert "42" in panel.state.text() and "PENDING" in panel.state.text()
        assert not panel.run_button.isEnabled() and panel.stop_button.isEnabled()
        panel.stop()
        assert source.calls[-1][0] == "stop"
        panel.show_state(dict(source.state, scheduler_state="UNKNOWN", status="unknown", can_cancel=False))
        assert not panel.run_button.isEnabled() and not panel.stop_button.isEnabled()
        panel.show_state(dict(source.state, scheduler_state="COMPLETED", status="ok", active=False, can_cancel=False))
        assert panel.run_button.isEnabled() and not panel.stop_button.isEnabled()
        assert core.load_config()["remote_hosts"]["cluster"]["slurm"]["ntasks"] == "32"
    finally:
        if hasattr(source, "pending"):
            source.pending = None
        panel.close()
        panel.deleteLater()
        source.deleteLater()
        qapp.sendPostedEvents(None, QEvent.DeferredDelete)
