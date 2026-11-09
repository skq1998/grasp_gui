"""
能级与组成查看器：读取混合系数文件 (name.m / name.cm)，按 J^π 分列画出能级图；
鼠标停在能级上显示信息，点击后在右侧列出该原子态 (ASF) 的组成：
jj 耦合下的 CSF 混合系数 (配合 name.c)，以及 jj2lsj 给出的 LSJ 组成 (name.lsj.lbl)。
把一个能级连到另一个能级即可计算两者之间的跃迁 (自动运行 rbiotransform 和 rtransition，见 grasp_trans.py)。
"""

import json
import math
import os
import queue
import shlex
import shutil
import struct
import threading

from PySide6.QtCore import QEvent, QMarginsF, QPointF, QRect, QRectF, QSize, QSizeF, Qt, QTimer, Signal
from PySide6.QtGui import (QColor, QFont, QFontDatabase, QFontMetricsF, QImage, QPainter, QPen, QPolygonF,
                           QTextCursor)
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog,
                               QFormLayout, QHBoxLayout, QLabel, QMenu, QMessageBox, QSpinBox,
                               QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton, QScrollArea, QSplitter,
                               QStyledItemDelegate, QTableWidget, QTableWidgetItem, QTabWidget, QToolTip,
                               QVBoxLayout, QWidget)

import grasp_config
import grasp_core as core
import grasp_index as index
import grasp_mix as mix
import grasp_session as sessions
import grasp_trans as tr
from grasp_core import T
from gui_common import Panel, button, help_button, saved_settings
from wfn_panel import fmt, nice_ticks

# (下拉框中的名称 (用时再翻译), 表头和坐标轴中的简写, 1 a.u. 折合多少, 显示格式)；第一个为默认单位
UNITS = [("eV", "eV", mix.HARTREE_EV, "{:.5f}"), ("cm⁻¹", "cm⁻¹", mix.HARTREE_CM, "{:.2f}"),
         ("原子单位 (Hartree)", "a.u.", 1.0, "{:.8f}")]


from grasp_mix import load_result_files as read_level_files, load_composition as read_composition


def energy_axis(unit, unified=False, multiple=True, english=False):
    if not multiple:
        text = "相对能量 ({u})"
    elif unified:
        text = "相对能量 ({u})，所有计算组共用最低能级为零"
    else:
        text = "相对能量 ({u})，各计算组分别以最低能级为零"
    return core.EN[text].format(u=unit) if english else T(text, u=unit)


class NumItem(QTableWidgetItem):
    """显示格式化的文字，按数值排序。"""

    def __init__(self, text, value):
        super().__init__(text)
        self.value = value
        self.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)

    def __lt__(self, other):
        return self.value < getattr(other, "value", 0)
PARITY_COLORS = {1: QColor("#1f5fbf"), -1: QColor("#c0392b")}     # 偶宇称蓝色，奇宇称红色
SELECTED = QColor("#f39c12")
ARROW = QColor("#d35400")          # 选中的一对能级之间的跃迁
LINK = QColor("#6c3483")           # 选中能级的其他已算跃迁


def rich_width(p, segs):
    """富文本 [(文字, "n" | "sup" | "sub")] 的宽度。"""
    base = p.font()
    small = QFont(base)
    small.setPointSizeF(base.pointSizeF() * 0.72)
    w = 0.0
    for text, mode in segs:
        w += QFontMetricsF(base if mode == "n" else small).horizontalAdvance(text)
    return w


def draw_rich(p, x, baseline, segs):
    """画富文本：上标、下标用小号字并上下移动 (不依赖字体中的上下标字符，导出 SVG 时也正确)。"""
    base = QFont(p.font())
    small = QFont(base)
    small.setPointSizeF(base.pointSizeF() * 0.72)
    asc = QFontMetricsF(base).ascent()
    for text, mode in segs:
        font = base if mode == "n" else small
        dy = {"n": 0.0, "sup": -asc * 0.42, "sub": asc * 0.22}[mode]
        p.setFont(font)
        p.drawText(QPointF(x, baseline + dy), text)
        x += QFontMetricsF(font).horizontalAdvance(text)
    p.setFont(base)
    return x


class LevelDiagram(QWidget):
    """能级图：每个 (文件, J^π) 一列，横线为能级；滚轮和拖动平移，捏合 (或 Ctrl + 滚轮) 缩放，双击复位，点击选中能级。
    从一个能级拖到另一个能级 (或按住 Shift 点击第二个能级) 选中一对能级，用于计算跃迁。
    按住右键 (或 Ctrl + 左键) 拖出一个框：放大到框内的能量范围；在空白处单击右键或按 Backspace 回到上一个视图。"""
    levelClicked = Signal(object)
    pairSelected = Signal(object, object)
    arrowRemoved = Signal(object, object)

    MARGIN = (78, 12, 46, 30)      # 左、右、上 (列标题)、下

    def __init__(self):
        super().__init__()
        self.columns = []          # dict(file, title, levels=[dict(key, y, label, color, info)])
        self.unit = "cm⁻¹"
        self.axis = None
        self.yr = None
        self.hover = self.selected = None
        self.drag = None
        self.press = None          # (能级, 按下位置)：拖动超过几个像素即开始连线
        self.linking = None        # 连线时鼠标的位置
        self.pair = None           # (上能级, 下能级)：选中的一对，未标注时画虚线箭头
        self.arrows = []           # [(上能级, 下能级, 文字)]：图上标注的跃迁 (实线箭头，文字如波长)
        self.arrow_segments = []   # [(上能级, 下能级, 起点, 终点)]：绘制时更新，用于右键移除
        self.links = []            # [(能级, 能级, 强度 0..1)]：选中能级的全部已算跃迁 (可选)
        self.centers = {}          # 能级 -> (x, y)，绘制时更新
        self.box = None            # 框选放大：(起点, 当前点)
        self.history = []          # 框选放大前的视图 (纵轴范围；None = 自动)，供“回到上一个视图”
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.ClickFocus)      # 接收 Backspace
        self.setMinimumHeight(240)

    def set_columns(self, columns, unit, axis=None):
        self.columns, self.unit = columns, unit
        self.axis = axis
        self.yr = None
        self.history = []
        self.hover = None
        self.update()

    def auto_y(self):
        ys = [lv["y"] for c in self.columns for lv in c["levels"]]
        if not ys:
            return (0.0, 1.0)
        lo, hi = min(ys), max(ys)
        pad = (hi - lo) * 0.06 or 1.0
        return (lo - pad, hi + pad)

    def y_range(self):
        return self.yr or self.auto_y()

    def plot_rect(self, width=None, height=None):
        l, r, t, b = self.MARGIN
        width = self.width() if width is None else width
        height = self.height() if height is None else height
        return QRectF(l, t, max(width - l - r, 10), max(height - t - b, 10))

    def to_py(self, y, rect, yr):
        return rect.bottom() - (y - yr[0]) / (yr[1] - yr[0]) * rect.height()

    def from_py(self, py, rect, yr):
        return yr[0] + (rect.bottom() - py) / rect.height() * (yr[1] - yr[0])

    def geometry_of(self, rect, widths=None):
        """各列的 (左边界 x, 宽度)：屏幕上所有列等宽；导出时按 widths (各列所需宽度) 的比例分配。"""
        n = max(len(self.columns), 1)
        widths = widths or [1.0] * n
        scale = rect.width() / (sum(widths) or 1)
        out, x = [], rect.left()
        for w in widths:
            out.append((x, w * scale))
            x += w * scale
        return out

    def paintEvent(self, _event):
        p = QPainter(self)
        self.draw(p, self.width(), self.height())

    # ---- 导出时的标注 ----
    LINE_LEN = 46                  # 导出图中能级线的长度 (像素)

    def label_widths(self, p, labels):
        """导出时各列所需的宽度：能级线 + 最宽的标注。"""
        out = []
        for col in self.columns:
            w = max((rich_width(p, labels[lv["key"]]) for lv in col["levels"] if lv["key"] in labels), default=0)
            out.append(self.LINE_LEN + 16 + (w + 14 if w else 0))
        return out

    def export_size(self, height, labels):
        """导出图的大小：宽度由各列标注的宽度决定 (至少与屏幕上一样宽)。"""
        img = QImage(1, 1, QImage.Format_ARGB32)
        p = QPainter(img)
        p.setFont(self.font())
        need = sum(self.label_widths(p, labels)) + self.MARGIN[0] + self.MARGIN[1]
        p.end()
        return int(max(need, self.width())), int(height)

    def draw(self, p, width, height, export=None):
        """画能级图。export 为 None 时画屏幕上的交互视图；导出时为
        dict(labels={能级: 富文本}, transitions=是否画选中的跃迁, axis=纵轴标题)。"""
        p.setRenderHint(QPainter.Antialiasing)
        p.setFont(self.font())
        p.fillRect(QRectF(0, 0, width, height), QColor("white"))
        rect = self.plot_rect(width, height)
        yr = self.y_range()
        fm = p.fontMetrics()

        big = yr[1] - yr[0] >= 100          # 如 cm⁻¹：刻度写成整数，不用科学计数法
        for y in nice_ticks(*yr, n=6):
            py = self.to_py(y, rect, yr)
            p.setPen(QPen(QColor("#ececec")))
            p.drawLine(QPointF(rect.left(), py), QPointF(rect.right(), py))
            p.setPen(QColor("#333"))
            label = f"{y:.0f}" if big else fmt(y)
            p.drawText(QPointF(rect.left() - fm.horizontalAdvance(label) - 6, py + fm.ascent() / 2 - 1), label)
        p.save()
        p.translate(14, rect.center().y())
        p.rotate(-90)
        text = export["axis"] if export else self.axis or T("相对能量 ({u})", u=self.unit)
        p.drawText(QPointF(-fm.horizontalAdvance(text) / 2, 0), text)
        p.restore()
        p.setPen(QColor("#999"))
        p.drawRect(rect)

        labels = export["labels"] if export else {}
        geo = self.geometry_of(rect, self.label_widths(p, labels) if labels else None)
        self.draw_headers(p, rect, geo)
        # 导出带标注时能级线画在列的左侧，标注写在右侧；否则居中
        span = (lambda x0, w: (x0 + 8, x0 + 8 + self.LINE_LEN)) if labels else \
            (lambda x0, w: (x0 + w * 0.15, x0 + w * 0.85))
        centers = {lv["key"]: ((span(x0, w)[0] + span(x0, w)[1]) / 2, self.to_py(lv["y"], rect, yr))
                   for (x0, w), col in zip(geo, self.columns) for lv in col["levels"]}
        if not export:
            self.centers = centers
        arrows = export.get("arrows", []) if export else self.arrows
        marked = {(a, b) for a, b, _ in arrows}
        p.save()
        p.setClipRect(rect)
        for k1, k2, strength in ([] if export else self.links):   # 选中能级的已算跃迁，越强越粗越深
            if k1 in centers and k2 in centers:
                c = QColor(LINK)
                c.setAlphaF(0.25 + 0.6 * strength)
                p.setPen(QPen(c, 0.8 + 2.4 * strength))
                p.drawLine(QPointF(*centers[k1]), QPointF(*centers[k2]))
        p.restore()
        for (x0, w), col in zip(geo, self.columns):
            p.save()
            p.setClipRect(rect)
            # 屏幕上能级只画横线，组成等信息在鼠标悬停时显示
            lx0, lx1 = span(x0, w)
            for lv in col["levels"]:
                py = self.to_py(lv["y"], rect, yr)
                key = lv["key"]
                in_pair = bool(self.pair and key in self.pair and not export)
                color = SELECTED if (key == self.selected and not export) or in_pair else lv["color"]
                width = 3.2 if (key in (self.selected, self.hover) and not export) or in_pair else 1.6
                p.setPen(QPen(color, width))
                p.drawLine(QPointF(lx0, py), QPointF(lx1, py))
            if labels:
                self.draw_labels(p, rect, yr, col, labels, lx1)
            p.restore()
        p.save()
        p.setClipRect(rect)
        segments = []
        for ku, kl, text in arrows:
            if ku in centers and kl in centers:
                a, b = QPointF(*centers[ku]), QPointF(*centers[kl])
                bold = not export and self.pair == (ku, kl)
                self.draw_arrow(p, a, b, 3.4 if bold else 2.2)
                segments.append((ku, kl, a, b))
                if text:
                    self.draw_arrow_text(p, a, b, text)
        if not export:
            self.arrow_segments = segments
            if self.pair and self.pair not in marked and all(k in centers for k in self.pair):
                self.draw_arrow(p, QPointF(*centers[self.pair[0]]), QPointF(*centers[self.pair[1]]), 2.0, dashed=True)
        if not export and self.linking and self.press and self.press[0] in self.centers:
            p.setPen(QPen(SELECTED, 2, Qt.DashLine))
            p.drawLine(QPointF(*self.centers[self.press[0]]), self.linking)
        if not export and self.box:
            box = QRectF(*self.box).normalized()
            p.setPen(QPen(SELECTED, 1, Qt.DashLine))
            p.setBrush(QColor(47, 108, 223, 40))
            p.drawRect(box)
        p.restore()

    def draw_labels(self, p, rect, yr, col, labels, x):
        """一列能级的标注：写在能级线右侧；挨得太近时上下错开 (保持顺序)，用细线连回能级。"""
        fm = p.fontMetrics()
        h = fm.height() * 1.05
        items = sorted(((self.to_py(lv["y"], rect, yr), labels[lv["key"]]) for lv in col["levels"]
                        if lv["key"] in labels and rect.top() <= self.to_py(lv["y"], rect, yr) <= rect.bottom()),
                       key=lambda t: -t[0])                    # 自下而上
        ys = [py for py, _ in items]
        for i in range(1, len(ys)):                            # 向上推开
            ys[i] = min(ys[i], ys[i - 1] - h)
        if ys and ys[-1] < rect.top() + h / 2:                 # 顶部放不下时整体下移
            ys[-1] = rect.top() + h / 2
            for i in range(len(ys) - 2, -1, -1):
                ys[i] = max(ys[i], ys[i + 1] + h)
        for (py, segs), y in zip(items, ys):
            if abs(y - py) > 1:
                p.setPen(QPen(QColor("#aaa"), 0.6))
                p.drawLine(QPointF(x + 2, py), QPointF(x + 10, y))
            # 白色底，跃迁连线从文字下面经过
            p.fillRect(QRectF(x + 10, y - fm.height() / 2, rich_width(p, segs) + 4, fm.height()), QColor(255, 255, 255, 225))
            p.setPen(QColor("#222"))
            draw_rich(p, x + 12, y + fm.ascent() / 2 - 1, segs)

    @staticmethod
    def draw_arrow_text(p, a, b, text):
        """箭头中点处的文字 (如波长)，白底。"""
        fm = p.fontMetrics()
        m = (a + b) / 2
        w = fm.horizontalAdvance(text)
        p.fillRect(QRectF(m.x() + 4, m.y() - fm.height() / 2, w + 4, fm.height()), QColor(255, 255, 255, 225))
        p.setPen(ARROW)
        p.drawText(QPointF(m.x() + 6, m.y() + fm.ascent() / 2 - 1), text)

    def arrow_at(self, pos, tol=6.0):
        """鼠标附近的标注箭头 (上能级, 下能级)，没有时为 None。"""
        best = None
        for ku, kl, a, b in self.arrow_segments:
            d = b - a
            n2 = d.x() ** 2 + d.y() ** 2
            t = 0.0 if n2 == 0 else max(0.0, min(1.0, ((pos.x() - a.x()) * d.x() + (pos.y() - a.y()) * d.y()) / n2))
            q = a + d * t
            dist = ((pos.x() - q.x()) ** 2 + (pos.y() - q.y()) ** 2) ** 0.5
            if dist < tol and (best is None or dist < best[0]):
                best = (dist, (ku, kl))
        return best[1] if best else None

    @staticmethod
    def draw_arrow(p, a, b, width=2.4, dashed=False):
        """从上能级 a 指向下能级 b 的箭头。"""
        p.setPen(QPen(ARROW, width, Qt.DashLine if dashed else Qt.SolidLine))
        p.drawLine(a, b)
        p.setPen(QPen(ARROW, width))
        d = b - a
        length = (d.x() ** 2 + d.y() ** 2) ** 0.5
        if length < 1:
            return
        ux, uy = d.x() / length, d.y() / length
        size = 11
        left = QPointF(b.x() - size * (ux * 0.87 - uy * 0.5), b.y() - size * (uy * 0.87 + ux * 0.5))
        right = QPointF(b.x() - size * (ux * 0.87 + uy * 0.5), b.y() - size * (uy * 0.87 - ux * 0.5))
        p.setBrush(ARROW)
        p.drawPolygon(QPolygonF([b, left, right]))
        p.setBrush(Qt.NoBrush)

    def draw_headers(self, p, rect, geo):
        """列标题：每个文件的名称居中写在它的几列上方 (放不下时截短)，各列下方写 J^π
        (放不下时用小号字，仍放不下则省略，悬停提示中仍有)。文件之间用实线分隔，同一文件的列之间用虚线。"""
        fm = p.fontMetrics()
        groups = []                # [文件名, 左边界, 右边界]
        for (x0, w), col in zip(geo, self.columns):
            if groups and groups[-1][0] == col["file"]:
                groups[-1][2] = x0 + w
                if x0 > rect.left():
                    p.setPen(QPen(QColor("#ddd"), 1, Qt.DashLine))
                    p.drawLine(QPointF(x0, rect.top()), QPointF(x0, rect.bottom()))
            else:
                groups.append([col["file"], x0, x0 + w])
                if x0 > rect.left():
                    p.setPen(QPen(QColor("#999"), 1))
                    p.drawLine(QPointF(x0, rect.top() - fm.height() - 22), QPointF(x0, rect.bottom()))
        bold = QFont(self.font())
        bold.setBold(True)
        p.setFont(bold)
        bfm = p.fontMetrics()
        p.setPen(QColor("#333"))
        for name, a, b in groups:
            text = bfm.elidedText(name, Qt.ElideMiddle, int(b - a - 6))
            p.drawText(QPointF((a + b) / 2 - bfm.horizontalAdvance(text) / 2, rect.top() - fm.height() - 8), text)
        small = QFont(self.font())
        small.setPointSizeF(self.font().pointSizeF() * 0.78)
        for (x0, w), col in zip(geo, self.columns):
            for font in (self.font(), small):
                p.setFont(font)
                tw = p.fontMetrics().horizontalAdvance(col["title"])
                if tw <= w - 3:
                    p.drawText(QPointF(x0 + w / 2 - tw / 2, rect.top() - 6), col["title"])
                    break
        p.setFont(self.font())

    def level_at(self, pos):
        rect = self.plot_rect()
        yr = self.y_range()
        for (x0, w), col in zip(self.geometry_of(rect), self.columns):
            if x0 <= pos.x() < x0 + w:
                best = None
                for lv in col["levels"]:
                    d = abs(self.to_py(lv["y"], rect, yr) - pos.y())
                    if d < 7 and (best is None or d < best[0]):
                        best = (d, lv)
                return best[1] if best else None
        return None

    def mouseMoveEvent(self, event):
        if self.box:
            self.box = (self.box[0], event.position())
            self.update()
            return
        if self.press and not self.drag:
            if self.linking or (event.position() - self.press[1]).manhattanLength() > 6:
                self.linking = event.position()
                lv = self.level_at(event.position())
                self.hover = lv["key"] if lv else None
                self.update()
                return
        if self.drag:
            start, yr = self.drag
            rect = self.plot_rect()
            dy = (event.position().y() - start.y()) / rect.height() * (yr[1] - yr[0])
            self.yr = (yr[0] + dy, yr[1] + dy)
            self.update()
            return
        lv = self.level_at(event.position())
        key = lv["key"] if lv else None
        if key != self.hover:
            self.hover = key
            self.update()
        if lv:
            QToolTip.showText(event.globalPosition().toPoint(), lv["info"], self)
            self.setCursor(Qt.PointingHandCursor)
        else:
            QToolTip.hideText()
            self.unsetCursor()

    def mousePressEvent(self, event):
        if event.button() == Qt.RightButton:          # 右键点标注的箭头：移除；否则开始框选
            hit = self.arrow_at(event.position())
            if hit:
                self.arrowRemoved.emit(*hit)
            else:
                self.box = (event.position(), event.position())
            return
        if event.button() != Qt.LeftButton:
            return
        if event.modifiers() & Qt.ControlModifier:
            self.box = (event.position(), event.position())
            return
        lv = self.level_at(event.position())
        if lv:
            if event.modifiers() & Qt.ShiftModifier and self.selected and lv["key"] != self.selected:
                self.pairSelected.emit(self.selected, lv["key"])
            else:
                self.press = (lv["key"], event.position())
        else:
            self.drag = (event.position(), self.y_range())

    def mouseReleaseEvent(self, event):
        if self.box:
            start, end = self.box
            self.box = None
            if abs(end.y() - start.y()) > 6:
                rect, yr = self.plot_rect(), self.y_range()
                lo, hi = sorted(self.from_py(py, rect, yr) for py in (start.y(), end.y()))
                self.history.append(self.yr)
                self.yr = (lo, hi)
            elif event.button() == Qt.RightButton:  # 单击右键 (没有拖动)：回到上一个视图
                self.back()
            self.update()
            return
        self.drag = None
        if self.press:
            key = self.press[0]
            if self.linking:
                lv = self.level_at(event.position())
                if lv and lv["key"] != key:
                    self.pairSelected.emit(key, lv["key"])
            else:
                self.levelClicked.emit(key)
        self.press = self.linking = None
        self.update()

    def mouseDoubleClickEvent(self, _event):
        self.yr = None
        self.history = []
        self.update()

    def back(self):
        """回到框选放大之前的视图。"""
        if self.history:
            self.yr = self.history.pop()
            self.update()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Backspace:
            self.back()
        elif event.key() == Qt.Key_Escape and self.box:
            self.box = None
            self.update()
        else:
            super().keyPressEvent(event)

    def zoom(self, py, f):
        """以屏幕纵坐标 py 处的能量为中心，把纵轴范围乘以 f。"""
        rect = self.plot_rect()
        yr = self.y_range()
        y = self.from_py(py, rect, yr)
        self.yr = (y + (yr[0] - y) * f, y + (yr[1] - y) * f)
        self.update()

    def wheelEvent(self, event):
        """滚轮上下平移；Ctrl + 滚轮缩放 (Windows 等平台的触控板捏合也以这种方式送来)。"""
        dy = event.angleDelta().y()
        if event.modifiers() & Qt.ControlModifier:
            if dy:
                self.zoom(event.position().y(), 0.8 ** (dy / 120))
            return
        rect = self.plot_rect()
        yr = self.y_range()
        pixels = event.pixelDelta().y()        # 触控板给出像素位移，鼠标滚轮每格 120，平移 1/10 的范围
        shift = (pixels / rect.height() if pixels else dy / 120 * 0.1) * (yr[1] - yr[0])
        if shift:
            self.yr = (yr[0] + shift, yr[1] + shift)
            self.update()

    def event(self, event):
        """触控板捏合 (macOS、Wayland)：缩放。"""
        if event.type() == QEvent.NativeGesture:
            if event.gestureType() == Qt.ZoomNativeGesture:
                self.zoom(event.position().y(), 1 / max(1 + event.value(), 0.1))
            return True
        return super().event(event)

    def leaveEvent(self, _event):
        self.hover = None
        self.update()


class WeightBar(QStyledItemDelegate):
    """权重列：在文字后面画一条与权重成比例的浅色条。"""

    def paint(self, painter, option, index):
        w = index.data(Qt.UserRole) or 0.0
        r = option.rect.adjusted(2, 3, -2, -3)
        painter.fillRect(QRectF(r.left(), r.top(), r.width() * min(max(w, 0.0), 1.0), r.height()),
                         QColor("#cfe3f7"))
        super().paint(painter, option, index)


def table(headers):
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.setEditTriggers(QTableWidget.NoEditTriggers)
    t.setSelectionBehavior(QTableWidget.SelectRows)
    t.setWordWrap(False)       # 长标签不折行，过宽时横向滚动
    t.verticalHeader().setVisible(False)
    t.horizontalHeader().setStretchLastSection(True)     # 列宽在填完后用 resizeColumnsToContents 调整一次
    return t


def item(value, color=None, align=None):
    it = QTableWidgetItem()
    it.setData(Qt.DisplayRole, value)
    if color is not None:
        it.setForeground(color)
    if align is not None:
        it.setTextAlignment(align)
    return it


def abbrev_rules():
    """组态缩写规则 [[缩写, 完整组态], ...]，默认为惰性气体原子实 ([He]、[Ne] …)。"""
    rules = core.load_config().get("config_abbrev")
    return mix.NOBLE_GAS_CORES if rules is None else rules


def config_text(d):
    """CSV 中的组态：缩写 + NIST 写法，如 [Ne]3s.3p"""
    return d["prefix"] + mix.config_ascii(d["config"])


class AbbrevDialog(QDialog):
    """编辑组态缩写规则：每行一个缩写和它代表的完整组态 (子壳层用 . 或空格分隔，如 1s2.2s2.2p6)。"""

    @classmethod
    def edit(cls, parent, preview=None):
        dlg = cls(parent, preview)
        if dlg.exec() == QDialog.Accepted:
            core.save_config(config_abbrev=dlg.rules())
            return True
        return False

    def __init__(self, parent, preview):
        super().__init__(parent)
        self.setWindowTitle(T("组态缩写"))
        self.resize(640, 420)
        self.preview_fn = preview
        lay = QVBoxLayout(self)
        note = QLabel(T("组态中含有右列的全部子壳层 (占据数相同) 时，把它们换成左列的缩写，写在组态最前面；"
                        "缩写可以留空，表示省略这些子壳层。子壳层多的规则先用。核心轨道 (CSF 文件中的 Core) 已计入组态。"))
        note.setWordWrap(True)
        lay.addWidget(note)
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels([T("缩写"), T("完整组态")])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        self.table.itemChanged.connect(self.update_preview)
        lay.addWidget(self.table, 1)
        row = QHBoxLayout()
        for text, slot in [("添加", self.add_row), ("删除", self.remove_row), ("恢复默认", self.reset)]:
            b = QPushButton(T(text))
            b.clicked.connect(slot)
            row.addWidget(b)
        row.addStretch()
        lay.addLayout(row)
        self.preview = QLabel()
        self.preview.setTextFormat(Qt.PlainText)
        lay.addWidget(self.preview)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        lay.addWidget(self.buttons)
        self.fill(abbrev_rules())

    def fill(self, rules):
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        for abbr, full in rules:
            self.add_row(abbr, full)
        self.table.blockSignals(False)
        self.table.resizeColumnToContents(0)
        self.update_preview()

    def add_row(self, abbr="", full=""):
        r = self.table.rowCount()
        self.table.insertRow(r)
        self.table.setItem(r, 0, QTableWidgetItem(abbr if isinstance(abbr, str) else ""))
        self.table.setItem(r, 1, QTableWidgetItem(full))

    def remove_row(self):
        r = self.table.currentRow()
        if r >= 0:
            self.table.removeRow(r)
            self.update_preview()

    def reset(self):
        self.fill(mix.NOBLE_GAS_CORES)

    def rules(self):
        out = []
        for r in range(self.table.rowCount()):
            abbr = (self.table.item(r, 0).text() if self.table.item(r, 0) else "").strip()
            full = (self.table.item(r, 1).text() if self.table.item(r, 1) else "").strip()
            if full:
                out.append([abbr, full])
        return out

    def update_preview(self, *_):
        bad = []
        for r in range(self.table.rowCount()):
            it = self.table.item(r, 1)
            if it is None:
                continue
            ok = not it.text().strip() or mix.parse_config(it.text()) is not None
            it.setForeground(QColor("black") if ok else QColor("#c00"))
            if not ok:
                bad.append(r + 1)
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(not bad)
        if bad:
            self.preview.setText(T("第 {r} 行的完整组态无法识别 (应写成 1s2.2s2.2p6 这样)", r=", ".join(map(str, bad))))
        elif self.preview_fn:
            d = self.preview_fn()
            prefix, rest = mix.abbreviate(d["full"], self.rules())
            self.preview.setText(T("预览：{a}  →  {b}", a=mix.config_ascii(d["full"]),
                                   b=prefix + mix.config_ascii(rest)))


class DiagramExportDialog(QDialog):
    """导出能级图的选项 (上次的选择会被记住)。"""

    DEFAULTS = dict(labels=True, energy=False, transitions=True, arrow_text="nm", english=True, height=900, scale=3)
    ARROW_TEXT = [("none", "不标注"), ("nm", "波长 (nm)"), ("A", "波长 (Å)")]

    @classmethod
    def ask(cls, parent, has_transitions):
        dlg = cls(parent, has_transitions)
        if dlg.exec() != QDialog.Accepted:
            return None
        opts = dict(labels=dlg.labels.isChecked(), energy=dlg.energy.isChecked(),
                    transitions=dlg.transitions.isChecked(), arrow_text=cls.ARROW_TEXT[dlg.arrow_text.currentIndex()][0],
                    english=dlg.lang.currentIndex() == 0,
                    height=dlg.height.value(), scale=dlg.scale.value())
        core.save_config(level_export=opts)
        return opts

    def __init__(self, parent, has_transitions):
        super().__init__(parent)
        self.setWindowTitle(T("导出能级图"))
        o = dict(self.DEFAULTS, **(core.load_config().get("level_export") or {}))
        form = QFormLayout(self)
        self.labels = QCheckBox(T("标注组态、谱项 (或 J^π)"))
        self.labels.setChecked(o["labels"])
        self.energy = QCheckBox(T("标注能量"))
        self.energy.setChecked(o["energy"])
        self.transitions = QCheckBox(T("画出图上标注的跃迁"))
        self.transitions.setChecked(o["transitions"] and has_transitions)
        self.transitions.setEnabled(has_transitions)
        self.arrow_text = QComboBox()
        self.arrow_text.addItems([T(label) for _, label in self.ARROW_TEXT])
        self.arrow_text.setCurrentIndex(next((i for i, (k, _) in enumerate(self.ARROW_TEXT) if k == o["arrow_text"]), 1))
        self.lang = QComboBox()
        self.lang.addItems(["English", "中文"])
        self.lang.setCurrentIndex(0 if o["english"] else 1)
        self.height = QSpinBox()
        self.height.setRange(300, 6000)
        self.height.setSuffix(" px")
        self.height.setValue(o["height"])
        self.scale = QSpinBox()
        self.scale.setRange(1, 6)
        self.scale.setPrefix("× ")
        self.scale.setValue(o["scale"])
        for w in (self.labels, self.energy, self.transitions):
            form.addRow(w)
        form.addRow(T("箭头上的文字"), self.arrow_text)
        abbr = QPushButton(T("组态缩写…"))
        abbr.clicked.connect(lambda: AbbrevDialog.edit(self, getattr(parent, "preview_desc", lambda: None)()))
        form.addRow(T("组态"), abbr)
        form.addRow(T("坐标轴文字"), self.lang)
        form.addRow(T("图的高度"), self.height)
        form.addRow(T("PNG 分辨率"), self.scale)
        note = QLabel(T("范围与屏幕上一致 (先缩放到需要的部分)；宽度按标注自动确定。PDF 嵌入字体，适合论文；"
                        "SVG 可在 Inkscape 等软件中编辑。"))
        note.setWordWrap(True)
        note.setStyleSheet("color: gray;")
        form.addRow(note)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)


class LevelPanel(Panel):
    KIND, TITLE = "levels", "能级图"
    MAX_ROWS = 500

    def __init__(self, workdir, settings=saved_settings):
        super().__init__()
        self.settings = settings   # 返回运行设置 (bindir, np, mpirun, mpitmp, nice) 的函数
        self.summary_source = None  # 目录 -> (流程, 运行记录) 或 None；主窗口设为计算页的 summary_source
        self.cache = {}            # 路径 -> (mtime, 数据)
        self.reader = index.ReadWorker()
        self.read_generation = 0
        self.read_stamp = None
        self.read_data = {}
        self.compositions = {}
        self.composition_token = None
        self.loading = False
        self.levels = {}           # key -> (文件标签, 路径, Level)
        self.key_by_id = {}        # (绝对路径, 2J, 宇称, 序号) -> key
        self.current = None
        self.zero_groups, self.energy_zeros, self.zero_warnings = {}, {}, []
        self.pair = None           # (上能级, 下能级)
        self.marked = []           # 图上标注的跃迁 [(上能级, 下能级)]，保存在 工作目录/.grasp_gui/marked.json
        self.plan = None           # (Workspace, 要算的跃迁类型)
        self.tr_by_pair = {}       # frozenset(能级, 能级) -> [Transition]
        self.tr_by_level = {}      # 能级 -> [(另一能级, Transition, 本能级是否为上能级)]
        self.runner = core.Runner(lambda text, tag: self.q.put((text, tag)))
        self.q = queue.Queue()
        self.running = None        # (Workspace, 跃迁类型)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(100)

        lay = QVBoxLayout(self)
        top = self.dir_bar(workdir)
        top.addSpacing(12)
        top.addWidget(QLabel(T("能量单位")))
        self.unit = QComboBox()
        for label, _, _, _ in UNITS:
            self.unit.addItem(T(label))
        self.unit.setCurrentIndex(min(max(core.load_config().get("level_unit", 0), 0), len(UNITS) - 1))
        self.unit.currentIndexChanged.connect(self.unit_changed)
        top.addWidget(self.unit)
        top.addWidget(QLabel(T("能量零点")))
        self.zero = QComboBox()
        self.zero.addItems([T("按计算组"), T("统一取最低能级")])
        self.zero.setCurrentIndex(min(max(core.load_config().get("level_zero", 0), 0), 1))
        self.zero.currentIndexChanged.connect(self.zero_changed)
        top.addWidget(self.zero)
        export = QPushButton(T("导出"))
        menu = QMenu(export)
        menu.addAction(T("能级图 (PNG / SVG / PDF)…")).triggered.connect(self.export_diagram)
        menu.addAction(T("能级数据 (CSV)…")).triggered.connect(self.export_levels)
        menu.addSeparator()
        menu.addAction(T("组态缩写…")).triggered.connect(lambda: AbbrevDialog.edit(self, self.preview_desc()))
        export.setMenu(menu)
        top.addWidget(export)
        top.addWidget(button("结果摘要…", self.open_summary))
        top.addSpacing(12)
        self.show_links = QCheckBox(T("显示选中能级的全部已算跃迁"))
        self.show_links.setChecked(bool(core.load_config().get("level_links", False)))
        self.show_links.toggled.connect(self.links_toggled)
        top.addWidget(self.show_links)
        top.addStretch()
        top.addWidget(help_button(T("鼠标停在能级上显示信息，点击查看组成；从一个能级拖到另一个能级 (或按住 Shift 点击) 计算跃迁，"
                                    "算好的跃迁会画成箭头，右键点箭头可移除；滚轮或拖动空白处上下平移，触控板捏合 (或 Ctrl + 滚轮) 缩放，双击复位。"
                                    "按住右键 (或 Ctrl + 左键) 拖出一个框可放大到框内的能量范围，单击右键或按 Backspace "
                                    "回到上一个视图。")))
        lay.addLayout(top)

        split = QSplitter(Qt.Horizontal)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        fl = QHBoxLayout()
        fl.addWidget(QLabel(T("混合系数文件")))
        fl.addStretch()
        fl.addWidget(help_button(T("文件按内容识别，与文件名无关：组态取自 CSF 数和 J^π 都对应的 CSF 文件，"
                                   "LSJ 组成取自能量对应的 jj2lsj 结果。"), None))
        ll.addLayout(fl)
        self.files = QListWidget()
        self.files.itemChanged.connect(self.redraw)
        ll.addWidget(self.files)
        split.addWidget(left)

        middle = QSplitter(Qt.Vertical)
        dw = QWidget()
        dl = QVBoxLayout(dw)
        dl.setContentsMargins(0, 0, 0, 0)
        self.diagram = LevelDiagram()
        self.diagram.levelClicked.connect(self.select)
        self.diagram.pairSelected.connect(self.connect_pair)
        self.diagram.arrowRemoved.connect(self.unmark)
        dl.addWidget(self.diagram, 1)
        middle.addWidget(dw)
        self.level_table = table([T("文件"), "J^π", T("序号"), T("能量"), T("总能量 (a.u.)"), T("纯度"),
                                  T("主要组成")])
        self.level_table.setSortingEnabled(True)
        self.level_table.setSelectionMode(QTableWidget.SingleSelection)
        self.level_table.cellClicked.connect(lambda row, _c: self.select(self.level_table.item(row, 0).data(Qt.UserRole)))
        middle.addWidget(self.level_table)
        middle.setSizes([560, 260])
        split.addWidget(middle)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        self.title = QLabel(T("点击能级查看组成"))
        self.title.setWordWrap(True)
        self.title.setTextFormat(Qt.RichText)
        rl.addWidget(self.title)
        fl = QHBoxLayout()
        fl.addWidget(QLabel(T("只显示权重 ≥")))
        self.min_w = QDoubleSpinBox()
        self.min_w.setDecimals(3)
        self.min_w.setRange(0.0, 100.0)
        self.min_w.setValue(0.1)
        self.min_w.setSuffix(" %")
        self.min_w.valueChanged.connect(self.show_composition)
        fl.addWidget(self.min_w)
        fl.addStretch()
        rl.addLayout(fl)
        self.comp_tabs = QTabWidget()
        self.jj_table = table(["#", "CSF", T("系数"), T("权重"), T("组态"), T("耦合 (中间 J、总 J^π)")])
        self.jj_table.setItemDelegateForColumn(3, WeightBar(self.jj_table))
        self.lsj_table = table([T("系数"), T("权重"), T("LSJ 标记")])
        self.lsj_table.setItemDelegateForColumn(1, WeightBar(self.lsj_table))
        self.comp_tabs.addTab(self.jj_table, T("jj 耦合 CSF"))
        self.comp_tabs.addTab(self.lsj_table, T("LSJ 组成"))
        self.tr_tab = self.build_transition_tab()
        self.comp_tabs.addTab(self.tr_tab, T("跃迁"))
        rl.addWidget(self.comp_tabs, 1)
        self.summary = QLabel()
        self.summary.setStyleSheet("color: gray;")
        rl.addWidget(self.summary)
        split.addWidget(right)
        split.setSizes([220, 660, 520])
        lay.addWidget(split, 1)
        self.scan()

    # ---- 文件 ----
    def files_checked(self):
        self.redraw()

    def scan(self):
        """列出混合系数文件；默认勾选 CI 结果 (.cm)，没有时勾选最新的 .m (保留已勾选的)。"""
        d = self.dir.text()
        # 重新扫描也要重新配对：可能只有 CSF / LSJ 候选发生变化。
        self.read_stamp = None
        checked = {self.files.item(i).data(Qt.UserRole) for i in range(self.files.count())
                   if self.files.item(i).checkState() == Qt.Checked}
        paths = [os.path.join(d, n) for n in mix.find_mix_files(d)]
        if not checked & set(paths) and paths:
            cm = [p for p in paths if p.endswith(".cm")]
            checked = set(cm) if cm else {max(paths, key=os.path.getmtime)}
        self.files.blockSignals(True)
        self.files.clear()
        for path in paths:
            it = QListWidgetItem(os.path.basename(path))
            it.setData(Qt.UserRole, path)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if path in checked else Qt.Unchecked)
            self.files.addItem(it)
        self.files.blockSignals(False)
        self.redraw()
        self.status(T("{n} 个混合系数文件", n=len(paths)) if paths
                                     else T("此目录中没有混合系数文件"))

    def cached(self, path, reader):
        try:
            mtime = index.stamp(path)
        except OSError as e:
            return e
        hit = self.cache.get((path, reader))
        if hit and hit[0] == mtime:
            return hit[1]
        try:
            data = reader(path)
        except (OSError, ValueError, StopIteration, struct.error) as e:
            data = e
        self.cache[(path, reader)] = (mtime, data)
        return data

    def load(self, path):
        """(blocks, csfs, CSF 文件, {(nb, pos): LSJ}, LSJ 文件)；没有对应文件时为 None / {}。
        CSF 列表和 LSJ 结果按内容配对，不依赖文件名。读不了混合系数文件时返回异常。"""
        return self.read_data.get(path)

    # ---- 能级图与能级列表 ----
    def unit_info(self):
        """(简写, 换算因子, 显示格式)"""
        _, short, factor, efmt = UNITS[self.unit.currentIndex()]
        return short, factor, efmt

    def unit_changed(self):
        core.save_config(level_unit=self.unit.currentIndex())    # 下次打开沿用
        self.redraw()

    def zero_changed(self):
        core.save_config(level_zero=self.zero.currentIndex())
        self.render_levels()

    def zero_for(self, key):
        return self.energy_zeros[self.zero_groups[self.levels[key][1]]]

    def redraw(self, *_):
        paths = [self.files.item(i).data(Qt.UserRole) for i in range(self.files.count())
                 if self.files.item(i).checkState() == Qt.Checked]
        saved = []
        for path in paths:
            try:
                saved.append(index.stamp(path))
            except OSError:
                saved.append([path, None, None])
            if path.endswith(".cm"):
                summary_path = os.path.splitext(path)[0] + ".csum"
                try:
                    saved.append(index.stamp(summary_path))
                except OSError:
                    saved.append([summary_path, None, None])
        stamp = self.dir.text(), saved
        if stamp != self.read_stamp:
            self.read_stamp = stamp
            self.read_generation += 1
            self.read_data = {}
            self.compositions = {}
            self.composition_token = None
            self.loading = bool(paths)
            self.reader.submit(("load", self.read_generation), read_level_files, paths, self.MAX_ROWS, self.dir.text())
        self.render_levels()

    def render_levels(self):
        unit_label, factor, efmt = self.unit_info()
        data = []
        for i in range(self.files.count()):
            it = self.files.item(i)
            if it.checkState() != Qt.Checked:
                continue
            path = it.data(Qt.UserRole)
            res = self.load(path)
            if res is None:
                continue
            if isinstance(res, Exception):
                it.setForeground(QColor("#c00"))
                it.setToolTip(T("无法读取：{e}", e=res))
                continue
            it.setForeground(QColor("black"))
            it.setToolTip(path)
            data.append((os.path.basename(path), path, *res))
        self.zero_groups, self.zero_warnings = {}, []
        for fname, path, blocks, *_ in data:
            fallback = dict(zero_group="DC", zero_warning="") if path.endswith(".m") else mix.unknown_hamiltonian()
            metadata = getattr(blocks, "hamiltonian", fallback)
            self.zero_groups[path] = metadata["zero_group"]
            if metadata["zero_warning"]:
                self.zero_warnings.append(fname)
        self.energy_zeros = mix.energy_zeros(
            ((self.zero_groups[path], lv.energy) for _, path, blocks, *_ in data for b in blocks for lv in b.levels),
            unified=bool(self.zero.currentIndex()))

        self.levels = {}
        self.key_by_id = {}
        self.workspaces = {}
        columns, rows = [], []
        for fname, path, blocks, csfs, csf_path, lsj, lsj_path in data:
            group = self.zero_groups[path]
            e0 = self.energy_zeros.get(group, 0.0)
            for b in blocks:
                col = dict(file=fname, title=self.jp(b), levels=[])
                for lv in b.levels:
                    key = (path, b.nb, lv.pos)
                    self.levels[key] = (fname, path, lv, csfs, lsj.get((b.nb, lv.pos)), csf_path, lsj_path)
                    self.key_by_id[(os.path.abspath(path), tr.two_j(b), b.parity, lv.pos)] = key
                    label, purity = self.leading(lv, csfs, lsj.get((b.nb, lv.pos)))
                    rel = (lv.energy - e0) * factor
                    info = (f"{fname}   J^π = {self.jp(b)}   #{lv.pos}\n"
                            + T("计算组：{group}", group=group) + "\n" +
                            f"{T('能量')}: {efmt.format(rel)} {unit_label}   ({lv.energy:.9f} a.u.)\n"
                            f"{T('主要组成')}: {label}  ({purity:.1f} %)")
                    col["levels"].append(dict(key=key, y=rel, label=label, color=PARITY_COLORS[b.parity], info=info))
                    rows.append((fname, self.jp(b), lv.pos, rel, lv.energy, purity, label, key))
                if col["levels"]:
                    columns.append(col)
        self.diagram.set_columns(columns, unit_label,
                                 energy_axis(unit_label, bool(self.zero.currentIndex()), len(self.energy_zeros) > 1))
        self.fill_levels(rows, unit_label, efmt)
        if self.current not in self.levels:
            self.current = None
        if self.pair and not all(k in self.levels for k in self.pair):
            self.pair = None
        self.diagram.selected = self.current
        self.diagram.pair = self.pair
        self.load_transitions()
        self.load_marked()
        self.show_composition()
        if self.zero_warnings:
            self.status(T("无法确定哈密顿量，零点可能不可比") + "：" + ", ".join(self.zero_warnings))

    @staticmethod
    def jp(block):
        return f"{block.J}{'⁺' if block.parity > 0 else '⁻'}"

    @staticmethod
    def leading(lv, csfs, lsj):
        """主要组成的标签和权重 (%)：有 LSJ 组成时用 LSJ 标记，否则用权重最大的 CSF。"""
        if lsj and lsj[1]:
            c, w, label = max(lsj[1], key=lambda t: t[1])
            return label, 100 * w
        i = lv.leading()
        c = lv._leading_coeff if hasattr(lv, "_leading_coeff") else lv.coeffs[i - 1]
        label = lv._leading_csf[0] if csfs and hasattr(lv, "_leading_csf") else (
            csfs[lv.block.nb - 1][i - 1][0] if csfs else f"CSF {i}")
        return label, 100 * c * c

    def fill_levels(self, rows, unit_label, efmt):
        t = self.level_table
        t.setSortingEnabled(False)
        t.setHorizontalHeaderItem(3, QTableWidgetItem(T("能量 ({u})", u=unit_label)))
        t.setRowCount(len(rows))
        for r, (fname, jp, pos, rel, e, purity, label, key) in enumerate(sorted(rows, key=lambda x: x[3])):
            first = item(fname)
            first.setData(Qt.UserRole, key)
            vals = [first, item(jp), item(pos), NumItem(efmt.format(rel), rel), NumItem(f"{e:.9f}", e),
                    NumItem(f"{purity:.1f}", purity), item(label)]
            for c, it in enumerate(vals):
                t.setItem(r, c, it)
        t.setSortingEnabled(True)
        t.resizeColumnsToContents()

    # ---- 组成 ----
    def select(self, key):
        if self.composition_token and self.composition_token[2] != key:
            self.reader.cancel.set()
            self.composition_token = None
        self.current = key
        self.diagram.selected = key
        self.diagram.links = self.links_of(key)
        self.diagram.update()
        for r in range(self.level_table.rowCount()):
            if self.level_table.item(r, 0).data(Qt.UserRole) == key:
                self.level_table.selectRow(r)
                break
        self.show_composition()

    def show_composition(self, *_):
        for t in (self.jj_table, self.lsj_table):
            t.setRowCount(0)
        if self.current is None:
            self.title.setText(T("点击能级查看组成"))
            self.summary.setText("")
            return
        fname, path, lv, csfs, lsj, csf_path, lsj_path = self.levels[self.current]
        unit_label, factor, efmt = self.unit_info()
        e0 = self.zero_for(self.current)
        self.title.setText(f"<b>{fname}</b> &nbsp; J<sup>π</sup> = {self.jp(lv.block)} &nbsp; #{lv.pos} &nbsp; "
                           f"{efmt.format((lv.energy - e0) * factor)} {unit_label} &nbsp; ({lv.energy:.9f} a.u.)")
        wmin = self.min_w.value() / 100
        if self.current not in self.compositions:
            token = ("composition", self.read_generation, self.current)
            if self.composition_token != token:
                self.composition_token = token
                self.reader.submit(token, read_composition, lv, csfs, self.MAX_ROWS)
            self.summary.setText(T("正在读取组成…"))
            return
        shown = [(i, c, orb, coup) for i, c, orb, coup in self.compositions[self.current] if c * c >= wmin]
        t = self.jj_table
        t.setRowCount(len(shown))
        names = csfs[lv.block.nb - 1] if csfs else None
        for r, (i, c, orb, coup) in enumerate(shown):
            w = item(f"{100 * c * c:.3f} %", align=Qt.AlignRight | Qt.AlignVCenter)
            w.setData(Qt.UserRole, c * c)
            for col, it in enumerate([item(r + 1), item(i), NumItem(f"{c:+.6f}", c), w, item(orb), item(coup)]):
                t.setItem(r, col, it)
        t.resizeColumnsToContents()
        total = sum(c * c for _, c, _, _ in shown)
        msg = T("显示 {n} / {m} 个 CSF，权重之和 {w:.3f} %", n=len(shown), m=len(lv.coeffs), w=100 * total)
        if names:
            msg += "  " + T("组态取自 {f}", f=os.path.basename(csf_path))
        else:
            msg += "  " + T("(找不到对应的 CSF 文件，只显示 CSF 序号)")
        self.summary.setText(msg)

        lt = self.lsj_table
        comps = [x for x in (lsj[1] if lsj else []) if x[1] >= wmin]
        lt.setRowCount(len(comps))
        for r, (c, wgt, label) in enumerate(comps):
            w = item(f"{100 * wgt:.3f} %", align=Qt.AlignRight | Qt.AlignVCenter)
            w.setData(Qt.UserRole, wgt)
            for col, it in enumerate([NumItem(f"{c:+.6f}", c), w, item(label)]):
                lt.setItem(r, col, it)
        lt.resizeColumnsToContents()
        self.comp_tabs.setTabEnabled(1, bool(lsj))
        self.comp_tabs.setTabToolTip(1, T("LSJ 组成取自 {f}", f=os.path.basename(lsj_path)) if lsj
                                     else T("没有对应的 jj2lsj 结果 (.lsj.lbl)"))
        self.show_transitions()

    # ---- 跃迁 ----
    def build_transition_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        self.tr_title = QLabel()
        self.tr_title.setWordWrap(True)
        self.tr_title.setTextFormat(Qt.RichText)
        lay.addWidget(self.tr_title)
        self.tr_plan = QLabel()
        self.tr_plan.setWordWrap(True)
        lay.addWidget(self.tr_plan)
        row = QHBoxLayout()
        self.tr_run = QPushButton(T("计算跃迁 (所选 J)"))
        self.tr_run.setToolTip(T("只算两个能级所在的 J^π，较快"))
        self.tr_run.clicked.connect(lambda: self.compute(full=False))
        self.tr_all = QPushButton(T("计算全部 J (MPI)"))
        self.tr_all.setToolTip(T("计算两个文件中全部 J^π 之间的跃迁 (用于寿命、分支比)，用 MPI 并行"))
        self.tr_all.clicked.connect(lambda: self.compute(full=True))
        self.tr_np = QSpinBox()
        self.tr_np.setRange(1, max(os.cpu_count() or 1, 1) * 4)
        cfg = core.load_config()
        if "tr_np" not in cfg:
            settings = self.settings()
            cfg["tr_np"] = core.default_mpi_np(settings["bindir"], settings["mpirun"])
            core.save_config(tr_np=cfg["tr_np"])
        self.tr_np.setValue(int(cfg["tr_np"]))
        self.tr_np.setToolTip(T("计算全部 J 时的 MPI 进程数") + "\n" +
                              T("默认按物理核数；超线程对 GRASP 帮助不大。"))
        self.tr_np.valueChanged.connect(lambda n: core.save_config(tr_np=n))
        self.tr_stop = QPushButton(T("停止"))
        self.tr_stop.clicked.connect(self.runner.stop)
        row.addWidget(self.tr_run)
        row.addWidget(self.tr_all)
        row.addStretch()
        lay.addLayout(row)
        # 按钮分两行：一行放不下时右栏的最小宽度很大，分隔条往右拖要多拖一半最小宽度才能收起
        row = QHBoxLayout()
        row.addWidget(QLabel(T("进程数")))
        row.addWidget(self.tr_np)
        row.addWidget(self.tr_stop)
        tr_export = QPushButton(T("导出"))
        menu = QMenu(tr_export)
        menu.addAction(T("当前能级的跃迁 (CSV)…")).triggered.connect(lambda: self.export_transitions(False))
        menu.addAction(T("这两个文件之间的全部已算跃迁 (CSV)…")).triggered.connect(lambda: self.export_transitions(True))
        menu.addAction(T("保存为 GRASP 跃迁文件 (供 rtabtrans1 / rtabtrans2 使用)…")).triggered.connect(self.export_grasp)
        tr_export.setMenu(menu)
        row.addWidget(tr_export)
        row.addStretch()
        lay.addLayout(row)
        self.pair_table = table([T("类型"), T("规范"), "A (s⁻¹)", "gf", "S (a.u.)"])
        lay.addWidget(self.pair_table, 1)
        mrow = QHBoxLayout()
        marked = QLabel(T("图上标注的跃迁"))
        marked.setWordWrap(True)
        mrow.addWidget(marked, 1)
        for text, slot in [("移除", self.unmark_selected), ("全部清除", self.clear_marked)]:
            b = QPushButton(T(text))
            b.clicked.connect(slot)
            mrow.addWidget(b)
        lay.addLayout(mrow)
        self.marked_list = QListWidget()
        self.marked_list.setMaximumHeight(96)
        self.marked_list.itemClicked.connect(lambda it: self.select_pair(*it.data(Qt.UserRole)))
        lay.addWidget(self.marked_list)
        self.level_tr_label = QLabel()
        self.level_tr_label.setWordWrap(True)
        lay.addWidget(self.level_tr_label)
        self.level_tr_table = table([T("另一能级"), "", T("类型"), "λ (Å)", "A (s⁻¹)", "gf", T("分支比")])
        self.level_tr_table.cellClicked.connect(self.level_tr_clicked)
        lay.addWidget(self.level_tr_table, 2)
        self.tr_log = QPlainTextEdit()
        self.tr_log.setReadOnly(True)
        self.tr_log.setMaximumBlockCount(3000)
        mono = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        mono.setPointSizeF(self.font().pointSizeF())
        self.tr_log.setFont(mono)
        self.tr_log.setPlaceholderText(T("GRASP 程序的输出"))
        lay.addWidget(self.tr_log, 1)
        # 放在滚动区中：否则这一页的最小尺寸 (约 300 × 600) 决定了右栏和整个窗口的最小尺寸，
        # 屏幕较小 (如 150% 缩放的 1080p) 时窗口最大化也放不下，分隔条往右拖也要多拖很远才收起
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QScrollArea.NoFrame)
        area.setWidget(w)
        return area

    # ---- 导出 ----
    def level_desc(self, key):
        """导出用的能级信息：组态 [(nl, 占据数)]、谱项 (有 jj2lsj 结果时)、J、宇称等。组态和谱项取自
        权重最大的 LSJ 成分；没有 LSJ 结果时组态由权重最大的 CSF 合并 2p-、2p 等得到，没有谱项。"""
        fname, path, lv, csfs, lsj, csf_path, lsj_path = self.levels[key]
        label, purity = self.leading(lv, csfs, lsj)
        config = term = None
        lsj_label = ""
        if lsj and lsj[1]:
            lsj_label = max(lsj[1], key=lambda t: t[1])[2]
            config, term = mix.parse_lsj_label(lsj_label)
        jj = lv._leading_csf[0] if csfs else ""
        if config is None and jj:
            config = mix.nonrel_config(jj)
        # LSJ 标记和 CSF 中不含核心轨道，补上后再按规则缩写 (如 1s2.2s2.2p6 -> [Ne])
        core_orbs = self.cached(csf_path, mix.csf_core) if csf_path else []
        core_orbs = [] if isinstance(core_orbs, Exception) else core_orbs
        full = [x for x in core_orbs if x[0] not in {nl for nl, _ in config or []}] + list(config or [])
        prefix, rest = mix.abbreviate(full, abbrev_rules())
        return dict(file=fname, J=lv.block.J, twoj=tr.two_j(lv.block), parity=lv.parity, pos=lv.pos, energy=lv.energy,
                    config=rest, prefix=prefix, full=full, term=term, purity=purity, lsj_label=lsj_label, jj=jj)

    def preview_desc(self):
        """缩写设置对话框中用来预览的能级：当前选中的，否则能量最低的。"""
        key = self.current or (min(self.levels, key=lambda k: self.levels[k][2].energy) if self.levels else None)
        return (lambda: self.level_desc(key)) if key else None

    @staticmethod
    def rich_label(d, energy=None):
        """能级图上的标注，如 2s²2p⁶3s3p ¹P°₁；没有谱项时写 J^π，如 2s²2p⁶3s3p  1⁻。"""
        segs = [(d["prefix"], "n")] if d["prefix"] else []
        for nl, occ in d["config"]:
            segs.append((nl, "n"))
            if occ > 1:
                segs.append((str(occ), "sup"))
        if not d["config"] and not d["prefix"]:
            segs.append((f"#{d['pos']}", "n"))
        if d["term"]:
            segs += [(" ", "n"), (d["term"][:-1], "sup"), (d["term"][-1], "n")]
            if d["parity"] < 0:
                segs.append(("o", "sup"))
            segs.append((d["J"], "sub"))
        else:
            segs += [("  " + d["J"], "n"), ("−" if d["parity"] < 0 else "+", "sup")]
        if energy:
            segs.append(("   " + energy, "n"))
        return segs

    def export_path(self, title, name, filters):
        path, filt = QFileDialog.getSaveFileName(self, title, os.path.join(self.dir.text(), name), filters)
        if path and not os.path.splitext(path)[1]:           # 没写扩展名时按所选的类型补上
            path += filt[filt.index("*") + 1:filt.index(")")].split()[0]
        return path

    def export_diagram(self):
        if not self.levels:
            return
        opts = DiagramExportDialog.ask(self, bool(self.marked))
        if not opts:
            return
        path = self.export_path(T("导出能级图…"), "levels.pdf", "PDF (*.pdf);;SVG (*.svg);;PNG (*.png)")
        if not path:
            return
        unit_label, factor, efmt = self.unit_info()
        visible = {lv["key"] for c in self.diagram.columns for lv in c["levels"]}
        labels = {}
        if opts["labels"] or opts["energy"]:
            for key in visible:
                d = self.level_desc(key)
                e = efmt.format((d["energy"] - self.zero_for(key)) * factor) if opts["energy"] else None
                labels[key] = self.rich_label(d, e) if opts["labels"] else [(e, "n")]
        axis = energy_axis(unit_label, bool(self.zero.currentIndex()), len(self.energy_zeros) > 1, opts["english"])
        arrows = []
        if opts["transitions"]:
            for u, l in self.marked:
                lam = self.wavelength(u, l)
                text = {"none": "", "A": f"{lam:.2f} Å", "nm": f"{lam / 10:.3f} nm"}[opts["arrow_text"]]
                arrows.append((u, l, text))
        export = dict(labels=labels, arrows=arrows, axis=axis)
        w, h = self.diagram.export_size(opts["height"], labels)
        ext = os.path.splitext(path)[1].lower()
        try:
            if ext == ".svg":
                from PySide6.QtSvg import QSvgGenerator
                gen = QSvgGenerator()
                gen.setFileName(path)
                gen.setSize(QSize(w, h))
                gen.setViewBox(QRect(0, 0, w, h))
                gen.setTitle("Energy levels")
                p = QPainter(gen)
                self.diagram.draw(p, w, h, export)
                p.end()
            elif ext == ".pdf":
                from PySide6.QtCore import qVersion
                from PySide6.QtGui import QPageSize, QPdfWriter
                if qVersion().startswith(("6.8.0", "6.8.1", "6.8.2")):
                    QMessageBox.warning(self, T("导出能级图"), T("Qt {v} 写出的 PDF 文件信息有错，部分阅读器会报错。"
                                                             "请升级 PySide6 (≥ 6.8.3)，或导出为 SVG / PNG。", v=qVersion()))
                wr = QPdfWriter(path)
                wr.setResolution(96)                  # 1 像素 = 1/96 英寸，与屏幕上的字号比例一致
                wr.setPageSize(QPageSize(QSizeF(w * 72 / 96, h * 72 / 96), QPageSize.Point))
                wr.setPageMargins(QMarginsF(0, 0, 0, 0))
                wr.setTitle("Energy levels")
                p = QPainter(wr)
                self.diagram.draw(p, w, h, export)
                p.end()
            else:
                scale = opts["scale"]
                img = QImage(w * scale, h * scale, QImage.Format_ARGB32)
                img.fill(QColor("white"))
                p = QPainter(img)
                p.scale(scale, scale)
                self.diagram.draw(p, w, h, export)
                p.end()
                img.setDotsPerMeterX(round(3779.53 * scale))      # 记录分辨率 (96 dpi × 倍数)
                img.setDotsPerMeterY(round(3779.53 * scale))
                if not img.save(path):
                    raise OSError(path)
        except OSError as e:
            QMessageBox.critical(self, T("错误"), T("无法写入 {p}", p=e))
            return
        self.status(T("已导出 {p}", p=path))

    LEVEL_HEADER = ["file", "J", "2J", "parity", "index", "configuration", "configuration_full", "term", "E_total_au", "E_rel_au",
                    "E_rel_cm-1", "E_rel_eV", "purity_percent", "leading_component", "lsj_label", "leading_csf_jj",
                    "zero_group", "E_zero_au"]

    def open_summary(self):
        """结果摘要 (给 agent)：本目录的能级、层间变化、收敛情况等；完整配置是可选的附加部分。"""
        from summary_panel import SummaryDialog
        d = self.dir.text()
        source = self.summary_source(d) if self.summary_source else None
        if source is None:
            try:
                data, _ = sessions.read(d)
            except (OSError, ValueError):
                data = None
            source = ((data or {}).get("pipeline", []), [])
        jobs, history = source
        cfg = grasp_config.export(jobs, dict(title=os.path.basename(os.path.normpath(d)))) if jobs else None
        dlg = SummaryDialog(self, d, cfg, jobs, history)
        if dlg.exec() == QDialog.Accepted:
            self.message.emit(T("已保存结果摘要：{p}", p=dlg.saved_path) if getattr(dlg, "saved_path", None)
                              else T("已复制结果摘要"))

    def export_levels(self):
        if not self.levels:
            return
        path = self.export_path(T("导出能级数据…"), "levels.csv", "CSV (*.csv)")
        if not path:
            return
        rows = []
        for key in sorted(self.levels, key=lambda k: self.levels[k][2].energy):
            d = self.level_desc(key)
            e0 = self.zero_for(key)
            rel = d["energy"] - e0
            leading = self.leading(self.levels[key][2], self.levels[key][3], self.levels[key][4])[0]
            rows.append([d["file"], d["J"], d["twoj"], "+" if d["parity"] > 0 else "-", d["pos"],
                         config_text(d), mix.config_ascii(d["full"]), mix.term_ascii(d["term"], d["parity"]), f"{d['energy']:.10f}",
                         f"{rel:.10f}", f"{rel * mix.HARTREE_CM:.4f}", f"{rel * mix.HARTREE_EV:.6f}",
                         f"{d['purity']:.3f}", leading, d["lsj_label"], d["jj"],
                         self.zero_groups[self.levels[key][1]], f"{e0:.10f}"])
        self.write_csv(path, self.LEVEL_HEADER, rows)

    def write_csv(self, path, header, rows):
        try:
            core.write_csv(path, header, rows)
        except OSError as e:
            QMessageBox.critical(self, T("错误"), T("无法写入 {p}", p=e))
            return
        self.status(T("已导出 {p} ({n} 行)", p=path, n=len(rows)))

    TRANSITION_HEADER = (["type"] + [f"{s}_{c}" for s in ("upper", "lower") for c in
                                     ("file", "J", "parity", "index", "configuration", "term", "E_total_au")] +
                         ["delta_E_cm-1", "lambda_vac_A", "gauge", "A_s-1", "gf", "S_au",
                          "A_Coulomb_s-1", "gf_Coulomb", "S_Coulomb_au", "A_C/A_B", "branching_ratio"])

    def transition_row(self, ku, kl, t):
        def level(key):
            d = self.level_desc(key)
            return [d["file"], d["J"], "+" if d["parity"] > 0 else "-", d["pos"], config_text(d),
                    mix.term_ascii(d["term"], d["parity"]), f"{d['energy']:.10f}"]
        a, gf, s = t.main()
        c = t.values.get("C")
        decay = sum(abs(x.main()[0]) for _, x, is_up in self.tr_by_level.get(ku, []) if is_up)
        ratio = t.gauge_ratio()
        return ([t.kind] + level(ku) + level(kl) +
                [f"{t.energy_cm:.2f}", f"{t.wavelength:.4f}", "Babushkin" if "B" in t.values else "magnetic",
                 f"{a:.6e}", f"{gf:.6e}", f"{s:.6e}"] +
                ([f"{x:.6e}" for x in c] if c else ["", "", ""]) +
                [f"{ratio:.6f}" if ratio is not None else "", f"{abs(a) / decay:.6f}" if decay else ""])

    def export_transitions(self, both_files):
        """当前能级的已算跃迁，或选中的一对能级所在的两个文件之间的全部已算跃迁。"""
        if both_files:
            if not self.pair:
                return
            files = {self.levels[k][1] for k in self.pair}
            rows = [(ku, kl, t) for ku, items in self.tr_by_level.items() if self.levels[ku][1] in files
                    for kl, t, is_up in items if is_up and self.levels[kl][1] in files]
            name = "_".join(os.path.splitext(self.levels[k][0])[0] for k in self.pair) + "_transitions.csv"
        else:
            if not self.current:
                return
            rows = [((self.current, other) if is_up else (other, self.current)) + (t,)
                    for other, t, is_up in self.tr_by_level.get(self.current, [])]
            d = self.level_desc(self.current)
            name = f"{os.path.splitext(d['file'])[0]}_{d['J'].replace('/', '_')}{'+' if d['parity'] > 0 else '-'}_{d['pos']}.csv"
        if not rows:
            QMessageBox.information(self, T("导出"), T("还没有算出的跃迁。"))
            return
        path = self.export_path(T("导出跃迁数据…"), name, "CSV (*.csv)")
        if not path:
            return
        rows.sort(key=lambda r: (tr.kind_key(r[2].kind), -self.levels[r[0]][2].energy, -self.levels[r[1]][2].energy))
        self.write_csv(path, self.TRANSITION_HEADER, [self.transition_row(ku, kl, t) for ku, kl, t in rows])

    def export_grasp(self):
        """把选中的一对能级所在两个文件之间的已算跃迁写成 GRASP 的 rtransition 输出 (如 even4.odd4.ct)。"""
        if not self.pair:
            return
        ws = self.workspace(self.levels[self.pair[0]][1], self.levels[self.pair[1]][1])
        transitions = ws.results()
        if not transitions:
            QMessageBox.information(self, T("导出"), T("还没有算出的跃迁。"))
            return
        names = {r: os.path.splitext(os.path.basename(x.mix))[0] for r, x in ws.sides.items()}
        ci = all(x.mix.endswith(".cm") for x in ws.sides.values())
        name = ".".join([names["a"], names.get("b", names["a"])]) + (".ct" if ci else ".t")
        runs = ws.manifest().get("runs", {})
        kinds = sorted({t.kind for t in transitions}, key=tr.kind_key)
        partial = [k for k in kinds if k not in runs.get("all", {}).get("kinds", {})]
        if partial and QMessageBox.question(
                self, T("导出"), T("{k} 只算了部分 J，文件中只有这些跃迁；rtabtrans2 算出的寿命只计入它们。"
                                   "先“计算全部 J”可以得到完整的结果。仍然导出吗？", k=", ".join(partial))) != QMessageBox.Yes:
            return
        path, _ = QFileDialog.getSaveFileName(self, T("保存为 GRASP 跃迁文件…"), os.path.join(self.dir.text(), name),
                                              "GRASP (*.ct *.t);;" + T("所有文件") + " (*)")
        if not path:
            return
        try:
            tr.write_grasp(path, transitions, names)
        except OSError as e:
            QMessageBox.critical(self, T("错误"), T("无法写入 {p}", p=e))
            return
        self.status(T("已导出 {p} ({n} 条跃迁)", p=path, n=len(transitions)))

    def level_name(self, key):
        fname, _, lv, *_ = self.levels[key]
        return f"{fname}  {self.jp(lv.block)}  #{lv.pos}"

    # ---- 图上标注的跃迁 ----
    def marked_file(self):
        return os.path.join(self.dir.text(), ".grasp_gui", "marked.json")

    def level_id(self, key):
        lv = self.levels[key][2]
        return [os.path.abspath(self.levels[key][1]), tr.two_j(lv.block), lv.parity, lv.pos]

    def load_marked(self):
        """读取工作目录中保存的标注；对应的能级不在当前勾选的文件中时暂不显示 (仍保留在文件中)。"""
        try:
            with open(self.marked_file()) as f:
                saved = json.load(f)
        except (OSError, ValueError):
            saved = []
        self.marked_saved = saved
        self.marked = []
        for u, l in saved:
            ku, kl = self.key_by_id.get(tuple(u)), self.key_by_id.get(tuple(l))
            if ku is not None and kl is not None:
                self.marked.append((ku, kl))
        self.update_arrows()

    def save_marked(self):
        shown = [[self.level_id(u), self.level_id(l)] for u, l in self.marked]
        hidden = [x for x in getattr(self, "marked_saved", []) if tuple(x[0]) not in self.key_by_id
                  or tuple(x[1]) not in self.key_by_id]
        self.marked_saved = shown + hidden
        try:
            os.makedirs(os.path.dirname(self.marked_file()), exist_ok=True)
            with open(self.marked_file(), "w") as f:
                json.dump(self.marked_saved, f)
        except OSError:
            pass

    def wavelength(self, ku, kl):
        """真空波长 (Å)：有计算结果时用 rtransition 的跃迁能量，否则用两个能级的能量差。"""
        ts = self.tr_by_pair.get(frozenset((ku, kl)))
        if ts:
            return ts[0].wavelength
        de = (self.levels[ku][2].energy - self.levels[kl][2].energy) * mix.HARTREE_CM
        return 1e8 / de if de > 0 else float("inf")

    def update_arrows(self):
        self.diagram.arrows = [(u, l, f"{self.wavelength(u, l):.2f} Å") for u, l in self.marked]
        self.diagram.update()
        self.marked_list.clear()
        for u, l in self.marked:
            it = QListWidgetItem(f"{self.level_name(u)}  →  {self.level_name(l)}   λ = {self.wavelength(u, l):.3f} Å")
            it.setData(Qt.UserRole, (u, l))
            self.marked_list.addItem(it)

    def mark(self, ku, kl):
        if (ku, kl) not in self.marked:
            self.marked.append((ku, kl))
            self.save_marked()
            self.update_arrows()

    def unmark(self, ku, kl):
        if (ku, kl) in self.marked:
            self.marked.remove((ku, kl))
            self.save_marked()
            self.update_arrows()
            self.status(T("已从图上移除 {u} → {l}", u=self.level_name(ku), l=self.level_name(kl)))

    def unmark_selected(self):
        it = self.marked_list.currentItem()
        if it:
            self.unmark(*it.data(Qt.UserRole))

    def clear_marked(self):
        self.marked = []
        self.save_marked()
        self.update_arrows()

    def connect_pair(self, k1, k2):
        """在图上用鼠标连了两个能级：选中这一对；已经算好时直接在图上画出箭头。"""
        self.select_pair(k1, k2)
        if self.tr_by_pair.get(frozenset(self.pair)):
            self.mark(*self.pair)

    def select_pair(self, k1, k2):
        """连上两个能级：能量高者为上能级。"""
        e1, e2 = self.levels[k1][2].energy, self.levels[k2][2].energy
        self.pair = (k1, k2) if e1 >= e2 else (k2, k1)
        self.diagram.pair = self.pair
        self.comp_tabs.setCurrentWidget(self.tr_tab)
        self.select(self.pair[0])          # 组成页和已算跃迁列表显示上能级

    def workspace(self, path1, path2):
        k = tuple(sorted((os.path.abspath(path1), os.path.abspath(path2))))
        if k not in self.workspaces:
            self.workspaces[k] = tr.Workspace(self.dir.text(), *k)
        return self.workspaces[k]

    def load_transitions(self):
        """读取工作目录中已算好的跃迁，按能级建立索引。"""
        self.tr_by_pair, self.tr_by_level = {}, {}
        for roles, transitions in tr.computed(self.dir.text()):
            for t in transitions:
                ku = self.key_by_id.get((roles.get(t.upper[0]),) + t.upper[1:])
                kl = self.key_by_id.get((roles.get(t.lower[0]),) + t.lower[1:])
                if ku is None or kl is None:          # 文件没有勾选
                    continue
                self.tr_by_pair.setdefault(frozenset((ku, kl)), []).append(t)
                self.tr_by_level.setdefault(ku, []).append((kl, t, True))
                self.tr_by_level.setdefault(kl, []).append((ku, t, False))
        self.diagram.links = self.links_of(self.current)
        self.diagram.update()

    def links_toggled(self, on):
        core.save_config(level_links=on)
        self.diagram.links = self.links_of(self.current)
        self.diagram.update()

    def links_of(self, key):
        """能级图上要画的已算跃迁 (勾选“显示全部已算跃迁”时)：线的粗细按 A 的对数
        (最强的为 1，弱 6 个量级以上的为 0)。"""
        if not self.show_links.isChecked():
            return []
        items = self.tr_by_level.get(key, [])
        best = {}
        for other, t, _ in items:
            a = abs(t.main()[0])
            best[other] = max(best.get(other, 0.0), a)
        top = max(best.values(), default=0.0)
        if top <= 0:
            return []
        return [(key, other, min(max((math.log10(a) - math.log10(top) + 6) / 6, 0.0), 1.0) if a > 0 else 0.0)
                for other, a in best.items()]

    def show_transitions(self):
        self.show_pair()
        self.show_level_transitions()

    def show_pair(self):
        busy = self.running is not None
        self.tr_stop.setEnabled(busy)
        self.pair_table.setRowCount(0)
        self.plan = None
        if not self.pair:
            self.tr_title.setText(T("从一个能级拖到另一个能级 (或选中一个能级后按住 Shift 点击另一个)，计算两者之间的跃迁。"))
            self.tr_plan.setText("")
            self.tr_run.setEnabled(False)
            self.tr_all.setEnabled(False)
            return
        up, low = self.pair
        lu, ll = self.levels[up][2], self.levels[low][2]
        unit_label, factor, efmt = self.unit_info()
        de = lu.energy - ll.energy
        lam = 1e8 / (de * mix.HARTREE_CM) if de > 0 else float("inf")
        self.tr_title.setText(f"<b>{self.level_name(up)}</b> → <b>{self.level_name(low)}</b><br>"
                              f"ΔE = {efmt.format(de * factor)} {unit_label} &nbsp; λ = {lam:.3f} Å")
        kinds = tr.multipoles(tr.two_j(lu.block), lu.parity, tr.two_j(ll.block), ll.parity)
        rows = sorted(self.tr_by_pair.get(frozenset(self.pair), []), key=lambda t: tr.KINDS.index(t.kind)
                      if t.kind in tr.KINDS else 99)
        self.fill_pair(rows)
        self.tr_run.setEnabled(False)
        self.tr_all.setEnabled(False)
        if not kinds:
            self.tr_plan.setText(T("J = 0 → J = 0：单光子跃迁严格禁戒。"))
            return
        self.tr_all.setEnabled(False)
        ws = self.workspace(self.levels[up][1], self.levels[low][1])
        ids = [(ws.role_of(self.levels[k][1]), tr.two_j(lv.block), lv.parity) for k, lv in ((up, lu), (low, ll))]
        missing = ws.missing_kinds(ids[0], ids[1], kinds)
        all_run = ws.manifest().get("runs", {}).get("all", {}).get("kinds", {})
        missing_all = [k for k in kinds if k not in all_run]
        problems = ws.problems() if missing or missing_all else []
        if problems:
            text = []
            for what, where in problems:
                if what == "isodata":
                    text.append(T("工作目录中没有 isodata：{d}", d=where))
                elif what == "w?":
                    text.append(T("无法确定与 {f} 对应的径向波函数文件，候选：{c}。请把对应的 .w 文件命名为与混合系数文件同名 "
                                  "(如 {stem}.w)。", f=os.path.basename(where[0]),
                                  c=", ".join(os.path.basename(c) for c in where[1]),
                                  stem=os.path.splitext(os.path.basename(where[0]))[0]))
                else:
                    text.append(T("找不到与 {f} 对应的 {what} 文件", f=os.path.basename(where),
                                  what=".c" if what == "CSF" else ".w"))
            self.tr_plan.setText("\n".join(text))
            return
        jps = sorted({self.jp(lu.block), self.jp(ll.block)})
        if not missing:
            text = T("已算出 {k}。结果保存在 {d}", k=", ".join(kinds), d=ws.dir)
        else:
            note = T("两个能级来自同一个文件，轨道相同，不需要双正交变换。") if ws.same else \
                T("两个能级来自不同的计算，先用 rbiotransform 做双正交变换。")
            files = "; ".join(f"{os.path.basename(x.mix)}: {os.path.basename(x.csf)}, {os.path.basename(x.w)}"
                              for x in ws.sides.values())
            text = (T("需要计算 {k}。", k=", ".join(missing)) + " " + note + "\n" + T("使用的文件：{f}", f=files) + "\n" +
                    T("“所选 J”只算 {j} 这些对称性之间的跃迁；“全部 J”算两个文件中所有能级之间的跃迁，用于寿命和分支比。",
                      j=", ".join(jps)))
        self.tr_plan.setText(text)
        self.plan = (ws, kinds, ws.subset_for(ids), missing, missing_all)
        self.tr_run.setEnabled(bool(missing) and not busy)
        self.tr_all.setEnabled(bool(missing_all) and not busy)

    def fill_pair(self, rows):
        t = self.pair_table
        out = []
        for x in rows:
            for g in ("B", "C", "M"):
                if g in x.values:
                    a, gf, s = x.values[g]
                    gauge = {"B": T("Babushkin (长度)"), "C": T("Coulomb (速度)"), "M": ""}[g]
                    out.append((x.kind, gauge, a, gf, s))
        t.setRowCount(len(out))
        for r, (kind, gauge, a, gf, s) in enumerate(out):
            for c, it in enumerate([item(kind), item(gauge), NumItem(f"{a:.5e}", a), NumItem(f"{gf:.5e}", gf),
                                    NumItem(f"{s:.5e}", s)]):
                t.setItem(r, c, it)
        t.resizeColumnsToContents()
        ratios = [f"{x.kind}: A_C / A_B = {x.gauge_ratio():.4f}" for x in rows if x.gauge_ratio() is not None]
        if ratios:
            t.setToolTip(T("两种规范之比接近 1 说明计算较可靠。") + "\n" + "\n".join(ratios))

    def show_level_transitions(self):
        t = self.level_tr_table
        t.setRowCount(0)
        key = self.current
        items = self.tr_by_level.get(key, []) if key else []
        if not key:
            self.level_tr_label.setText("")
            return
        if not items:
            self.level_tr_label.setText(T("能级 {l} 还没有算出的跃迁。", l=self.level_name(key)))
            return
        decay = sum(abs(x.main()[0]) for _, x, is_up in items if is_up)
        rows = sorted(items, key=lambda i: -abs(i[1].main()[0]))
        t.setSortingEnabled(False)
        t.setRowCount(len(rows))
        for r, (other, x, is_up) in enumerate(rows):
            a, gf, _ = x.main()
            br = abs(a) / decay if is_up and decay else None
            first = item(self.level_name(other))
            first.setData(Qt.UserRole, other)
            vals = [first, item("↓" if is_up else "↑"), item(x.kind), NumItem(f"{x.wavelength:.3f}", x.wavelength),
                    NumItem(f"{a:.4e}", a), NumItem(f"{gf:.4e}", gf),
                    NumItem(f"{100 * br:.2f} %" if br is not None else "", br or 0.0)]
            for c, it in enumerate(vals):
                t.setItem(r, c, it)
        t.setSortingEnabled(True)
        t.resizeColumnsToContents()
        msg = T("能级 {l} 的已算跃迁 ({n} 条；↓ 衰变到更低能级，↑ 从更低能级激发)。", l=self.level_name(key), n=len(rows))
        if decay:
            msg += " " + T("已算衰变的总速率 {a:.4e} s⁻¹，对应寿命 {tau:.4e} s (只计入已算的跃迁)。", a=decay, tau=1 / decay)
        self.level_tr_label.setText(msg)

    def level_tr_clicked(self, row, _col):
        other = self.level_tr_table.item(row, 0).data(Qt.UserRole)
        if self.current and other in self.levels:
            self.select_pair(self.current, other)

    def mpi_programs(self, cfg):
        """MPI 版本能用时返回 None，否则返回原因。"""
        launcher = shlex.split(cfg["mpirun"] or "mpirun")[:1]
        if not launcher or not shutil.which(launcher[0]):
            return T("找不到 {p}", p=cfg["mpirun"] or "mpirun")
        for prog in ("rbiotransform_mpi", "rtransition_mpi"):
            if not (os.access(os.path.join(cfg["bindir"], prog), os.X_OK) or shutil.which(prog)):
                return T("找不到 {p}", p=prog)
        return None

    def compute(self, full=False):
        """计算选中的一对能级之间的跃迁：默认只算两个能级所在的 J^π (用主窗口的进程数)；
        full 时算两个文件的全部 J^π，用 MPI (进程数见跃迁页)。"""
        if not self.plan or self.running:
            return
        ws, kinds, subset, missing, missing_all = self.plan
        if full:
            subset, kinds = None, missing_all
        else:
            kinds = missing
        cfg = dict(self.settings())
        if full:
            cfg["np"] = self.tr_np.value()
        self.tr_log.clear()
        try:
            rid = ws.prepare(subset)
        except (OSError, ValueError) as e:
            self.tr_log.appendPlainText(T("\n*** 错误: {e}\n", e=e))
            return
        cfg["cwd"] = ws.run_dir(rid)
        if cfg["np"] > 1:
            why = None if ws.mpi_ok(cfg["mpitmp"], subset) else \
                T("计算目录的路径超过 {n} 个字符，GRASP 的 MPI 版本无法处理", n=tr.MPI_PATH_MAX)
            why = why or self.mpi_programs(cfg)
            if why:
                cfg["np"] = 1
                self.tr_log.appendPlainText(why + T("，改用串行版本。"))
        jobs = ws.jobs(rid, kinds, {x.key: x for x in core.STEPS})
        self.running = (ws, rid, kinds)
        what = T("全部 J") if subset is None else T("所选 J")
        self.status(T("正在计算 {k} 跃迁 ({w}，{n} 个进程) …", k=", ".join(kinds), w=what, n=cfg["np"]))
        self.show_pair()
        self.runner.stopped = False
        threading.Thread(target=self.runner.run_all, daemon=True,
                         args=(jobs, cfg, lambda ok: self.q.put((ok, "done")))).start()

    def poll(self):
        try:
            while True:
                kind, token, result = self.reader.events.get_nowait()
                if token[1] != self.read_generation:
                    continue
                if token[0] == "composition" and (token != self.composition_token or token[2] != self.current):
                    continue
                if kind == "progress":
                    path, fraction = result
                    self.status(T("正在读取 {f}… {p}%", f=os.path.basename(path), p=int(100 * fraction)))
                elif kind == "error":
                    self.loading = False
                    self.status(T("无法读取：{e}", e=result))
                    if token[0] == "composition" and token == self.composition_token:
                        self.summary.setText(T("无法读取：{e}", e=result))
                elif token[0] == "load":
                    self.loading = False
                    self.read_data = result
                    self.render_levels()
                    if not self.zero_warnings:
                        self.status(T("读入完成"))
                elif token == self.composition_token:
                    self.compositions = {token[2]: result}
                    self.show_composition()
        except queue.Empty:
            pass
        text = []
        try:
            while True:
                s, tag = self.q.get_nowait()
                if tag == "done":
                    if text:
                        self.tr_log.insertPlainText("".join(text))
                        text = []
                    self.finished(s)
                else:
                    text.append(s)
        except queue.Empty:
            pass
        if text:
            self.tr_log.moveCursor(QTextCursor.End)
            self.tr_log.insertPlainText("".join(text))
            self.tr_log.ensureCursorVisible()

    def finished(self, ok):
        ws, rid, kinds = self.running
        self.running = None
        if ok:
            try:
                ws.finish(rid, kinds)
            except (OSError, RuntimeError) as e:
                ok = False
                self.tr_log.appendPlainText(T("\n*** 错误: {e}\n", e=e))
        self.status(T("跃迁计算完成") if ok else T("跃迁计算失败，见下方的程序输出"))
        self.load_transitions()
        if ok and self.pair and self.tr_by_pair.get(frozenset(self.pair)):
            self.mark(*self.pair)               # 算好后在图上画出这条跃迁
        self.show_transitions()

    def busy(self):
        return bool(self.running)

    def shutdown(self):
        self.timer.stop()
        self.read_generation += 1
        self.reader.close()
        if self.running:
            self.runner.kill()
