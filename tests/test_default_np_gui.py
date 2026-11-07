"""进程数的首次保存、旧设置保留，以及主窗口和跃迁页的显示。"""

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
    w.show_tab("levels")
    panel = w.panel("levels")
    assert panel.tr_np.value() == expected
    assert core.load_config()["tr_np"] == expected
    assert w.run.np_spin.maximum() == 8
    assert panel.tr_np.maximum() == 32
    assert "GRASP" in panel.tr_np.toolTip()
    w.set_language("en" if lang == "zh" else "zh")
    assert w.run.np() == expected
    assert w.panel("levels").tr_np.value() == expected
    assert w.close()


def test_saved_counts_and_other_settings_are_retained(qapp, tmp_path, mpi_settings, monkeypatch):
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


def test_defaults_are_stable_when_mpi_changes(qapp, tmp_path, mpi_settings):
    from level_panel import LevelPanel

    assert gc.saved_settings()["np"] == 2
    panel = LevelPanel(str(tmp_path))
    assert panel.tr_np.value() == 2
    panel.close()
    (mpi_settings / "mpirun").unlink()
    assert gc.saved_settings()["np"] == 2
    panel = LevelPanel(str(tmp_path))
    assert panel.tr_np.value() == 2
    panel.close()


def test_transition_default_is_independent_of_saved_main_count(qapp, tmp_path, mpi_settings):
    from level_panel import LevelPanel

    core.save_config(np=7)
    panel = LevelPanel(str(tmp_path))
    assert panel.tr_np.value() == 2
    assert core.load_config() == dict(np=7, tr_np=2)
    panel.close()
