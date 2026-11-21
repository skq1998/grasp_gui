"""绘制收敛图时，检验对数刻度、缺层和只画选中态。"""

import pytest

pytestmark = pytest.mark.gui
pytest.importorskip("PySide6.QtWidgets")

from convergence_plot import ConvergencePlot


@pytest.mark.parametrize("language", ["zh", "en"])
def test_plot_magnitude_is_logarithmic_and_selection_keeps_color(qapp, language):
    from grasp_core import set_lang

    set_lang(language)
    result = dict(layers=[dict(file=f"layer{i}.m") for i in range(1, 5)],
                  rows=[dict(id="a", j="1/2", parity=-1, lsj="", primary=("2p(1)", ""),
                             points=[dict(delta_cm=value) for value in (None, -1, 10, 100)]),
                        dict(id="b", j="1/2", parity=1, lsj="3s_2S", primary=("3s(1)", ""),
                             points=[dict(delta_cm=value) for value in (None, 3, 0, 30)])])
    plot = ConvergencePlot()
    plot.resize(640, 240)
    try:
        plot.set_result(result, unit="eV", units_per_cm=.01)
        assert not plot.grab().isNull()
        assert "eV" in plot.accessibleName()
        assert [len(layer) for layer in plot.markers] == [0, 2, 1, 2]
        assert plot.has_zero
        points = [layer[0] for layer in plot.markers[1:]]
        assert [value for _, _, value in points] == pytest.approx([.01, .1, 1])
        ys = [point.y() for point, _, _ in points]
        assert ys[0] > ys[1] > ys[2]
        assert ys[0] - ys[1] == pytest.approx(ys[1] - ys[2])
        color = plot.series[1][1]
        plot.set_result(result, selected={"b"})
        assert not plot.grab().isNull()
        assert [len(layer) for layer in plot.markers] == [0, 1, 0, 1]
        assert "3s_2S" in plot.markers[-1][0][1]
        assert len(plot.series) == 1 and plot.series[0][1] == color
        result["rows"][1]["points"][2]["delta_cm"] = 300
        plot.set_result(result)
        picture = plot.grab()
        a, b, c = [layer[1][0] for layer in plot.markers[1:]]
        # 检查第二条折线围出的内部，画点用的画刷不能把它填成实色块。
        inside = (.45 * a + .35 * b + .2 * c) * picture.devicePixelRatio()
        assert picture.toImage().pixelColor(inside.toPoint()) == plot.palette().base().color()
    finally:
        plot.close()
