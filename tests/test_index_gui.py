"""后台读入期间的响应、结果展示和请求代次检查。"""

import threading
import time

import pytest

pytestmark = pytest.mark.gui
pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtCore import QTimer, Qt

import grasp_index as index
import level_panel as panel
from test_large_files import make_files


def spin(app, until, timeout=15):
    end = time.monotonic() + timeout
    while not until():
        if time.monotonic() >= end:
            pytest.fail("background read timed out")
        app.processEvents()
        time.sleep(0.005)


def test_background_results_and_composition(qapp, tmp_path):
    path = make_files(tmp_path, 1100, 1)
    widget = panel.LevelPanel(str(tmp_path))
    try:
        spin(qapp, lambda: not widget.loading)
        assert widget.level_table.rowCount() == 1
        key = next(iter(widget.levels))
        widget.select(key)
        spin(qapp, lambda: key in widget.compositions)
        assert widget.jj_table.rowCount() > 0
        expected = widget.levels[key][2].top_components(500, widget.min_w.value() / 100)
        assert widget.jj_table.rowCount() == len(expected)
        widget.min_w.setValue(0)
        assert widget.jj_table.rowCount() == 500
        widget.files.item(0).setCheckState(Qt.Unchecked)
        assert widget.level_table.rowCount() == 0
        fallback = tmp_path / "fallback.c"
        fallback.write_bytes(path.with_suffix(".c").read_bytes())
        with path.with_suffix(".c").open("a") as f:
            f.write(" 2p-( 1)\n\n 1/2-\n")
        widget.scan()
        spin(qapp, lambda: not widget.loading)
        assert widget.read_data[str(path)][2] == str(fallback)
        cache = index.cache_dirs(path)[0] / index.cache_name(path, "mix")
        path.unlink()
        widget.scan()
        spin(qapp, lambda: not cache.exists())
    finally:
        widget.shutdown()
        widget.close()


def test_responsive_and_old_result_discarded(qapp, tmp_path, monkeypatch):
    first, second = tmp_path / "first", tmp_path / "second"
    make_files(first, 31, 1)
    make_files(second, 45, 1)
    real = panel.read_level_files
    started = threading.Event()

    def slow(paths, limit, directory=None, cancel=None, progress=None):
        if paths and str(first) in paths[0]:
            started.set()
            for _ in range(100):
                index.checkpoint(cancel)
                time.sleep(0.01)
        return real(paths, limit, directory, cancel, progress)

    monkeypatch.setattr(panel, "read_level_files", slow)
    ticks = []
    timer = QTimer()
    timer.timeout.connect(lambda: ticks.append(1))
    timer.start(10)
    widget = panel.LevelPanel(str(first))
    try:
        spin(qapp, started.is_set)
        spin(qapp, lambda: len(ticks) >= 5)
        assert widget.loading
        widget.set_dir(str(second))
        spin(qapp, lambda: not widget.loading)
        assert len(widget.levels) == 1
        assert all(str(second) in key[0] for key in widget.levels)
        lv = next(iter(widget.levels.values()))[2]
        assert len(lv.coeffs) == 45
    finally:
        timer.stop()
        widget.shutdown()
        widget.close()
    assert not widget.reader.thread.is_alive()
