"""名称问题在手动检查和运行前报告，且没有强制运行入口。"""

import pytest

QtWidgets = pytest.importorskip("PySide6.QtWidgets")

import grasp_bg as bg
from test_gui import no_dialogs, window
from test_name_limits import LONG
from test_summary import job

pytestmark = pytest.mark.gui


@pytest.mark.parametrize("action", ["run_pipeline", "run_from_selected"])
def test_name_issues_block_without_run_anyway(qapp, tmp_path, monkeypatch, no_dialogs, action):
    w = window(tmp_path)
    bad = job("jj2lsj")
    bad["vals"]["state"] = LONG
    w.calc.pipeline = [bad]
    monkeypatch.setattr(bg, "launch", lambda *a, **kw: pytest.fail("names must block before launch"))
    getattr(w.calc, action)()
    assert any(kind == "warning" and LONG in message[1] and "23" in message[1]
               for kind, message in no_dialogs)
    assert not any(kind == "question" for kind, _ in no_dialogs)
    assert w.close()


def test_manual_check_combines_names_and_missing_files(qapp, tmp_path, no_dialogs):
    w = window(tmp_path)
    bad = job("jj2lsj")
    bad["vals"]["state"] = LONG
    w.calc.pipeline = [bad]
    w.calc.check_inputs()
    assert len(no_dialogs) == 1
    kind, (_, text) = no_dialogs[0]
    assert kind == "information" and LONG in text and "缺少" in text
    assert "state" in text and "23" in text
    assert w.close()


def test_current_form_check_does_not_generate_invalid_input(qapp, tmp_path, no_dialogs):
    w = window(tmp_path)
    form = next(f for f in w.calc.forms if f.step.key == "jj2lsj")
    form.set_values(dict(state=LONG))
    w.calc.nav.setCurrentWidget(form)
    w.calc.check_inputs()
    assert len(no_dialogs) == 1 and LONG in no_dialogs[0][1][1] and "state" in no_dialogs[0][1][1]
    assert w.close()


def test_appended_subset_reports_absolute_step(qapp, tmp_path, monkeypatch, no_dialogs):
    w = window(tmp_path)
    bad = job("jj2lsj")
    bad["vals"]["state"] = LONG
    w.calc.pipeline = [job("rnucleus"), bad]
    monkeypatch.setattr(bg, "launch", lambda *a, **kw: pytest.fail("appended step must be checked"))
    w.calc.start(w.calc.pipeline[1:])
    assert any("第 2 步" in message[1] and LONG in message[1] for _, message in no_dialogs)
    assert not any(kind == "question" for kind, _ in no_dialogs)
    assert w.close()
