"""零点选项同步影响本机/远程视图、选中标题和图表/CSV 导出。"""

import csv
from pathlib import Path
import time

import pytest

import grasp_core as core
import grasp_mix as mix
from test_energy_zero import csum, write_mix

pytestmark = pytest.mark.gui


def spin(qapp, predicate):
    deadline = time.monotonic() + 10
    while not predicate():
        assert time.monotonic() < deadline
        qapp.processEvents()
        time.sleep(0.005)


@pytest.fixture
def panel(qapp, tmp_path, monkeypatch):
    from PySide6.QtCore import QEvent, Qt
    from level_panel import LevelPanel
    monkeypatch.setattr(core, "CONFIG_FILE", str(tmp_path / "preferences.json"))
    data = [("even.m", [-100, -99], 1, ()), ("odd.cm", [-99.8, -99.6], -1, ()),
            ("qed_even.cm", [-158, -157.5], 1, ("Breit", "VP")),
            ("qed_odd.cm", [-157.8, -157.7], -1, ("Breit", "VP"))]
    for name, energies, parity, terms in data:
        path = write_mix(tmp_path / name, energies, parity)
        if path.suffix == ".cm":
            path.with_suffix(".csum").write_text(csum(terms, se=bool(terms)))
    widget = LevelPanel(str(tmp_path))
    for i in range(widget.files.count()):
        widget.files.item(i).setCheckState(Qt.Checked)
    spin(qapp, lambda: not widget.loading and len(widget.levels) == 8)
    widget.unit.setCurrentIndex(2)
    yield widget
    widget.shutdown()
    widget.deleteLater()
    qapp.sendPostedEvents(None, QEvent.DeferredDelete)


def test_switch_changes_values_title_axis_and_persists(panel):
    widget = panel
    assert widget.zero.currentIndex() == 0 and widget.energy_zeros == {"DC": -100, "DC+Breit+VP+SE": -158}
    key = next(key for key in widget.levels if Path(key[0]).name == "odd.cm" and key[2] == 1)
    widget.select(key)
    assert "0.20000000" in widget.title.text()
    assert "各计算组分别" in widget.diagram.axis
    entry = next(row for col in widget.diagram.columns for row in col["levels"] if row["key"] == key)
    assert entry["y"] == pytest.approx(0.2) and "计算组：DC" in entry["info"]
    widget.zero.setCurrentIndex(1)
    assert "58.20000000" in widget.title.text()
    assert "所有计算组共用" in widget.diagram.axis
    assert core.load_config()["level_zero"] == 1
    entry = next(row for col in widget.diagram.columns for row in col["levels"] if row["key"] == key)
    assert entry["y"] == pytest.approx(58.2)
    # 跃迁始终取原始总能量之差，不受能量零点选项影响。
    upper = next(k for k in widget.levels if Path(k[0]).name == "odd.cm" and k[2] == 2)
    wavelength = widget.wavelength(upper, key)
    widget.zero.setCurrentIndex(0)
    assert widget.wavelength(upper, key) == wavelength


@pytest.mark.parametrize("unified", [False, True])
def test_csv_preserves_columns_and_records_actual_zero(panel, tmp_path, monkeypatch, unified):
    widget = panel
    widget.zero.setCurrentIndex(int(unified))
    path = tmp_path / "levels.csv"
    monkeypatch.setattr(widget, "export_path", lambda *args: str(path))
    widget.export_levels()
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        assert reader.fieldnames == ["file", "J", "2J", "parity", "index", "configuration", "configuration_full",
                                     "term", "E_total_au", "E_rel_au", "E_rel_cm-1", "E_rel_eV", "purity_percent",
                                     "leading_component", "lsj_label", "leading_csf_jj", "zero_group", "E_zero_au"]
        rows = list(reader)
    row = next(row for row in rows if row["file"] == "odd.cm" and row["index"] == "1")
    assert row["zero_group"] == "DC" and float(row["E_total_au"]) == -99.8
    assert float(row["E_zero_au"]) == (-158 if unified else -100)
    assert float(row["E_rel_au"]) == pytest.approx(58.2 if unified else 0.2)
    assert float(row["E_rel_cm-1"]) == pytest.approx(float(row["E_rel_au"]) * mix.HARTREE_CM, abs=1e-4)


@pytest.mark.parametrize("extension", ["svg", "pdf", "png"])
def test_diagram_export_uses_current_zeros_and_axis(panel, tmp_path, monkeypatch, extension):
    import level_panel
    widget = panel
    path = tmp_path / ("levels." + extension)
    options = dict(level_panel.DiagramExportDialog.DEFAULTS, labels=False, energy=True, transitions=False)
    monkeypatch.setattr(level_panel.DiagramExportDialog, "ask", lambda *args: options)
    monkeypatch.setattr(widget, "export_path", lambda *args: str(path))
    exports = []
    draw = widget.diagram.draw
    def captured(painter, width, height, export=None):
        exports.append(export)
        return draw(painter, width, height, export)
    monkeypatch.setattr(widget.diagram, "draw", captured)
    key = next(key for key in widget.levels if Path(key[0]).name == "odd.cm" and key[2] == 1)
    widget.export_diagram()
    assert path.stat().st_size > 100
    assert "each calculation group" in exports[-1]["axis"]
    assert exports[-1]["labels"][key] == [("0.20000000", "n")]
    widget.zero.setCurrentIndex(1)
    widget.export_diagram()
    assert "all calculation groups" in exports[-1]["axis"]
    assert exports[-1]["labels"][key] == [("58.20000000", "n")]


def test_summary_change_alone_refreshes_group_and_unknown_warning(panel, qapp):
    widget = panel
    warnings = []
    widget.message.connect(warnings.append)
    summed = Path(widget.dir.text()) / "odd.csum"
    summed.unlink()
    widget.redraw()
    spin(qapp, lambda: not widget.loading)
    assert widget.zero_groups[str(summed.with_suffix(".cm"))] == "unknown"
    assert any("无法确定哈密顿量" in text for text in warnings)
    summed.write_text(csum(("Breit", "VP"), True))
    widget.redraw()
    spin(qapp, lambda: not widget.loading)
    assert widget.zero_groups[str(summed.with_suffix(".cm"))] == "DC+Breit+VP+SE"


def test_server_diagram_ignores_desktop_unified_preference(panel, monkeypatch):
    import grasp_render
    from grasp_service import Service
    from level_panel import LevelDiagram
    panel.zero.setCurrentIndex(1)
    columns = []
    original = LevelDiagram.set_columns
    def captured(self, data, unit, axis=None):
        columns.append((data, axis))
        return original(self, data, unit, axis)
    monkeypatch.setattr(LevelDiagram, "set_columns", captured)
    result = grasp_render.render(Service(panel.dir.text()), "plot_levels", panel.dir.text())
    assert result["_image"] and "各计算组分别" in columns[-1][1]
    values = {col["file"]: [row["y"] for row in col["levels"]] for col in columns[-1][0]}
    assert values["even.m"][0] == 0 and values["qed_even.cm"][0] == 0
    assert values["odd.cm"][0] == pytest.approx(0.2 * mix.HARTREE_CM)


def test_remote_view_uses_server_groups_without_local_summary_reads(panel, qapp, monkeypatch, tmp_path):
    import sys
    from PySide6.QtCore import QEvent, Qt
    import grasp_ssh as ssh
    from remote_panel import RemoteLevels, Source
    client = ssh.Client("fixture", command=[sys.executable, str(Path(__file__).parents[1] / "grasp_gui.py"), "remote"],
                        cache=ssh.Cache("zero", root=tmp_path / "remote-cache"))
    source = Source(client)
    def no_local_read(*args, **kwargs):
        pytest.fail("Remote groups must be read on the server")
    monkeypatch.setattr(mix, "hamiltonian", no_local_read)
    widget = RemoteLevels(source, panel.dir.text())
    source.setParent(widget)
    try:
        spin(qapp, lambda: len(widget.levels) == 6 and not source.waiting)
        for i in range(widget.files.count()):
            widget.files.item(i).setCheckState(Qt.Checked)
        assert len(widget.levels) == 8
        assert widget.energy_zeros == {"DC": -100, "DC+Breit+VP+SE": -158}
        key = next(k for k in widget.levels if Path(k[0]).name == "odd.cm" and k[2] == 1)
        widget.select(key)
        assert "0.20000000" in widget.title.text()
        widget.zero.setCurrentIndex(1)
        assert "58.20000000" in widget.title.text()
    finally:
        widget.shutdown()
        source.close()
        client.thread.join(timeout=5)
        widget.deleteLater()
        qapp.sendPostedEvents(None, QEvent.DeferredDelete)


def test_old_remote_fields_become_unknown_without_local_reads(panel, monkeypatch, qapp):
    from types import SimpleNamespace
    from PySide6.QtCore import QEvent, QObject
    from remote_panel import RemoteLevels
    from grasp_service import Service
    data = Service(panel.dir.text()).levels(panel.dir.text())
    for row in data["levels"]:
        row.pop("zero_group")
        row.pop("zero_warning")
    class Source(QObject):
        client = SimpleNamespace(host="old-fixture")
        def submit(self, *args, **kwargs):
            pass
    source = Source()
    widget = RemoteLevels(source, "/remote/calculation")
    source.setParent(widget)
    def no_local_read(*args, **kwargs):
        pytest.fail("Old server fallback must not read local .csum")
    monkeypatch.setattr(mix, "hamiltonian", no_local_read)
    try:
        widget.apply_remote(dict(data, files=["even.m", "odd.cm", "qed_even.cm", "qed_odd.cm"]))
        assert set(widget.zero_groups.values()) == {"unknown"}
        assert widget.zero_warnings
    finally:
        widget.shutdown()
        widget.deleteLater()
        qapp.sendPostedEvents(None, QEvent.DeferredDelete)
