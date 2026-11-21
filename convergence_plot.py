"""显示逐层激发能增量，复用能级页的能量单位。"""

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QToolTip, QWidget

from grasp_core import T
from wfn_panel import COLORS, STYLES, fmt, fmt_log


class ConvergencePlot(QWidget):
    def __init__(self):
        super().__init__()
        self.setMinimumHeight(180)
        self.setMouseTracking(True)
        self.set_result(None)

    def set_result(self, result, *, selected=None, unit="cm⁻¹", units_per_cm=1.0):
        """按结果顺序为各态分配颜色，只绘制选中的态时沿用同一颜色。"""
        self.unit = unit
        self.axis = T("激发能增量绝对值 ({unit}，对数刻度)", unit=unit)
        self.setAccessibleName(self.axis)
        self.layers = [layer["file"] for layer in result["layers"]] if result else []
        self.series, self.markers = [], []
        self.frame = QRectF()
        self.has_zero = False
        for i, row in enumerate(result["rows"] if result else ()):
            if selected is not None and row["id"] not in selected:
                continue
            values = [point["delta_cm"] for point in row["points"]]
            self.has_zero |= 0 in values
            values = tuple(abs(value) * units_per_cm if value else None for value in values)
            symmetry = row["j"] + ("+" if row["parity"] > 0 else "−")
            name = row["lsj"] or row["primary"][0] or row["id"]
            self.series.append((f"{symmetry}  {name}", QColor(COLORS[i % len(COLORS)]),
                                STYLES[(i // len(COLORS)) % len(STYLES)], values))
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        palette = self.palette()
        painter.fillRect(self.rect(), palette.base())
        text = palette.text().color()
        painter.setPen(text)
        self.markers = [[] for _ in self.layers]
        levels = [math.log10(value) for _, _, _, values in self.series for value in values if value]
        if not levels:
            painter.drawText(self.rect(), Qt.AlignCenter, T("没有可绘制的非零增量。"))
            return

        lo, hi = math.floor(min(levels)), math.ceil(max(levels))
        if hi == lo:
            lo, hi = lo - 1, hi + 1
        step = max(1, math.ceil((hi - lo) / 6))
        ticks = range(lo, hi + 1, step)
        fm = painter.fontMetrics()
        height = fm.height()
        left = 2 * height + max(fm.horizontalAdvance(fmt_log(y)) for y in ticks)
        footer = (3 if self.has_zero else 2) * height + 12
        rect = QRectF(left, 10, max(10, self.width() - left - 15), max(10, self.height() - 10 - footer))
        self.frame = rect
        span = max(1, len(self.layers) - 1)
        grid = palette.midlight().color()

        for tick in ticks:
            y = rect.bottom() - (tick - lo) * rect.height() / (hi - lo)
            painter.setPen(grid)
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            painter.setPen(text)
            painter.drawText(QRectF(height + 4, y - height / 2, left - height - 10, height),
                             Qt.AlignRight | Qt.AlignVCenter, fmt_log(tick))

        stride = max(1, math.ceil(len(self.layers) / max(2, int(rect.width() / 32))))
        for i in range(0, len(self.layers), stride):
            x = rect.left() + i * rect.width() / span
            painter.setPen(grid)
            painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
            painter.setPen(text)
            painter.drawText(QRectF(x - 14, rect.bottom() + 3, 28, height), Qt.AlignCenter, str(i + 1))
        painter.setPen(text)
        painter.drawRect(rect)
        painter.drawText(QRectF(rect.left(), rect.bottom() + height + 5, rect.width(), height),
                         Qt.AlignCenter, T("轨道层"))
        if self.has_zero:
            painter.drawText(QRectF(rect.left(), rect.bottom() + 2 * height + 8, rect.width(), height),
                             Qt.AlignCenter, T("零增量保留在表中。"))
        painter.save()
        painter.translate(0, rect.bottom())
        painter.rotate(-90)
        painter.drawText(QRectF(0, 0, rect.height(), height + 4), Qt.AlignCenter,
                         T("|ΔE| ({unit}，对数)", unit=self.unit))
        painter.restore()

        painter.setClipRect(rect.adjusted(-2, -2, 2, 2))
        for label, color, style, values in self.series:
            curve, connected, dots = QPainterPath(), False, []
            for i, value in enumerate(values):
                if not value:
                    connected = False  # 零或缺失值不连接前后的层。
                    continue
                point = QPointF(rect.left() + i * rect.width() / span,
                                rect.bottom() - (math.log10(value) - lo) * rect.height() / (hi - lo))
                if connected:
                    curve.lineTo(point)
                else:
                    curve.moveTo(point)
                connected = True
                dots.append(point)
                self.markers[i].append((point, label, value))
            pen = QPen(color, 1.5)
            pen.setStyle(style)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(curve)
            painter.setPen(Qt.NoPen)
            painter.setBrush(color)
            for point in dots:
                painter.drawEllipse(point, 2.5, 2.5)

    def mouseMoveEvent(self, event):
        target = event.position()
        if self.frame.contains(target) and self.markers:
            i = round((target.x() - self.frame.left()) * (len(self.layers) - 1) / self.frame.width())
            nearest = min(self.markers[i], key=lambda row: (row[0] - target).manhattanLength(), default=None)
            if nearest and (nearest[0] - target).manhattanLength() < 12:
                _, label, value = nearest
                tip = f"{label}\n{self.layers[i]}\n|ΔE| = {fmt(value)} {self.unit}"
                QToolTip.showText(event.globalPosition().toPoint(), tip, self)
                return
        QToolTip.hideText()

    def leaveEvent(self, _event):
        QToolTip.hideText()
