"""
界面各部分共用的控件：字体与小控件、GRASP bin 目录和 MPI 设置、最近的目录，以及查看器面板的基类
(Panel) 和单独显示一个面板的窗口 (PanelWindow)。

主窗口 (grasp_gui.py) 有三个标签页：计算 (calc_panel.py)、径向波函数 (wfn_panel.py)、能级图 (level_panel.py)。
"""

import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import (QApplication, QCheckBox, QFileDialog, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                               QMainWindow, QMenu, QPlainTextEdit, QPushButton, QSpinBox, QToolButton, QToolTip,
                               QVBoxLayout, QWidget)

import grasp_core as core
import grasp_mix as mix
import grasp_wfn as wfn
from grasp_core import T

CJK_FONTS = ["Noto Sans CJK SC", "Source Han Sans SC", "Microsoft YaHei", "WenQuanYi Micro Hei",
             "Droid Sans Fallback", "AR PL UMing CN"]
LATIN_FONTS = ["Noto Sans", "DejaVu Sans", "Liberation Sans"]
MONO_FONTS = ["DejaVu Sans Mono", "Noto Sans Mono", "Liberation Mono", "Noto Sans Mono CJK SC"]


def first_family(candidates):
    fams = set(QFontDatabase.families())
    return next((f for f in candidates if f in fams), None)


def fixed_font():
    """等宽字体：优先用常见的等宽字体 (包括打包的 DejaVu Sans Mono)，都没有时用系统默认。"""
    family = first_family(MONO_FONTS)
    if family:
        font = QFont(family)
        font.setStyleHint(QFont.Monospace)
    else:
        font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    font.setPointSizeF(QApplication.font().pointSizeF())       # 与界面字号一致
    return font


def hint_label(text):
    w = QLabel(T(text) if text else "")
    w.setStyleSheet("color: gray;")
    return w


def text_edit(text="", lines=4):
    w = QPlainTextEdit(text)
    w.setFont(fixed_font())
    w.setTabChangesFocus(True)
    w.setFixedHeight(int(w.fontMetrics().lineSpacing() * lines + 14))
    return w


def button(text, slot):
    b = QPushButton(T(text))
    b.clicked.connect(slot)
    return b


def help_button(text, label="操作提示"):
    """说明文字不占界面空间：鼠标停在按钮上或点击按钮时显示 (按分号分行)。text 已翻译。"""
    tip = "<p>" + text.replace("；", "；<br>").replace("; ", ";<br>") + "</p>"
    b = QToolButton()
    b.setText(T(label) if label else "?")
    b.setToolTip(tip)
    b.clicked.connect(lambda: QToolTip.showText(b.mapToGlobal(b.rect().bottomLeft()), tip, b))
    return b


# ---------------------------------------------------------------------------
#  最近的目录、按内容识别文件
# ---------------------------------------------------------------------------

RECENT_MAX = 10


def recent_dirs():
    return [d for d in core.load_config().get("recent_dirs", []) if os.path.isdir(d)]


def remember_dir(d):
    """把目录放到“最近的目录”最前面 (保存在配置文件中)。"""
    d = os.path.abspath(d)
    if os.path.isdir(d):
        old = core.load_config().get("recent_dirs", [])
        core.save_config(recent_dirs=[d] + [x for x in old if x != d][:RECENT_MAX - 1])


def recent_menu(parent, choose, title="", browse=None):
    """“最近的目录”菜单 (打开时读取)，选中后调用 choose(目录)；browse 给出时，最上面是“打开其他目录…”。"""
    menu = QMenu(title, parent)

    def fill():
        menu.clear()
        if browse:
            menu.addAction(T("打开其他目录…")).triggered.connect(browse)
            menu.addSeparator()
        dirs = recent_dirs()
        for d in dirs:
            menu.addAction(d).triggered.connect(lambda _=False, d=d: choose(d))
        if not dirs:
            menu.addAction(T("(无)")).setEnabled(False)
    menu.aboutToShow.connect(fill)
    return menu


def recent_button(choose, browse=None):
    b = QToolButton()
    b.setText(T("最近"))
    b.setToolTip(T("最近打开的目录"))
    b.setPopupMode(QToolButton.InstantPopup)
    b.setMenu(recent_menu(b, choose, browse=browse))
    return b


def viewer_for(path):
    """显示这个文件的面板："wfn"、"levels"，不认识时为 None (按文件内容识别，与扩展名无关)。"""
    if not os.path.isfile(path):
        return None
    if wfn.is_wfn_file(path):
        return "wfn"
    if mix.is_mix_file(path):
        return "levels"
    return None


# ---------------------------------------------------------------------------
#  GRASP bin 目录和 MPI 设置 (计算和跃迁共用)
# ---------------------------------------------------------------------------

class RunSettings(QWidget):
    """可收起的设置区；收起时只显示一行摘要。展开 / 收起的按钮 (self.toggle) 由主窗口放在工作目录一行。"""

    def __init__(self, cfg):
        super().__init__()
        top = QVBoxLayout(self)
        top.setContentsMargins(0, 0, 0, 0)
        self.toggle = QPushButton()
        self.toggle.setToolTip(T("展开或收起 GRASP bin 目录和 MPI 设置"))
        self.toggle.clicked.connect(lambda: self.show_box(not self.box.isVisible()))
        self.summary = hint_label("")
        top.addWidget(self.summary)

        self.box = QWidget()
        g = QGridLayout(self.box)
        g.setContentsMargins(0, 0, 0, 0)
        self.bindir_edit = QLineEdit(cfg["bindir"])
        self.bindir_edit.editingFinished.connect(self.save)     # 改完立即保存，下次启动沿用
        g.addWidget(QLabel(T("GRASP bin 目录")), 0, 0)
        g.addWidget(self.bindir_edit, 0, 1)
        g.addWidget(button("浏览…", self.pick_bindir), 0, 2)
        mp = QHBoxLayout()
        self.np_spin = QSpinBox()
        self.np_spin.setRange(1, max(os.cpu_count() or 1, 1))
        self.np_spin.setValue(cfg["np"])
        self.mpirun_edit = QLineEdit(cfg["mpirun"])
        self.mpitmp_edit = QLineEdit(cfg["mpitmp"])
        self.nice_check = QCheckBox(T("降低计算进程优先级 (nice)"))
        self.nice_check.setChecked(cfg["nice"])
        mpi_hint = T("进程数 > 1 时 rangular / rmcdhf / rci / rbiotransform / rtransition 改用 *_mpi 版本；"
                     "rangular 与 rmcdhf 须使用相同的进程数和 MPI_TMP。")
        mpi_hint += "\n" + T("默认按物理核数；超线程对 GRASP 帮助不大。")
        for w in [QLabel(T("MPI 进程数")), self.np_spin, QLabel(T("mpirun 命令")), self.mpirun_edit,
                  QLabel("MPI_TMP"), self.mpitmp_edit, self.nice_check]:
            w.setToolTip(mpi_hint)
            mp.addWidget(w)
        mp.addStretch()
        g.addLayout(mp, 1, 0, 1, 3)
        g.setColumnStretch(1, 1)
        top.addWidget(self.box)

        self.bindir_edit.textChanged.connect(self.update_summary)
        self.np_spin.valueChanged.connect(self.update_summary)
        # 按上次的收起状态显示；GRASP bin 目录中找不到 GRASP 时总是展开，提醒检查路径
        collapsed = core.load_config().get("settings_collapsed", True)
        if not os.path.isfile(os.path.join(cfg["bindir"], "rmcdhf")):
            collapsed = False
        self.show_box(not collapsed, save=False)

    def show_box(self, visible, save=True):
        self.box.setVisible(visible)
        self.summary.setVisible(not visible)
        self.toggle.setText(("▾ " if visible else "▸ ") + T("设置"))
        self.update_summary()
        if save:
            core.save_config(settings_collapsed=not visible)

    def update_summary(self):
        n = self.np_spin.value()
        mpi = T("MPI {n} 进程", n=n) if n > 1 else T("串行")
        self.summary.setText(T("GRASP bin：{b}　·　{m}", b=self.bindir_edit.text(), m=mpi))

    def pick_bindir(self):
        d = QFileDialog.getExistingDirectory(self, "", self.bindir_edit.text() or os.getcwd())
        if d:
            self.bindir_edit.setText(d)
            self.save()

    def bindir(self):
        return self.bindir_edit.text()

    def np(self):
        return self.np_spin.value()

    def mpirun(self):
        return self.mpirun_edit.text()

    def mpitmp(self):
        return self.mpitmp_edit.text().strip() or "tmp_mpi"

    def nice(self):
        return self.nice_check.isChecked()

    def mpi_prefix(self):
        n = self.np()
        return f"{self.mpirun().strip()} -np {n}" if n > 1 else ""

    def values(self):
        """运行设置 (bindir, np, mpirun, mpitmp, nice)，交给 core.Runner。"""
        return dict(bindir=self.bindir(), np=self.np(), mpirun=self.mpirun(), mpitmp=self.mpitmp(), nice=self.nice())

    def apply(self, data):
        """载入参数文件中的设置 (只设置其中有的项)。"""
        for key, edit in [("bindir", self.bindir_edit), ("mpirun", self.mpirun_edit), ("mpitmp", self.mpitmp_edit)]:
            if key in data:
                edit.setText(str(data[key]))
        if "np" in data:
            self.np_spin.setValue(int(data["np"]))
        if "nice" in data:
            self.nice_check.setChecked(bool(data["nice"]))

    def save(self):
        """保存到 ~/.config/grasp_gui.json，作为下次启动的默认值。"""
        core.save_config(bindir=self.bindir().strip(), np=self.np(), mpirun=self.mpirun(), mpitmp=self.mpitmp(),
                         nice=self.nice())


def saved_settings():
    """没有主窗口时的运行设置：主窗口保存的 GRASP bin 目录和 MPI 设置。"""
    return core.saved_run_settings()


# ---------------------------------------------------------------------------
#  查看器面板
# ---------------------------------------------------------------------------

class Panel(QWidget):
    """径向波函数和能级图面板的基类。

    在主窗口的标签页中，面板显示主窗口的工作目录 (由主窗口调用 set_dir)；在单独的窗口 (PanelWindow)
    中有自己的目录栏。子类设置 KIND / TITLE，在界面中加入 dir_bar()，并实现 scan() (重新列出目录中的
    文件) 和 files_checked() (勾选的文件改变后更新显示)；文件列表为 self.files，每项的 Qt.UserRole 是路径。
    拖放到面板上的目录或文件交给 self.host.open_path 处理。"""
    KIND, TITLE = "", ""
    message = Signal(str)      # 状态栏消息

    def __init__(self):
        super().__init__()
        self.host = None
        self.setAcceptDrops(True)

    def status(self, text):
        self.message.emit(text)

    def dir_bar(self, workdir):
        """目录栏：目录、浏览、最近的目录 (只在单独的窗口中显示) 和刷新。子类在后面加入自己的控件，
        最后加一个 addStretch()，使控件在没有目录栏时靠左排列。"""
        top = QHBoxLayout()
        self.dir_box = QWidget()
        row = QHBoxLayout(self.dir_box)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(QLabel(T("目录")))
        self.dir = QLineEdit(workdir)
        self.dir.editingFinished.connect(self.dir_edited)
        row.addWidget(self.dir, 1)
        row.addWidget(button("浏览…", self.pick_dir))
        row.addWidget(recent_button(self.set_dir, self.pick_dir))
        self.dir_box.hide()
        top.addWidget(self.dir_box, 1)
        top.addWidget(button("刷新", self.scan))
        return top

    def show_dir_bar(self):
        self.dir_box.show()

    # ---- 目录与文件 ----
    def dir_edited(self):
        remember_dir(self.dir.text())
        self.scan()

    def pick_dir(self):
        d = QFileDialog.getExistingDirectory(self, "", self.dir.text())
        if d:
            self.dir.setText(d)
            self.dir_edited()

    def set_dir(self, d):
        if os.path.abspath(d) != os.path.abspath(self.dir.text()):
            self.dir.setText(d)
            self.dir_edited()

    def open_file(self, path):
        """显示文件所在的目录，并只勾选这个文件。"""
        path = os.path.abspath(path)
        self.set_dir(os.path.dirname(path))
        self.files.blockSignals(True)
        found = False
        for i in range(self.files.count()):
            it = self.files.item(i)
            mine = os.path.abspath(it.data(Qt.UserRole)) == path
            it.setCheckState(Qt.Checked if mine else Qt.Unchecked)
            found |= mine
        if not found:
            self.add_extra_file(path)
        self.files.blockSignals(False)
        self.files_checked()

    def add_extra_file(self, path):
        """目录中没有列出的文件 (子类可以把它加入列表)。"""

    def checked_paths(self):
        return [self.files.item(i).data(Qt.UserRole) for i in range(self.files.count())
                if self.files.item(i).checkState() == Qt.Checked]

    def set_checked_paths(self, paths):
        """恢复勾选的文件 (重新创建面板后)；列表中没有的文件忽略。"""
        paths = set(paths)
        if not paths:
            return
        self.files.blockSignals(True)
        for i in range(self.files.count()):
            it = self.files.item(i)
            it.setCheckState(Qt.Checked if it.data(Qt.UserRole) in paths else Qt.Unchecked)
        self.files.blockSignals(False)
        self.files_checked()

    def busy(self):
        """是否有计算在运行 (关闭前询问)。"""
        return False

    def shutdown(self):
        """关闭前结束正在运行的计算。"""

    # ---- 拖放 ----
    def dragEnterEvent(self, event):
        if any(u.isLocalFile() for u in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self, event):
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        if paths and self.host is not None:
            self.host.open_path(paths[0])
            event.acceptProposedAction()


PANEL_WINDOWS = []         # 打开着的单独窗口 (保持引用，关闭后移除)


class PanelWindow(QMainWindow):
    """在单独的窗口中显示一个面板 (有自己的目录栏)，用于同时查看另一个目录或与计算页并排。"""

    def __init__(self, panel):
        super().__init__()
        self.panel = panel
        panel.host = self
        panel.show_dir_bar()
        panel.message.connect(self.statusBar().showMessage)
        self.setWindowTitle(T(panel.TITLE))
        self.resize(1400 if panel.KIND == "levels" else 1250, 880)
        self.setCentralWidget(panel)
        fm = self.menuBar().addMenu(T("文件"))
        fm.addAction(T("打开目录…")).triggered.connect(panel.pick_dir)
        fm.addMenu(recent_menu(fm, panel.set_dir, T("最近的目录"), panel.pick_dir))
        fm.addSeparator()
        fm.addAction(T("关闭")).triggered.connect(self.close)
        remember_dir(panel.dir.text())
        PANEL_WINDOWS.append(self)

    @classmethod
    def open(cls, factory, workdir, path=None):
        win = cls(factory(workdir))
        if path:
            win.panel.open_file(path)
        win.show()
        win.raise_()
        win.activateWindow()
        return win

    def open_path(self, path):
        """拖放的目录或文件：同类文件在本窗口中打开，另一类文件在新窗口中打开。"""
        if os.path.isdir(path):
            self.panel.set_dir(path)
            return
        kind = viewer_for(path)
        if kind == self.panel.KIND:
            self.panel.open_file(path)
        elif kind is None:
            self.panel.set_dir(os.path.dirname(os.path.abspath(path)))
        else:
            PanelWindow.open(PANEL_FACTORIES[kind], os.path.dirname(os.path.abspath(path)), path)

    def closeEvent(self, event):
        self.panel.shutdown()
        if self in PANEL_WINDOWS:
            PANEL_WINDOWS.remove(self)
        super().closeEvent(event)


PANEL_FACTORIES = {}       # 面板种类 -> 创建面板的函数 (工作目录)；由主窗口注册
