"""
径向波函数查看器：读取工作目录中的 GRASP 径向波函数文件 (*.w)，列出轨道性质，并绘制
P(r)、Q(r) 和径向密度 P² + Q²；可叠加多个文件比较 (如 odd2.w … odd6.w)。

绘图用 Qt 自绘 (QPainter)，不依赖 matplotlib。
"""

import bisect
import csv
import math
import os
import struct

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QLabel, QMessageBox,
                               QListWidget, QListWidgetItem, QPushButton, QSplitter, QTableWidget,
                               QTableWidgetItem, QToolTip, QVBoxLayout, QWidget)

import grasp_wfn as wfn
from grasp_core import T
from gui_common import Panel, help_button

# 轨道配色 (同一轨道在各图中颜色相同)，文件用线型区分
COLORS = ["#1f77b4", "#d62728", "#2ca02c", "#ff7f0e", "#9467bd", "#8c564b", "#e377c2", "#17becf",
          "#7f7f7f", "#bcbd22"]
STYLES = [Qt.SolidLine, Qt.DashLine, Qt.DotLine, Qt.DashDotLine, Qt.DashDotDotLine]

QUANTITIES = [("P", "P(r)"), ("Q", "Q(r)"), ("D", "P² + Q²")]
X_MODES = [("r", "r (a.u.)"), ("log", "log₁₀ r"), ("sqrt", "√r")]


def nice_ticks(lo, hi, n=6):
    """lo..hi 之间约 n 个"整齐"的刻度。"""
    if hi <= lo:
        return [lo]
    step = (hi - lo) / n
    mag = 10 ** math.floor(math.log10(step))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= step)
    first = math.ceil(lo / step) * step
    return [first + i * step for i in range(int((hi - first) / step + 1e-9) + 1)]


SUPERSCRIPT = str.maketrans("-0123456789", "⁻⁰¹²³⁴⁵⁶⁷⁸⁹")


def fmt_log(x):
    """log₁₀ r 横轴的刻度：整数刻度写成 10ⁿ，其余写出 r 的值。"""
    if abs(x - round(x)) < 1e-9:
        return "10" + str(int(round(x))).translate(SUPERSCRIPT)
    return fmt(10 ** x)


def fmt(v):
    if v == 0:
        return "0"
    a = abs(v)
    return f"{v:.3g}" if 1e-3 <= a < 1e4 else f"{v:.2e}"


class PlotWidget(QWidget):
    """一个子图：多条曲线、坐标轴、图例；滚轮缩放、拖动平移、双击复位、悬停显示数值。"""
    xRangeChanged = Signal(float, float)
    resetRequested = Signal()

    MARGIN = (72, 14, 24, 34)      # 左、右、上、下

    def __init__(self, title):
        super().__init__()
        self.title = title
        self.series = []           # dict(label, color, style, x, y, r)；x 已按横轴模式变换
        self.xr = None             # 当前横轴范围 (x0, x1)；None 为自动
        self.yr = None
        self.show_legend = True
        self.x_log = False         # 横轴为 log₁₀ r 时，刻度标成 10ⁿ
        self.drag = None
        self.hover = None
        self.cache = None          # 坐标轴、曲线和图例画好后缓存，悬停时只重画标记点
        self.setMouseTracking(True)
        self.setMinimumHeight(160)

    def changeEvent(self, event):
        if event.type() == QEvent.FontChange:      # 主窗口调整了字号
            self.invalidate()
        super().changeEvent(event)

    def invalidate(self):
        """数据、范围或外观变化后重画缓存。"""
        self.cache = None
        self.update()

    # ---- 数据与范围 ----
    def set_series(self, series, keep_x=True):
        self.series = series
        if not keep_x:
            self.xr = None
        self.yr = None
        self.hover = None
        self.invalidate()

    def auto_x(self):
        """包含所有曲线中幅度不可忽略的部分 (去掉指数网格很长的零尾巴)。"""
        xs = []
        for s in self.series:
            big = max((abs(v) for v in s["y"]), default=0)
            idx = [i for i, v in enumerate(s["y"]) if abs(v) > 1e-3 * big]
            if idx:
                xs += [s["x"][idx[0]], s["x"][idx[-1]]]
        if not xs:
            return (0.0, 1.0)
        lo, hi = min(xs), max(xs)
        pad = (hi - lo) * 0.03 or 1.0
        # r、√r 横轴不出现负值；log r 横轴可以为负
        return (lo - pad if lo < 0 else max(lo - pad, 0.0), hi + pad)

    def x_range(self):
        return self.xr or self.auto_x()

    def y_range(self):
        if self.yr:
            return self.yr
        x0, x1 = self.x_range()
        ys = [0.0]
        for s in self.series:
            i0 = bisect.bisect_left(s["x"], x0)
            i1 = bisect.bisect_right(s["x"], x1)
            ys += s["y"][i0:i1]
        lo, hi = min(ys), max(ys)
        pad = (hi - lo) * 0.06 or 1.0
        return (lo - pad, hi + pad)

    def set_x_range(self, x0, x1, emit=True):
        self.xr = (x0, x1)
        self.yr = None
        self.invalidate()
        if emit:
            self.xRangeChanged.emit(x0, x1)

    def reset(self):
        self.xr = self.yr = None
        self.invalidate()

    # ---- 坐标变换 ----
    def plot_rect(self):
        l, r, t, b = self.MARGIN
        return QRectF(l, t, max(self.width() - l - r, 10), max(self.height() - t - b, 10))

    def to_px(self, x, y, rect, xr, yr):
        return QPointF(rect.left() + (x - xr[0]) / (xr[1] - xr[0]) * rect.width(),
                       rect.bottom() - (y - yr[0]) / (yr[1] - yr[0]) * rect.height())

    def from_px(self, px, py, rect, xr, yr):
        return (xr[0] + (px - rect.left()) / rect.width() * (xr[1] - xr[0]),
                yr[0] + (rect.bottom() - py) / rect.height() * (yr[1] - yr[0]))

    # ---- 绘制 ----
    def paintEvent(self, _event):
        dpr = self.devicePixelRatioF()
        if self.cache is None or self.cache.size() != self.size() * dpr:
            self.cache = QPixmap(self.size() * dpr)
            self.cache.setDevicePixelRatio(dpr)
            painter = QPainter(self.cache)
            self.render_plot(painter)
            painter.end()
        p = QPainter(self)
        p.drawPixmap(0, 0, self.cache)
        if self.hover:
            rect, xr, yr = self.ranges
            s, i = self.hover
            p.setRenderHint(QPainter.Antialiasing)
            p.setClipRect(rect)
            p.setPen(QPen(QColor(s["color"]), 2))
            p.setBrush(QColor("white"))
            p.drawEllipse(self.to_px(s["x"][i], s["y"][i], rect, xr, yr), 4, 4)

    def render_plot(self, p):
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor("white"))
        rect = self.plot_rect()
        xr, yr = self.x_range(), self.y_range()
        self.ranges = (rect, xr, yr)
        grid_pen = QPen(QColor("#e6e6e6"))
        p.setPen(QColor("#333"))
        font = QFont(self.font())
        p.setFont(font)
        fm = p.fontMetrics()

        # 刻度与网格
        for x in nice_ticks(*xr):
            pt = self.to_px(x, yr[0], rect, xr, yr)
            p.setPen(grid_pen)
            p.drawLine(QPointF(pt.x(), rect.top()), QPointF(pt.x(), rect.bottom()))
            p.setPen(QColor("#333"))
            label = fmt_log(x) if self.x_log else fmt(x)
            p.drawText(QPointF(pt.x() - fm.horizontalAdvance(label) / 2, rect.bottom() + fm.ascent() + 4), label)
        for y in nice_ticks(*yr, n=5):
            pt = self.to_px(xr[0], y, rect, xr, yr)
            p.setPen(grid_pen)
            p.drawLine(QPointF(rect.left(), pt.y()), QPointF(rect.right(), pt.y()))
            p.setPen(QColor("#333"))
            label = fmt(y)
            p.drawText(QPointF(rect.left() - fm.horizontalAdvance(label) - 6, pt.y() + fm.ascent() / 2 - 1), label)
        if yr[0] < 0 < yr[1]:
            p.setPen(QPen(QColor("#999"), 1))
            y0 = self.to_px(xr[0], 0, rect, xr, yr).y()
            p.drawLine(QPointF(rect.left(), y0), QPointF(rect.right(), y0))
        p.setPen(QColor("#333"))
        p.drawRect(rect)
        bold = QFont(font)
        bold.setBold(True)
        p.setFont(bold)
        p.drawText(QPointF(rect.left(), rect.top() - 7), self.title)
        p.setFont(font)

        # 曲线
        p.save()
        p.setClipRect(rect)
        for s in self.series:
            i0 = max(bisect.bisect_left(s["x"], xr[0]) - 1, 0)
            i1 = min(bisect.bisect_right(s["x"], xr[1]) + 1, len(s["x"]))
            # 近核处网格很密：与上一个点相距不到半个像素的点不画
            pts, last = [], None
            for i in range(i0, i1):
                pt = self.to_px(s["x"][i], s["y"][i], rect, xr, yr)
                if last is None or abs(pt.x() - last.x()) + abs(pt.y() - last.y()) >= 0.5 or i == i1 - 1:
                    pts.append(pt)
                    last = pt
            pen = QPen(QColor(s["color"]), 1.8)
            pen.setStyle(s["style"])
            p.setPen(pen)
            p.drawPolyline(QPolygonF(pts))
        p.restore()

        # 图例 (几个子图的曲线相同，只在最上面的子图显示)
        if self.series and self.show_legend:
            fit = max(int((rect.height() - 20) / fm.height()), 1)     # 图内放得下的行数
            items = self.series if len(self.series) <= fit else self.series[:fit - 1]
            more = T("… 另有 {n} 条", n=len(self.series) - len(items)) if len(items) < len(self.series) else ""
            w = max([fm.horizontalAdvance(s["label"]) + 44 for s in items] + [fm.horizontalAdvance(more) + 12])
            h = fm.height() * (len(items) + bool(more)) + 8
            box = QRectF(rect.right() - w - 6, rect.top() + 6, w, h)
            p.setPen(QColor("#ccc"))
            p.setBrush(QColor(255, 255, 255, 220))
            p.drawRect(box)
            for k, s in enumerate(items):
                y = box.top() + 4 + fm.height() * (k + 0.5)
                pen = QPen(QColor(s["color"]), 2)
                pen.setStyle(s["style"])
                p.setPen(pen)
                p.drawLine(QPointF(box.left() + 6, y), QPointF(box.left() + 32, y))
                p.setPen(QColor("#333"))
                p.drawText(QPointF(box.left() + 38, y + fm.ascent() / 2 - 1), s["label"])
            if more:
                y = box.top() + 4 + fm.height() * (len(items) + 0.5)
                p.setPen(QColor("#777"))
                p.drawText(QPointF(box.left() + 6, y + fm.ascent() / 2 - 1), more)

    # ---- 交互 ----
    def wheelEvent(self, event):
        rect = self.plot_rect()
        xr, yr = self.x_range(), self.y_range()
        x, y = self.from_px(event.position().x(), event.position().y(), rect, xr, yr)
        f = 0.8 if event.angleDelta().y() > 0 else 1.25
        if event.modifiers() & Qt.ShiftModifier:      # Shift + 滚轮：缩放纵轴
            self.yr = (y + (yr[0] - y) * f, y + (yr[1] - y) * f)
            self.invalidate()
        else:
            self.set_x_range(x + (xr[0] - x) * f, x + (xr[1] - x) * f)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.drag = (event.position(), self.x_range(), self.y_range())

    def mouseReleaseEvent(self, _event):
        self.drag = None

    def mouseDoubleClickEvent(self, _event):
        self.resetRequested.emit()          # 由查看器统一复位所有子图

    def mouseMoveEvent(self, event):
        rect = self.plot_rect()
        if self.drag:
            start, xr, yr = self.drag
            dx = (event.position().x() - start.x()) / rect.width() * (xr[1] - xr[0])
            dy = (event.position().y() - start.y()) / rect.height() * (yr[1] - yr[0])
            self.yr = (yr[0] + dy, yr[1] + dy)
            self.set_x_range(xr[0] - dx, xr[1] - dx)
            return
        self.update_hover(event)

    def update_hover(self, event):
        """找离鼠标最近的数据点，显示曲线名称和数值。"""
        if self.cache is None:
            return
        rect, xr, yr = self.ranges                # 与缓存中的图使用同一组坐标范围
        mx, my = event.position().x(), event.position().y()
        best = None
        x, _ = self.from_px(mx, my, rect, xr, yr)
        for s in self.series:
            i = bisect.bisect_left(s["x"], x)
            for j in (i - 1, i):
                if 0 <= j < len(s["x"]):
                    pt = self.to_px(s["x"][j], s["y"][j], rect, xr, yr)
                    d = (pt.x() - mx) ** 2 + (pt.y() - my) ** 2
                    if best is None or d < best[0]:
                        best = (d, s, j)
        if best and best[0] < 30 ** 2:
            _, s, j = best
            self.hover = (s, j)
            QToolTip.showText(event.globalPosition().toPoint(),
                              f"{s['label']}\nr = {s['r'][j]:.6g}\n{self.title} = {s['y'][j]:.6g}", self)
        else:
            self.hover = None
            QToolTip.hideText()
        self.update()

    def leaveEvent(self, _event):
        self.hover = None
        self.update()


class WfnPanel(Panel):
    KIND, TITLE = "wfn", "径向波函数"

    def __init__(self, workdir):
        super().__init__()
        self.cache = {}            # 路径 -> (mtime, 轨道列表 或 错误信息)
        self.build_view(QVBoxLayout(self), workdir)
        self.scan()

    def build_view(self, lay, workdir):
        top = self.dir_bar(workdir)
        top.addStretch()
        lay.addLayout(top)

        split = QSplitter(Qt.Horizontal)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addWidget(QLabel(T("径向波函数文件 (勾选要比较的文件)")))
        self.files = QListWidget()
        self.files.itemChanged.connect(self.files_changed)
        ll.addWidget(self.files, 2)
        add = QPushButton(T("添加其他文件…"))
        add.clicked.connect(self.add_file)
        ll.addWidget(add)
        ll.addWidget(QLabel(T("轨道")))
        self.orbs = QListWidget()
        self.orbs.itemChanged.connect(self.redraw)
        ll.addWidget(self.orbs, 3)
        ob = QHBoxLayout()
        for text, state in [("全选", True), ("全不选", False)]:
            b = QPushButton(T(text))
            b.clicked.connect(lambda _=False, s=state: self.check_all_orbitals(s))
            ob.addWidget(b)
        ll.addLayout(ob)
        split.addWidget(left)

        right = QSplitter(Qt.Vertical)
        plots = QWidget()
        pl = QVBoxLayout(plots)
        pl.setContentsMargins(0, 0, 0, 0)
        ctl = QHBoxLayout()
        self.show_q = {}
        for key, label in QUANTITIES:
            c = QCheckBox(label)
            c.setChecked(key != "Q")
            c.toggled.connect(self.layout_plots)
            self.show_q[key] = c
            ctl.addWidget(c)
        ctl.addSpacing(16)
        ctl.addWidget(QLabel(T("横轴")))
        self.xmode = QComboBox()
        for key, label in X_MODES:
            self.xmode.addItem(label, key)
        self.xmode.currentIndexChanged.connect(lambda: self.redraw(reset=True))
        ctl.addWidget(self.xmode)
        ctl.addStretch()
        for text, slot in [("复位缩放", self.reset_zoom), ("导出图片…", self.export_png), ("导出数据…", self.export_csv)]:
            b = QPushButton(T(text))
            b.clicked.connect(slot)
            ctl.addWidget(b)
        ctl.addWidget(help_button(T("滚轮缩放横轴，Shift + 滚轮缩放纵轴，拖动平移，双击复位；鼠标停在曲线上显示数值。")))
        pl.addLayout(ctl)
        self.canvas = QWidget()
        self.canvas_lay = QVBoxLayout(self.canvas)
        self.canvas_lay.setContentsMargins(0, 0, 0, 0)
        self.plots = {}
        self.zoomed = False        # 用户缩放 / 平移过横轴后，切换曲线时保持该范围
        for key, label in QUANTITIES:
            w = PlotWidget(label)
            w.xRangeChanged.connect(lambda x0, x1, src=w: self.sync_x(src, x0, x1))
            w.resetRequested.connect(self.reset_zoom)
            self.plots[key] = w
            self.canvas_lay.addWidget(w)
        pl.addWidget(self.canvas, 1)
        right.addWidget(plots)

        self.table = QTableWidget(0, 9)
        self.table.setHorizontalHeaderLabels([T("文件"), T("轨道"), "κ", T("−ε (a.u.)"), "⟨r⟩ (a.u.)",
                                              T("峰值位置 r (a.u.)"), T("节点 / 应有"), T("归一化"), T("网格点数")])
        self.table.horizontalHeaderItem(3).setToolTip(T("GRASP 中束缚态的轨道能量记为正值"))
        self.table.horizontalHeaderItem(6).setToolTip(T("大分量 P(r) 的节点数；谱学轨道应为 n − l − 1"))
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSortingEnabled(True)
        right.addWidget(self.table)
        right.setSizes([620, 200])
        split.addWidget(right)
        split.setSizes([260, 990])
        lay.addWidget(split, 1)
        self.layout_plots()

    # ---- 文件 ----
    def files_checked(self):
        self.files_changed()

    def add_extra_file(self, path):
        self.add_item(path, True, True)

    def scan(self):
        """列出目录中的径向波函数文件；默认勾选最新的一个 (保留已勾选的)。"""
        d = self.dir.text()
        checked = {self.files.item(i).data(Qt.UserRole) for i in range(self.files.count())
                   if self.files.item(i).checkState() == Qt.Checked}
        paths = [os.path.join(d, n) for n in wfn.find_w_files(d)]
        extra = [self.files.item(i).data(Qt.UserRole) for i in range(self.files.count())
                 if self.files.item(i).data(Qt.UserRole + 1)]
        if not checked & set(paths + extra) and paths:
            checked = {max(paths, key=os.path.getmtime)}
        self.files.blockSignals(True)
        self.files.clear()
        for path in paths + [p for p in extra if p not in paths]:
            self.add_item(path, path in checked, path in extra)
        self.files.blockSignals(False)
        self.files_changed()
        self.status(T("{n} 个径向波函数文件", n=len(paths)) if paths else T("此目录中没有径向波函数文件"))

    def add_item(self, path, checked, extra=False):
        item = QListWidgetItem(os.path.basename(path) if not extra else path)
        item.setData(Qt.UserRole, path)
        item.setData(Qt.UserRole + 1, extra)
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        item.setCheckState(Qt.Checked if checked else Qt.Unchecked)
        self.files.addItem(item)
        return item

    def add_file(self):
        path, _ = QFileDialog.getOpenFileName(self, T("添加其他文件…"), self.dir.text())
        if path and not wfn.is_wfn_file(path):
            QMessageBox.warning(self, T("添加其他文件…"), T("{f} 不是 GRASP 径向波函数文件", f=os.path.basename(path)))
            return
        if path:
            self.files.blockSignals(True)
            self.add_item(path, True, True)
            self.files.blockSignals(False)
            self.files_changed()

    def load(self, path):
        """读取 (带缓存，文件更新后重新读取)；返回轨道列表，出错时返回错误信息字符串。"""
        try:
            mtime = os.path.getmtime(path)
        except OSError as e:
            return str(e)
        hit = self.cache.get(path)
        if hit and hit[0] == mtime:
            return hit[1]
        try:
            data = wfn.read_w(path)
        except (OSError, ValueError, struct.error) as e:
            data = str(e)
        self.cache[path] = (mtime, data)
        return data

    def checked_files(self):
        """[(标签, 路径, 轨道列表)]，只含能读取的文件。"""
        out = []
        for i in range(self.files.count()):
            item = self.files.item(i)
            if item.checkState() != Qt.Checked:
                continue
            path = item.data(Qt.UserRole)
            data = self.load(path)
            if isinstance(data, str):
                item.setForeground(QColor("#c00"))
                item.setToolTip(T("无法读取：{e}", e=data))
                continue
            item.setForeground(QColor("black"))
            item.setToolTip(path)
            out.append((os.path.splitext(os.path.basename(path))[0], path, data))
        return out

    def files_changed(self, *_):
        """更新轨道列表 (所选文件中轨道的并集)，保留已勾选的轨道。"""
        old = {self.orbs.item(i).text(): self.orbs.item(i).checkState() == Qt.Checked
               for i in range(self.orbs.count())}
        orbitals = {}
        for _, _, orbs in self.checked_files():
            for o in orbs:
                orbitals.setdefault(o.name, o.sort_key)
        names = sorted(orbitals, key=orbitals.get)
        if not any(old.get(n) for n in names):
            # 第一次：勾选最内层的几个轨道 (通常是参考组态中的轨道)
            old = {n: True for n in names[:4]}
        self.orbs.blockSignals(True)
        self.orbs.clear()
        for n in names:
            item = QListWidgetItem(n)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if old.get(n) else Qt.Unchecked)
            self.orbs.addItem(item)
        self.orbs.blockSignals(False)
        self.redraw()

    def check_all_orbitals(self, state):
        self.orbs.blockSignals(True)
        for i in range(self.orbs.count()):
            self.orbs.item(i).setCheckState(Qt.Checked if state else Qt.Unchecked)
        self.orbs.blockSignals(False)
        self.redraw()

    # ---- 绘图 ----
    def layout_plots(self):
        first = True
        for key, w in self.plots.items():
            visible = self.show_q[key].isChecked()
            w.setVisible(visible)
            w.show_legend = visible and first
            first = first and not visible
            w.invalidate()
        if not self.zoomed:
            self.auto_x()

    def selected(self):
        """[(文件标签, 轨道, 颜色, 线型)]"""
        names = [self.orbs.item(i).text() for i in range(self.orbs.count())
                 if self.orbs.item(i).checkState() == Qt.Checked]
        files = self.checked_files()
        out = []
        for fi, (label, _, orbs) in enumerate(files):
            by_name = {o.name: o for o in orbs}
            for oi, name in enumerate(names):
                if name in by_name:
                    out.append((label, by_name[name], COLORS[oi % len(COLORS)], STYLES[fi % len(STYLES)]))
        return out, len(files)

    def transform(self, r):
        mode = self.xmode.currentData()
        if mode == "log":
            return [math.log10(x) if x > 0 else -12.0 for x in r]
        if mode == "sqrt":
            return [math.sqrt(x) for x in r]
        return list(r)

    def redraw(self, *_args, reset=False):
        sel, nfiles = self.selected()
        for key, w in self.plots.items():
            series = []
            for label, o, color, style in sel:
                r = o.r[1:] if self.xmode.currentData() == "log" else o.r     # log 横轴去掉 r = 0
                y = {"P": o.P, "Q": o.Q, "D": o.density()}[key]
                y = list(y[1:] if len(r) < len(o.r) else y)
                series.append(dict(label=f"{label} {o.name}" if nfiles > 1 else o.name, color=color,
                                   style=style, x=self.transform(r), y=y, r=list(r)))
            w.x_log = self.xmode.currentData() == "log"
            w.set_series(series)
        if reset:
            self.zoomed = False
        if not self.zoomed:
            self.auto_x()
        self.fill_table(sel)

    def auto_x(self):
        """所有可见子图使用同一个横轴范围：各子图自动范围的并集。"""
        ranges = [w.auto_x() for k, w in self.plots.items() if self.show_q[k].isChecked() and w.series]
        if not ranges:
            return
        x0, x1 = min(r[0] for r in ranges), max(r[1] for r in ranges)
        for w in self.plots.values():
            w.set_x_range(x0, x1, emit=False)

    def sync_x(self, src, x0, x1):
        self.zoomed = True
        for w in self.plots.values():
            if w is not src:
                w.set_x_range(x0, x1, emit=False)

    def reset_zoom(self):
        self.zoomed = False
        for w in self.plots.values():
            w.reset()
        self.auto_x()

    def fill_table(self, sel):
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(sel))
        for row, (label, o, color, _) in enumerate(sel):
            nodes, expect = o.nodes(), o.expected_nodes()
            values = [label, o.name, o.kappa, o.energy, o.mean_r(), o.r_peak(), f"{nodes} / {expect}",
                      o.norm(), len(o.r)]
            for col, v in enumerate(values):
                item = QTableWidgetItem()
                if isinstance(v, float):
                    item.setData(Qt.DisplayRole, float(f"{v:.8g}"))
                else:
                    item.setData(Qt.DisplayRole, v)
                if col == 1:
                    item.setForeground(QColor(color))
                if col == 6 and nodes != expect:
                    item.setForeground(QColor("#c60"))
                self.table.setItem(row, col, item)
        self.table.setSortingEnabled(True)
        self.table.resizeColumnsToContents()      # 填完后调整一次 (逐格自动调整很慢)

    # ---- 导出 ----
    def export_png(self):
        path, _ = QFileDialog.getSaveFileName(self, T("导出图片…"), os.path.join(self.dir.text(), "wavefunctions.png"),
                                              "PNG (*.png)")
        if path:
            self.canvas.grab().save(path)
            self.status(T("已导出 {p}", p=path))

    def export_csv(self):
        sel, _ = self.selected()
        if not sel:
            return
        path, _ = QFileDialog.getSaveFileName(self, T("导出数据…"), os.path.join(self.dir.text(), "wavefunctions.csv"),
                                              "CSV (*.csv)")
        if not path:
            return
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["file", "orbital", "kappa", "r", "P", "Q"])
            for label, o, _, _ in sel:
                for r, p, q in zip(o.r, o.P, o.Q):
                    w.writerow([label, o.name, o.kappa, repr(r), repr(p), repr(q)])
        self.status(T("已导出 {p}", p=path))
