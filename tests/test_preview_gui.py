"""输入预览跟随当前行及运行设置；自动更新不能抢走流程标签。"""

import pytest

pytestmark = pytest.mark.gui
QtWidgets = pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

import grasp_core as core  # noqa: E402
from test_gui import no_dialogs, window  # noqa: E402, F401
from test_summary import job  # noqa: E402


@pytest.fixture
def app_window(qapp, tmp_path, no_dialogs, monkeypatch):
    monkeypatch.setattr(core, "CONFIG_FILE", str(tmp_path / "preferences.json"))
    w = window(tmp_path)
    # 不依赖测试机器实际核数，固定串行起点并允许验证 MPI 切换。
    w.run.np_spin.setMaximum(max(2, w.run.np_spin.maximum()))
    w.run.np_spin.setValue(1)
    w.run.mpirun_edit.setText("mpirun")
    yield w
    assert w.close()
    assert not no_dialogs


def assert_preview(calc, expected):
    assert calc.prev.toPlainText() == core.describe(expected, calc.run.mpi_prefix()) + "\n"


def form(calc, key):
    page = next(f for f in calc.forms if f.step.key == key)
    calc.nav.setCurrentWidget(page)
    return page


def test_update_selected_immediately_refreshes_and_opens_preview(app_window):
    calc = app_window.calc
    calc.set_preview_column(False)
    page = form(calc, "rmcdhf")
    page.set_values(dict(nscf="100", save="odd2"))
    calc.add_to_pipeline()
    calc.plist.setCurrentRow(0)
    calc.show_job()
    assert "\n100\n" in calc.prev.toPlainText()
    calc.btabs.setCurrentIndex(0)
    page.set_values(dict(nscf="250"))
    # 修改表单本身仍不会更新已有预览。
    assert "\n100\n" in calc.prev.toPlainText()
    calc.update_job()
    assert calc.pipeline[0]["vals"]["nscf"] == "250"
    assert "\n250\n" in calc.prev.toPlainText()
    assert_preview(calc, calc.pipeline[0])
    assert calc.btabs.currentWidget() is calc.prev


@pytest.mark.parametrize("width,wide", [(1100, False), (1900, True)])
def test_mouse_keyboard_and_programmatic_row_changes_keep_pipeline_tab(qapp, app_window, width, wide):
    w, calc = app_window, app_window.calc
    calc.pipeline = [job("rnucleus"), job("rmcdhf", nscf="250"), job("rangular", csf="next.c")]
    calc.refresh_list()
    w.resize(width, 950)
    w.show()
    qapp.processEvents()
    assert calc.preview_column is wide
    calc.btabs.setCurrentIndex(0)
    pipeline_tab = calc.btabs.currentWidget()
    calc.plist.setCurrentRow(0)
    assert_preview(calc, calc.pipeline[0])
    # 使用真实鼠标和方向键事件，不只测试直接调用刷新方法。
    rect = calc.plist.visualItemRect(calc.plist.item(1))
    QTest.mouseClick(calc.plist.viewport(), Qt.LeftButton, Qt.NoModifier, rect.center())
    assert calc.selected() == 1
    assert_preview(calc, calc.pipeline[1])
    assert calc.btabs.currentWidget() is pipeline_tab
    QTest.keyClick(calc.plist, Qt.Key_Down)
    assert calc.selected() == 2
    assert_preview(calc, calc.pipeline[2])
    # 连续多选时显示当前行，而不是选区首行。
    calc.plist.item(1).setSelected(True)
    assert_preview(calc, calc.pipeline[2])
    assert calc.btabs.currentWidget() is pipeline_tab


def test_move_remove_and_clear_follow_new_current_row(app_window):
    calc = app_window.calc
    calc.pipeline = [job("rnucleus"), job("rmcdhf"), job("rangular")]
    calc.refresh_list()
    calc.plist.setCurrentRow(1)
    calc.move(-1)
    assert calc.selected() == 0 and calc.pipeline[0]["step"] == "rmcdhf"
    assert_preview(calc, calc.pipeline[0])
    calc.move(1)
    assert calc.selected() == 1 and calc.pipeline[1]["step"] == "rmcdhf"
    assert_preview(calc, calc.pipeline[1])
    calc.remove_job()
    if calc.selected() is None:
        assert not calc.prev.toPlainText()
    else:
        assert_preview(calc, calc.pipeline[calc.selected()])
    calc.plist.setCurrentRow(0)
    calc.clear_pipeline()
    assert not calc.prev.toPlainText()
    # 没有当前流程行不影响独立的表单预览。
    calc.preview()
    text = calc.prev.toPlainText()
    assert text
    calc.clear_pipeline()
    assert calc.prev.toPlainText() == text


@pytest.mark.parametrize("existing_row", [False, True])
def test_add_to_pipeline_preserves_form_preview_and_mpi_source(qapp, app_window, existing_row):
    calc = app_window.calc
    if existing_row:
        calc.pipeline = [job("rangular", csf="earlier.c")]
        calc.refresh_list()
        calc.plist.setCurrentRow(0)
    app_window.resize(1100, 950)
    app_window.show()
    qapp.processEvents()
    page = form(calc, "rnucleus")
    calc.preview()
    text = calc.prev.toPlainText()
    assert "rnucleus" in text
    add = next(b for b in calc.findChildren(QtWidgets.QPushButton) if b.text() == core.T("加入流程 ↓"))
    QTest.mouseClick(add, Qt.LeftButton)
    assert calc.pipeline[-1]["step"] == "rnucleus"
    assert calc.prev.toPlainText() == text
    # 加入列表后仍保留表单来源，切换运行设置不能把预览清掉。
    calc.run.np_spin.setValue(2)
    assert_preview(calc, page.job())
    calc.plist.setCurrentRow(0)
    assert_preview(calc, calc.pipeline[0])


@pytest.mark.parametrize("action", ["remove", "clear"])
def test_list_changes_preserve_form_preview_with_an_existing_current_row(app_window, action):
    calc = app_window.calc
    calc.pipeline = [job("rangular"), job("rmcdhf")]
    calc.refresh_list()
    calc.plist.setCurrentRow(1)
    page = form(calc, "rmcdhf")
    calc.preview()
    text = calc.prev.toPlainText()
    if action == "remove":
        calc.remove_job()
    else:
        calc.clear_pipeline()
    assert calc.prev.toPlainText() == text
    calc.run.np_spin.setValue(2)
    assert_preview(calc, page.job())


@pytest.mark.parametrize("source", ["pipeline", "form"])
def test_mpi_settings_refresh_the_displayed_source_without_switching_tabs(app_window, source):
    calc = app_window.calc
    page = form(calc, "rmcdhf")
    page.set_values(dict(nscf="250"))
    calc.pipeline = [job("rangular", csf="pipeline.c")]
    calc.refresh_list()
    calc.plist.setCurrentRow(0)
    if source == "form":
        calc.preview()
        expected = page.job()
        # 来源应仍是 rmcdhf 表单，不能因切到其他程序页而改成那一页的预览。
        form(calc, "rnucleus")
    else:
        expected = calc.pipeline[0]
    calc.btabs.setCurrentIndex(0)
    pipeline_tab = calc.btabs.currentWidget()
    assert "_mpi" not in calc.prev.toPlainText()
    for count in (2, 1, 2):
        calc.run.np_spin.setValue(count)
        assert_preview(calc, expected)
        assert calc.btabs.currentWidget() is pipeline_tab
    calc.run.mpirun_edit.setText("custom-mpirun --bind-to core")
    assert "custom-mpirun --bind-to core -np 2 " in calc.prev.toPlainText()
    assert_preview(calc, expected)
    assert calc.btabs.currentWidget() is pipeline_tab


def test_invalid_form_on_settings_change_keeps_previous_text_and_stays_silent(app_window):
    calc = app_window.calc
    page = form(calc, "rmcdhf")
    page.set_values(dict(modify_nsic=True, nsic="3"))
    calc.preview()
    text = calc.prev.toPlainText()
    page.set_values(dict(nsic="-1"))
    calc.run.np_spin.setValue(2)
    calc.run.mpirun_edit.setText("other-mpirun")
    assert calc.prev.toPlainText() == text
    page.set_values(dict(nsic="5"))
    calc.run.np_spin.setValue(1)
    assert_preview(calc, page.job())


def test_explicit_preview_show_and_double_click_still_open_preview(qapp, app_window):
    w, calc = app_window, app_window.calc
    calc.pipeline = [job("rangular", csf="selected.c")]
    calc.refresh_list()
    page = form(calc, "rmcdhf")
    w.resize(1100, 950)
    w.show()
    qapp.processEvents()
    calc.plist.setCurrentRow(0)
    calc.preview()
    assert_preview(calc, page.job())
    assert calc.btabs.currentWidget() is calc.prev
    calc.btabs.setCurrentIndex(0)
    calc.show_job()
    assert_preview(calc, calc.pipeline[0])
    assert calc.btabs.currentWidget() is calc.prev
    calc.btabs.setCurrentIndex(0)
    qapp.processEvents()
    rect = calc.plist.visualItemRect(calc.plist.item(0))
    QTest.mouseClick(calc.plist.viewport(), Qt.LeftButton, Qt.NoModifier, rect.center())
    QTest.mouseDClick(calc.plist.viewport(), Qt.LeftButton, Qt.NoModifier, rect.center())
    assert_preview(calc, calc.pipeline[0])
    assert calc.btabs.currentWidget() is calc.prev


@pytest.mark.parametrize("source", ["pipeline", "form"])
def test_language_rebuild_keeps_preview_source_and_reconnects_settings(qapp, app_window, source):
    w, calc = app_window, app_window.calc
    calc.pipeline = [job("rangular", csf="pipeline.c")]
    calc.refresh_list()
    calc.plist.setCurrentRow(0)
    page = form(calc, "rmcdhf")
    if source == "form":
        calc.preview()
    w.set_language("en")
    qapp.processEvents()
    expected = calc.pipeline[0] if source == "pipeline" else next(
        f for f in calc.forms if f.step.key == "rmcdhf").job()
    assert_preview(calc, expected)
    w.run.np_spin.setValue(2)
    assert_preview(calc, expected)
    # 单独重建计算页时使用同一组运行设置，仍能正确刷新。
    calc.rebuild()
    w.run.mpirun_edit.setText("rebuilt-mpirun")
    assert_preview(calc, expected)


def test_run_current_preview_uses_form_on_later_settings_changes(app_window, monkeypatch):
    calc = app_window.calc
    calc.pipeline = [job("rangular")]
    calc.refresh_list()
    calc.plist.setCurrentRow(0)
    page = form(calc, "rmcdhf")
    launched = []
    monkeypatch.setattr(calc, "start", lambda jobs: launched.extend(jobs))
    calc.run_current()
    assert launched == [page.job()]
    calc.run.np_spin.setValue(2)
    assert_preview(calc, page.job())
