"""GUI 的 MCP 开关和外部记录冲突：默认载入，覆盖需要确认。"""

import pytest

pytestmark = pytest.mark.gui
QtWidgets = pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtCore import QEvent           # noqa: E402

import calc_panel as panel                 # noqa: E402
import grasp_bg as bg                      # noqa: E402
import grasp_config as config              # noqa: E402
import grasp_core as core                  # noqa: E402
import grasp_service as service            # noqa: E402
import grasp_session as sessions           # noqa: E402
from test_gui import no_dialogs, spin, window  # noqa: E402, F401
from test_mcp import document, settings     # noqa: E402, F401


def installed(tmp_path):
    root = tmp_path / "calc"
    config.install(document(), str(root))
    return root


def updated():
    cfg = document()
    cfg["jobs"][0]["vals"]["z"] = "6"
    cfg["jobs"].append(dict(step="rsave", vals=config.STEPS["rsave"].defaults()))
    return cfg


def test_switch_is_off_and_gui_can_toggle(qapp, tmp_path, settings, no_dialogs):
    root = installed(tmp_path)
    w = window(root)
    assert not w.mcp_action.isChecked() and not core.load_config().get("mcp_enabled", False)
    w.mcp_action.setChecked(True)
    assert core.load_config()["mcp_enabled"]
    assert len(no_dialogs) == 1 and no_dialogs[0][0] == "exec"
    assert all(name in no_dialogs[0][1] for name in ("claude", "codex", "gemini", "--root"))
    no_dialogs.clear()
    w.mcp_action.setChecked(False)
    assert not core.load_config()["mcp_enabled"]
    assert w.close() and not no_dialogs


@pytest.mark.parametrize("choice,confirm,count,z", [("load", False, 2, "6"), ("keep", True, 1, "5")])
def test_external_record_load_or_confirmed_keep(qapp, tmp_path, settings, no_dialogs, monkeypatch,
                                               choice, confirm, count, z):
    root = installed(tmp_path)
    w = window(root)
    old_revision = sessions.read(root)[1]
    local = document()
    local["jobs"][0]["vals"]["z"] = "5"
    w.calc.apply(config.session(local))
    svc = service.Service(tmp_path)
    svc.update_workdir("calc", old_revision, updated())
    shown = []

    def choose(d):
        shown.append(True)
        d.choose(choice)
        return d.result()

    monkeypatch.setattr(panel.SessionChangedDialog, "exec", choose)
    asked = []
    monkeypatch.setattr(QtWidgets.QMessageBox, "question",
                        lambda *a, **k: asked.append(a[2]) or (QtWidgets.QMessageBox.Yes if confirm else
                                                            QtWidgets.QMessageBox.No))
    assert w.calc.save_session()
    assert shown == [True]
    assert len(asked) == (1 if choice == "keep" else 0)
    assert len(w.calc.pipeline) == count and w.calc.pipeline[0]["vals"]["z"] == z
    saved = core.load_session(root)["pipeline"]
    assert len(saved) == count and saved[0]["vals"]["z"] == z
    assert bg.status(root) is None
    assert w.close() and not no_dialogs


def test_keep_requires_confirmation_and_gui_changes_conflict(qapp, tmp_path, settings, no_dialogs, monkeypatch):
    root = installed(tmp_path)
    w = window(root)
    original = sessions.read(root)[1]
    svc = service.Service(tmp_path)
    svc.update_workdir("calc", original, updated())
    monkeypatch.setattr(panel.SessionChangedDialog, "exec",
                        lambda d: d.choose("keep") or d.result())
    assert not w.calc.save_session()
    assert len(core.load_session(root)["pipeline"]) == 2
    assert any(x[0] == "question" for x in no_dialogs)
    monkeypatch.setattr(panel.SessionChangedDialog, "exec",
                        lambda d: d.choose("load") or d.result())
    assert w.calc.save_session()
    revision = sessions.read(root)[1]
    w.calc.pipeline.pop()
    assert w.calc.save_session()
    with pytest.raises(sessions.Conflict):
        svc.update_workdir("calc", revision, updated())
    assert w.close()


def test_focus_loads_external_additions_without_running(qapp, tmp_path, settings, no_dialogs, monkeypatch):
    root = installed(tmp_path)
    w = window(root)
    w.show()
    qapp.processEvents()
    monkeypatch.setattr(panel.SessionChangedDialog, "exec",
                        lambda d: d.choose("load") or d.result())
    service.Service(tmp_path).update_workdir("calc", sessions.read(root)[1], updated())
    w.activateWindow()
    qapp.processEvents()
    QtWidgets.QApplication.sendEvent(w, QEvent(QEvent.ActivationChange))
    assert len(w.calc.pipeline) == 2 and not w.calc.busy and bg.status(root) is None
    assert w.close() and not no_dialogs


def test_external_load_does_not_run_old_jobs(qapp, tmp_path, settings, no_dialogs, monkeypatch):
    root = installed(tmp_path)
    w = window(root)
    old_jobs = list(w.calc.pipeline)
    service.Service(tmp_path).update_workdir("calc", sessions.read(root)[1], updated())
    monkeypatch.setattr(panel.SessionChangedDialog, "exec",
                        lambda d: d.choose("load") or d.result())
    monkeypatch.setattr(bg, "launch", lambda *a, **k: pytest.fail("loading external steps must not run jobs"))
    w.calc.start(old_jobs)
    assert len(w.calc.pipeline) == 2 and not w.calc.busy
    assert w.close() and not no_dialogs


def test_write_failure_does_not_trap_readonly_viewer(qapp, tmp_path, settings, no_dialogs, monkeypatch):
    root = installed(tmp_path)
    w = window(root)
    original = (root / core.SESSION_FILE).read_bytes()

    def fail(*args, **kwargs):
        raise OSError("read-only directory")

    monkeypatch.setattr(sessions, "write", fail)
    assert w.close()
    assert no_dialogs and "read-only" in no_dialogs[0][1][1]
    assert (root / core.SESSION_FILE).read_bytes() == original


@pytest.mark.parametrize("enabled,seen", [(True, False), (True, True), (False, False)])
def test_completed_source_hint_survives_log_loading(qapp, tmp_path, settings, no_dialogs, enabled, seen):
    import socket

    root = installed(tmp_path)
    core.save_config(mcp_enabled=enabled)
    bg.write_state(root, dict(pid=0, host=socket.gethostname(), start="test", end="test", titles=[],
                              step=0, status="ok", started_by="mcp", seen=seen))
    (root / bg.RUN_LOG).write_text("calculation output\n")
    w = window(root)
    w.calc.poll()
    assert ("MCP agent" in w.calc.out.toPlainText()) is enabled
    assert len(w.calc.pipeline) == 1
    w.mcp_action.setChecked(False)
    w.calc.poll()
    assert "MCP agent" not in w.calc.out.toPlainText()
    assert w.close() and not no_dialogs
