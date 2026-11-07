"""只在绘图请求时加载 Qt，复用查看器的绘图函数。"""

import os
from pathlib import Path
import tempfile

from grasp_core import T

_app = None


def render(service, kind, directory, files=None, limit=200, output=None):
    global _app
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    try:
        from PySide6.QtCore import QBuffer, QIODevice, QRect, QSize, QSizeF
        from PySide6.QtGui import QColor, QImage, QPageSize, QPainter, QPdfWriter
        from PySide6.QtWidgets import QApplication
    except ImportError as e:
        raise RuntimeError(T("绘图需要 Qt；当前服务器没有图形库")) from e
    _app = QApplication.instance() or QApplication([])
    root = service.directory_files(directory)
    width, height = 1200, 800
    if kind == "plot_levels":
        from level_panel import LevelDiagram, PARITY_COLORS
        import grasp_mix as mix

        rows = service.levels(directory, files, limit)["levels"]
        if not rows:
            raise ValueError(T("没有可绘制的能级"))
        e0 = min(r["energy_hartree"] for r in rows)
        columns = {}
        for row in rows:
            group = row["file"], row["block"]
            title = row["J"] + ("+" if row["parity"] > 0 else "−")
            col = columns.setdefault(group, dict(file=row["file"], title=title, levels=[]))
            key = row["file"], row["block"], row["index"]
            col["levels"].append(dict(key=key, y=(row["energy_hartree"] - e0) * mix.HARTREE_CM,
                                      label=str(row["index"]), color=PARITY_COLORS[row["parity"]], info=""))
        widget = LevelDiagram()
        widget.resize(width, height)
        widget.set_columns(list(columns.values()), "cm⁻¹")
        draw = lambda painter: widget.draw(painter, width, height)
    else:
        from wfn_panel import PlotWidget
        from PySide6.QtCore import Qt
        import grasp_wfn as wfn

        names = files if files is not None else wfn.find_w_files(str(root))
        series = []
        colors = ("#2468bd", "#b12d2d", "#338044", "#8b4ab0")
        for name in names:
            path = service.path(str(root / name))
            for o in wfn.read_w(path):
                series.append(dict(label=path.name + " " + o.name, x=list(o.r), r=list(o.r), y=list(o.P),
                                   color=colors[len(series) % len(colors)], style=Qt.SolidLine))
                if len(series) >= 20:
                    break
            if len(series) >= 20:
                break
        if not series:
            raise ValueError(T("没有可绘制的径向波函数"))
        widget = PlotWidget("P(r)")
        widget.resize(width, height)
        widget.set_series(series)
        draw = widget.render_plot
    image = QImage(width, height, QImage.Format_ARGB32)
    image.fill(QColor("white"))
    painter = QPainter(image)
    try:
        draw(painter)
    finally:
        painter.end()
    buffer = QBuffer()
    buffer.open(QIODevice.WriteOnly)
    if not image.save(buffer, "PNG"):
        raise OSError(T("无法生成 PNG"))
    png = bytes(buffer.data())
    result = dict(_image=png, width=width, height=height)
    if output is not None:
        path = service.path(str(root / output))
        ext = path.suffix.lower()
        if ext not in (".png", ".svg", ".pdf"):
            raise ValueError(T("图片文件必须使用 PNG、SVG 或 PDF 扩展名"))
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, suffix=ext, delete=False) as f:
                temporary = f.name
            if ext == ".png":
                Path(temporary).write_bytes(png)
            else:
                if ext == ".svg":
                    from PySide6.QtSvg import QSvgGenerator
                    device = QSvgGenerator()
                    device.setFileName(temporary)
                    device.setSize(QSize(width, height))
                    device.setViewBox(QRect(0, 0, width, height))
                else:
                    device = QPdfWriter(temporary)
                    device.setResolution(96)
                    device.setPageSize(QPageSize(QSizeF(width * 72 / 96, height * 72 / 96), QPageSize.Point))
                painter = QPainter(device)
                try:
                    draw(painter)
                finally:
                    painter.end()
                del device
            if not os.path.getsize(temporary):
                raise OSError(T("无法写入 {p}", p=str(path)))
            os.replace(temporary, path)
            result["output"] = str(path)
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)
    widget.close()
    return result
