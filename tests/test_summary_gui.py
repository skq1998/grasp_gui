"""摘要勾选、异步预览和运行前文件提示。"""

import copy

import pytest

pytestmark = pytest.mark.gui
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

import calc_panel as panel                 # noqa: E402
import grasp_bg as bg                      # noqa: E402
import grasp_config as config              # noqa: E402
import grasp_core as core                  # noqa: E402
from summary_panel import SummaryDialog    # noqa: E402
from test_gui import no_dialogs, spin, window  # noqa: E402, F401
from test_summary import job               # noqa: E402


@pytest.mark.parametrize("language", ["zh", "en"])
def test_background_preview_sections_and_copy(qapp, tmp_path, language):
    """结果摘要为主：完整配置默认不选，勾选后才附带。"""
    core.set_lang(language)
    jobs = [job("rnucleus")]
    cfg = config.export(jobs, dict(title="summary preview"))
    dialog = SummaryDialog(None, str(tmp_path), cfg, jobs)
    dialog.show()
    spin(qapp, lambda: dialog.copy_button.isEnabled())
    assert not dialog.sections["configuration"].isChecked()
    assert "summary preview" not in dialog.content
    dialog.sections["configuration"].setChecked(True)
    assert "summary preview" in dialog.content
    for check in dialog.sections.values():
        check.setChecked(False)
    assert not dialog.copy_button.isEnabled() and not dialog.save_button.isEnabled()
    dialog.sections["run_status"].setChecked(True)
    assert dialog.copy_button.isEnabled()
    content = dialog.content
    dialog.copy()
    assert qapp.clipboard().text() == content
    assert dialog.worker.closed


def test_summary_saved_to_file(qapp, tmp_path, monkeypatch):
    jobs = [job("rnucleus")]
    dialog = SummaryDialog(None, str(tmp_path), None, jobs)
    dialog.show()
    spin(qapp, lambda: dialog.save_button.isEnabled())
    assert not dialog.sections["configuration"].isEnabled()      # 没有配置可附带
    target = tmp_path / "out.md"
    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName", lambda *a, **k: (str(target), ""))
    dialog.save()
    assert target.read_text(encoding="utf-8") == dialog.content
    assert dialog.saved_path == str(target)


def test_summary_on_levels_tab_uses_calculation_pipeline(qapp, tmp_path, monkeypatch, no_dialogs):
    """入口在能级图页；当前目录的流程取自计算页，计算页的导出菜单不再有摘要。"""
    w = window(tmp_path)
    w.calc.pipeline = [job("rnucleus")]
    w.show_tab("levels")
    qapp.clipboard().setText("keep clipboard")
    seen = {}

    def cancel(d):
        seen.update(jobs=d.jobs, cfg=d.cfg)
        d.reject()
        return d.result()

    monkeypatch.setattr(SummaryDialog, "exec", cancel)
    w.panel("levels").open_summary()
    assert [j["step"] for j in seen["jobs"]] == ["rnucleus"] and seen["cfg"]["jobs"]
    assert qapp.clipboard().text() == "keep clipboard"
    export = [b for b in w.calc.findChildren(QtWidgets.QPushButton) if b.menu() and b.text() == core.T("导出")]
    texts = [a.text() for b in export for a in b.menu().actions()]
    assert not any(core.T("结果摘要") in t for t in texts)
    assert w.close() and not no_dialogs


@pytest.mark.parametrize("confirm", [False, True])
def test_missing_input_prompt_and_run_anyway(qapp, tmp_path, monkeypatch, no_dialogs, confirm):
    w = window(tmp_path)
    w.calc.pipeline = [job("rangular", csf="missing.c")]
    requested = []
    monkeypatch.setattr(QtWidgets.QMessageBox, "question",
                        lambda *args: requested.append(args[2]) or
                        (QtWidgets.QMessageBox.Yes if confirm else QtWidgets.QMessageBox.No))
    launched = []

    def launch(*args, **kwargs):
        launched.append(True)
        raise RuntimeError("test launch reached")

    monkeypatch.setattr(bg, "launch", launch)
    w.calc.run_from_selected()
    assert len(requested) == 1 and "missing.c" in requested[0]
    assert bool(launched) is confirm
    assert w.close()


def test_appended_steps_are_checked_before_run(qapp, tmp_path, monkeypatch, no_dialogs):
    original = config.export([job("rnucleus")], dict(title="test"))
    config.install(original, str(tmp_path / "calc"))
    w = window(tmp_path / "calc")
    updated = copy.deepcopy(original)
    updated["jobs"].append(dict(step="rangular", vals=dict(config.STEPS["rangular"].defaults(), csf="next.c")))

    def append(d):
        d.accept_update(True)
        return d.result()

    monkeypatch.setattr(panel.UpdateDialog, "exec", append)
    w.calc.update_workdir(updated)
    assert len(w.calc.pipeline) == 2 and not w.calc.busy
    assert any(kind == "question" and "next.c" in text[1] for kind, text in no_dialogs)
    assert w.close()


def test_file_check_read_error_is_visible(qapp, tmp_path, monkeypatch, no_dialogs):
    import grasp_summary as summary

    w = window(tmp_path)
    w.calc.pipeline = [job("rnucleus")]

    def fail(*args, **kwargs):
        raise OSError("cannot inspect directory")

    monkeypatch.setattr(summary, "check_files", fail)
    monkeypatch.setattr(bg, "launch", lambda *args, **kwargs: pytest.fail("failed check must be reported first"))
    w.calc.check_inputs()
    w.calc.run_pipeline()
    assert len(no_dialogs) == 2 and all("cannot inspect directory" in x[1][1] for x in no_dialogs)
    assert w.close()
