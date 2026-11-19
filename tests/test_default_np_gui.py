"""进程数的来源、旧设置保留，以及主窗口和跃迁页的默认值更新。"""

import json

import pytest

pytestmark = pytest.mark.gui
pytest.importorskip("PySide6.QtWidgets")

import grasp_core as core
import gui_common as gc


@pytest.fixture
def mpi_settings(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name in ["rmcdhf", "mpirun", "rangular_mpi", "rmcdhf_mpi", "rci_mpi",
                 "rbiotransform_mpi", "rtransition_mpi"]:
        path = bindir / name
        path.write_text("#!/bin/sh\nexit 99\n")
        path.chmod(0o755)
    monkeypatch.setenv("PATH", str(bindir))
    monkeypatch.setattr(core, "CONFIG_FILE", str(tmp_path / "settings.json"))
    monkeypatch.setattr(core, "default_bindir", lambda: str(bindir))
    monkeypatch.setattr(core, "physical_cpu_count", lambda: 2)
    monkeypatch.setattr(core.os, "cpu_count", lambda: 8)
    return bindir


@pytest.mark.parametrize("lang", ["zh", "en"])
@pytest.mark.parametrize("mpi_available", [True, False])
def test_first_defaults_saved_in_both_views(qapp, tmp_path, mpi_settings, lang, mpi_available):
    import grasp_gui

    if not mpi_available:
        (mpi_settings / "mpirun").unlink()
    expected = 2 if mpi_available else 1
    core.set_lang(lang)
    w = grasp_gui.MainWindow(str(tmp_path), "calc")
    assert w.run.np() == expected
    assert core.load_config()["np"] == expected
    assert core.load_config()["np_user"] is False
    w.show_tab("levels")
    panel = w.panel("levels")
    assert panel.tr_np.value() == expected
    assert core.load_config()["tr_np"] == expected
    assert core.load_config()["tr_np_user"] is False
    assert w.run.np_spin.maximum() == 8
    assert panel.tr_np.maximum() == 32
    assert "GRASP" in panel.tr_np.toolTip()
    w.set_language("en" if lang == "zh" else "zh")
    assert w.run.np() == expected
    assert w.panel("levels").tr_np.value() == expected
    assert w.close()


def test_saved_counts_and_other_settings_are_retained(qapp, tmp_path, mpi_settings, monkeypatch):
    import grasp_gui
    from level_panel import LevelPanel

    core.save_config(np=3, tr_np=12, other="keep")

    def unexpected_default(*args):
        pytest.fail("saved process counts must not be recomputed")

    monkeypatch.setattr(core, "default_mpi_np", unexpected_default)
    assert gc.saved_settings()["np"] == 3
    panel = LevelPanel(str(tmp_path))
    assert panel.tr_np.value() == 12
    assert core.load_config() == dict(np=3, tr_np=12, other="keep")
    panel.shutdown()
    panel.close()
    w = grasp_gui.MainWindow(str(tmp_path), "calc")
    w.run.mpirun_edit.setText("no-launcher")
    w.run.mpirun_edit.editingFinished.emit()
    assert w.run.np() == 3
    w.show_tab("levels")
    assert w.panel("levels").tr_np.value() == 12
    assert w.close()


def test_transition_default_rechecks_when_panel_opens(qapp, tmp_path, mpi_settings):
    from level_panel import LevelPanel

    assert gc.saved_settings()["np"] == 2
    panel = LevelPanel(str(tmp_path))
    assert panel.tr_np.value() == 2
    panel.close()
    (mpi_settings / "mpirun").unlink()
    assert gc.saved_settings()["np"] == 2
    panel = LevelPanel(str(tmp_path))
    assert panel.tr_np.value() == 1
    assert core.load_config()["tr_np_user"] is False
    panel.close()


def test_transition_default_is_independent_of_saved_main_count(qapp, tmp_path, mpi_settings):
    from level_panel import LevelPanel

    core.save_config(np=7)
    panel = LevelPanel(str(tmp_path))
    assert panel.tr_np.value() == 2
    assert core.load_config() == dict(np=7, tr_np=2, tr_np_user=False)
    panel.close()


def test_provisional_counts_follow_bindir_and_launcher(qapp, tmp_path, mpi_settings, monkeypatch):
    import grasp_gui

    no_bin = str(tmp_path / "not-installed")
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(core, "default_bindir", lambda: no_bin)
    core.save_config(mpirun=str(mpi_settings / "mpirun"))
    w = grasp_gui.MainWindow(str(tmp_path), "calc")
    assert w.run.np() == 1
    w.show_tab("levels")
    assert w.panel("levels").tr_np.value() == 1
    w.show_tab("calc")
    notices = []
    w.run.defaultChanged.connect(notices.append)
    w.run.bindir_edit.setText(str(mpi_settings))
    w.run.bindir_edit.editingFinished.emit()
    assert w.run.np() == 2 and core.load_config()["np_user"] is False
    w.run.bindir_edit.editingFinished.emit()
    assert notices == [core.T("MPI 进程数的默认值已改为 {n}。", n=2)]
    assert w.panel("levels").tr_np.value() == 1
    transition_notices = []
    w.panel("levels").message.connect(transition_notices.append)
    w.show_tab("levels")
    assert w.panel("levels").tr_np.value() == 2 and core.load_config()["tr_np_user"] is False
    qapp.processEvents()
    w.show_tab("levels")
    qapp.processEvents()
    assert transition_notices.count(core.T("跃迁 MPI 进程数的默认值已改为 {n}。", n=2)) == 1
    w.run.mpirun_edit.setText("no-launcher")
    w.run.mpirun_edit.editingFinished.emit()
    assert w.run.np() == 1
    w.show_tab("levels")
    assert w.panel("levels").tr_np.value() == 1
    assert w.close()


def test_manual_process_counts_stay_fixed(qapp, tmp_path, mpi_settings):
    import grasp_gui

    w = grasp_gui.MainWindow(str(tmp_path), "calc")
    w.run.np_spin.setValue(3)
    w.show_tab("levels")
    panel = w.panel("levels")
    panel.tr_np.setValue(5)
    w.run.mpirun_edit.setText("no-launcher")
    w.run.mpirun_edit.editingFinished.emit()
    w.show_tab("levels")
    assert w.run.np() == 3 and panel.tr_np.value() == 5
    assert core.load_config()["np_user"] is core.load_config()["tr_np_user"] is True
    assert w.close()


def test_user_parameter_file_marks_count_while_session_apply_does_not(qapp, tmp_path, mpi_settings, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    import grasp_gui

    w = grasp_gui.MainWindow(str(tmp_path), "calc")
    w.run.apply({"np": 5})
    assert w.run.np() == 5 and not w.run.np_user
    parameters = tmp_path / "params.json"
    parameters.write_text(json.dumps({"np": 6}))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (str(parameters), "JSON"))
    w.calc.load_params()
    assert w.run.np() == 6 and w.run.np_user
    w.run.mpirun_edit.setText("no-launcher")
    w.run.mpirun_edit.editingFinished.emit()
    assert w.run.np() == 6
    assert w.close()
