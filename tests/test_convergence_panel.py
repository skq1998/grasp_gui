"""面板依流程排序并共享计算、图表和报告中的逐层数据。"""

from pathlib import Path

import pytest

import grasp_converge as converge
import grasp_core as core
from grasp_mix import HARTREE_CM
from test_converge import state
from test_gui import spin, window

pytestmark = pytest.mark.gui


@pytest.fixture
def results(tmp_path, monkeypatch):
    files = ("base.m", "layer_z.m", "layer_a.m")
    layers = {}
    step = core.RMcdhf()
    jobs = []
    for file, offset in zip(files, (0, 2, -2)):
        path = tmp_path / file
        path.touch()
        jobs.append(dict(step.job(dict(step.defaults(), save=path.stem)), group="odd"))
        levels = (state("2p", -8, term="2P"),
                  state("3p", -8 + (30 + offset) / HARTREE_CM, j="3/2", twoj=3, position=2, term="2P"))
        layers[file] = converge.Layer(file, levels, dict(missing=True))
    core.save_config(level_unit=1)
    assert core.save_session(str(tmp_path), {"pipeline": jobs})

    def read(directory, files, *, jobs, cancel):
        assert Path(directory) == tmp_path
        return [layers[file] for file in files]

    monkeypatch.setattr(converge, "read_layers", read)
    return tmp_path, files


def test_layer_order_filter_units_and_reports(qapp, results, monkeypatch):
    from PySide6.QtWidgets import QFileDialog

    from convergence_panel import ConvergencePanel, STATUS_COLORS

    directory, files = results
    panel = ConvergencePanel(str(directory))
    try:
        spin(qapp, lambda: not panel.loading)
        assert [layer["file"] for layer in panel.result["layers"]] == list(files)
        assert panel.group.currentData() == "odd" and panel.threshold.value() == 1
        assert panel.results.rowCount() == 2
        status = panel.results.item(1, panel.results.columnCount() - 1)
        assert status.text() == "未收敛"
        assert status.background().color().name() == STATUS_COLORS["unconverged"]
        assert panel.results.horizontalHeaderItem(2).toolTip() == "无日志"
        assert "纯度" in panel.results.item(1, 2).toolTip()

        panel.only_unconverged.setChecked(True)
        assert panel.results.rowCount() == 1 and panel.results.item(0, 1).text() == "3p"
        panel.selection_only.setChecked(True)
        assert not panel.plot.series
        panel.results.selectRow(0)
        assert len(panel.plot.series) == 1
        report = panel.displayed_result()
        target = directory / "convergence.csv"
        monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_: (str(target), "CSV"))
        panel.copy_button.click()
        panel.csv_button.click()
        assert qapp.clipboard().text() == converge.markdown(report)
        assert target.read_bytes().decode("utf-8-sig") == converge.csv_text(report)

        panel.view.setCurrentIndex(1)
        assert panel.results.rowCount() == 1 and "2P" == panel.results.item(0, 1).text()
        panel.set_unit(0)
        assert "eV" in panel.results.horizontalHeaderItem(3).text()
        assert panel.threshold_cm == 1 and panel.threshold.value() < 1

        panel.files.setCurrentRow(2)
        panel.move_layer(-1)
        spin(qapp, lambda: not panel.loading)
        assert [layer["file"] for layer in panel.result["layers"]] == [files[0], files[2], files[1]]
    finally:
        panel.shutdown()
        panel.deleteLater()


def test_open_standalone_and_choose_layers(qapp, results, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QFileDialog

    from convergence_panel import ConvergencePanel
    from gui_common import PanelWindow

    directory, files = results
    core.set_lang("en")
    win = PanelWindow.open(ConvergencePanel, str(directory))
    panel = win.panel
    try:
        assert win.windowTitle() == "Convergence" and panel.dir_box.isVisible()
        spin(qapp, lambda: not panel.loading)
        panel.files.item(0).setCheckState(Qt.Unchecked)
        spin(qapp, lambda: not panel.loading)
        assert [layer["file"] for layer in panel.result["layers"]] == list(files[1:])
        monkeypatch.setattr(QFileDialog, "getOpenFileNames", lambda *_: ([str(directory / files[0])], ""))
        panel.pick_files()
        spin(qapp, lambda: not panel.loading)
        assert [layer["file"] for layer in panel.result["layers"]] == list(files)
        panel.reference.setCurrentIndex(2)
        spin(qapp, lambda: not panel.loading)
        assert panel.result["reference"] is not None
        assert panel.result["rows"][1]["points"][-1]["excitation_cm"] == 0
    finally:
        assert win.close()


def test_main_window_convergence_shortcut_open_window_and_change_directory(qapp, tmp_path, monkeypatch):
    from PySide6.QtGui import QKeySequence
    from PySide6.QtWidgets import QMenu

    import gui_common as gc

    first, second = tmp_path / "first", tmp_path / "second"
    step = core.RMcdhf()
    for directory in (first, second):
        directory.mkdir()
        (directory / (directory.name + ".m")).touch()
        job = dict(step.job(dict(step.defaults(), save=directory.name)), group="odd")
        assert core.save_session(str(directory), {"pipeline": [job]})
    monkeypatch.setattr(core, "CONFIG_FILE", str(tmp_path / "preferences.json"))
    first_requests = []

    def read(directory, files, *, jobs, cancel):
        if Path(directory) == first:
            first_requests.append(cancel)
            assert cancel.wait(10)
        return [converge.Layer(name, (state("2p", -8),), dict(missing=True)) for name in files]

    monkeypatch.setattr(converge, "read_layers", read)
    w = window(first)
    try:
        w.show()
        assert [w.tabs.tabText(i) for i in range(w.tabs.count())] == ["计算", "径向波函数", "能级图", "收敛"]
        menus = {menu.title(): menu for menu in w.menuBar().findChildren(QMenu)}
        view = next(action for action in menus["视图"].actions() if action.text() == "收敛")
        assert view.shortcut().toString(QKeySequence.PortableText) == "Ctrl+4"
        view.trigger()
        assert w.current_tab() == "convergence"
        panel = w.panel("convergence")
        initial_read = panel.worker.cancel
        spin(qapp, lambda: initial_read in first_requests)
        w.change_workdir(str(second))
        assert initial_read.is_set()
        spin(qapp, lambda: not panel.loading)
        assert [row["file"] for row in panel.result["layers"]] == ["second.m"]
        standalone = next(action for action in menus["窗口"].actions() if action.text() == "在新窗口中打开收敛")
        standalone.trigger()
        opened = gc.PANEL_WINDOWS[-1]
        assert opened.panel.KIND == "convergence" and opened.panel.dir_box.isVisible()
        spin(qapp, lambda: not opened.panel.loading)
        assert [row["file"] for row in opened.panel.result["layers"]] == ["second.m"]
    finally:
        assert w.close()
