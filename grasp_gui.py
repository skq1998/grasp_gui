#!/usr/bin/env python3
"""
GRASP 图形界面 (Qt / PySide6)。主窗口上方是工作目录和运行设置，下面三个标签页：
  计算    calc_panel.py   填写各程序的参数，自动生成交互输入并在工作目录中运行 (可组成流程、导出脚本)
  径向波函数  wfn_panel.py    径向波函数的图和轨道性质
  能级图  level_panel.py  能级图、组成和跃迁
共用的控件在 gui_common.py 中，与界面无关的部分在 grasp_*.py 中。界面支持中文 / English。

用法:  python3 grasp_gui.py [--lang en] [目录 | levels [目录] | wfn [目录] | 文件 | check|script|steps|reference …]   (见 --help)
依赖:  pip install pyside6
"""

import os
import sys

if __name__ == "__main__" and sys.argv[1:2] == ["_run"] and len(sys.argv) == 3:
    # 后台运行进程 (见 grasp_bg.py)：不加载 Qt
    import grasp_bg
    sys.exit(grasp_bg.serve(sys.argv[2]))


def fix_wsl_display():
    """WSL 启用 systemd 后 $XDG_RUNTIME_DIR 中常缺少 WSLg 的 wayland 套接字；直接指向它并优先用 Wayland。"""
    if os.environ.get("QT_QPA_PLATFORM"):
        return
    wl = os.environ.get("WAYLAND_DISPLAY")
    if not wl:
        return
    path = wl if os.path.isabs(wl) else os.path.join(os.environ.get("XDG_RUNTIME_DIR", ""), wl)
    wslg = os.path.join("/mnt/wslg/runtime-dir", os.path.basename(wl))
    if not os.path.exists(path) and os.path.exists(wslg):
        os.environ["WAYLAND_DISPLAY"] = path = wslg
    if os.path.exists(path):
        os.environ["QT_QPA_PLATFORM"] = "wayland;xcb"


FONTS_CONF = """<?xml version="1.0"?>
<!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd">
<fontconfig>
  <dir>/usr/share/fonts</dir>
  <dir>/usr/local/share/fonts</dir>
  <dir prefix="xdg">fonts</dir>
  <dir>~/.fonts</dir>
  <cachedir>{cache}</cachedir>
  <alias><family>sans-serif</family>
    <prefer><family>Noto Sans</family><family>DejaVu Sans</family><family>Noto Sans CJK SC</family></prefer></alias>
  <alias><family>serif</family>
    <prefer><family>Noto Serif</family><family>DejaVu Serif</family><family>Noto Serif CJK SC</family></prefer></alias>
  <alias><family>monospace</family>
    <prefer><family>DejaVu Sans Mono</family><family>Noto Sans Mono</family><family>Noto Sans Mono CJK SC</family></prefer></alias>
  <match target="font">
    <edit name="antialias" mode="assign"><bool>true</bool></edit>
    <edit name="hinting" mode="assign"><bool>true</bool></edit>
    <edit name="hintstyle" mode="assign"><const>hintslight</const></edit>
  </match>
</fontconfig>
"""


def fix_fontconfig():
    """打包的程序自带构建机器上的 fontconfig 库；系统的 /etc/fonts 若来自更新的 fontconfig，
    旧库读它时会打印大量 "invalid attribute 'xsi:nil'" 之类的错误。因此改用一份只列出字体目录的
    简单配置 (系统和用户字体照常可用；打包的字体在 load_fonts 中直接注册)。"""
    if not getattr(sys, "frozen", False) or os.environ.get("FONTCONFIG_FILE") or os.environ.get("FONTCONFIG_PATH"):
        return
    cache = os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "grasp_gui")
    path = os.path.join(cache, "fonts.conf")
    text = FONTS_CONF.format(cache=os.path.join(cache, "fontconfig"))
    try:
        os.makedirs(cache, exist_ok=True)
        old = open(path).read() if os.path.exists(path) else None
        if old != text:
            with open(path, "w") as f:
                f.write(text)
    except OSError:
        return
    os.environ["FONTCONFIG_FILE"] = os.environ["GRASP_GUI_FONTCONFIG"] = path


fix_wsl_display()
fix_fontconfig()
# 打包的 libxkbcommon 可能比系统的 Compose 文件旧，读到新的按键名 (如 dead_hamza) 时会打印大量 ERROR；
# 这些行被跳过，不影响使用，因此只显示严重错误
os.environ.setdefault("XKB_LOG_LEVEL", "critical")

from PySide6.QtCore import QByteArray, QEvent, QObject, QPointF, Qt  # noqa: E402
from PySide6.QtGui import (QAction, QActionGroup, QColor, QFontDatabase, QKeySequence, QPainter,  # noqa: E402
                           QPalette)
from PySide6.QtWidgets import (QApplication, QFileDialog, QHBoxLayout, QLabel, QLineEdit,  # noqa: E402
                               QMainWindow, QMessageBox, QSplitter, QSplitterHandle, QTabWidget, QToolButton,
                               QVBoxLayout, QWidget)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import grasp_bg                                                     # noqa: E402
import grasp_core as core                                           # noqa: E402
import gui_common as gc                                             # noqa: E402
from calc_panel import CalcPanel                                    # noqa: E402
from grasp_core import T                                            # noqa: E402
from gui_common import button                                       # noqa: E402

DEFAULT_PT = 10.0          # 系统默认字号 (main 中按 QApplication 的默认字体设置)；"视图"菜单可调整并保存


def load_fonts():
    """注册随程序打包的字体 (build.sh 放在 fonts/ 中)，使没有安装字体的机器也能正常显示。
    系统已有的字体照常可用；查找位置为可执行文件内部和程序所在目录的 fonts/。"""
    dirs = [os.path.join(getattr(sys, "_MEIPASS", ""), "fonts"),
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")]
    for d in dirs:
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            if name.lower().endswith((".ttf", ".otf", ".ttc")):
                QFontDatabase.addApplicationFont(os.path.join(d, name))


def wfn_panel(workdir):
    from wfn_panel import WfnPanel
    return WfnPanel(workdir)


def level_panel(workdir, settings=gc.saved_settings):
    from level_panel import LevelPanel
    return LevelPanel(workdir, settings)


# ---------------------------------------------------------------------------
#  主窗口：工作目录和运行设置，下面是计算、径向波函数、能级图三个标签页
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):
    TABS = [("calc", "计算"), ("wfn", "径向波函数"), ("levels", "能级图")]

    def __init__(self, workdir=None, tab=None):
        super().__init__()
        self.resize(1300, 900)
        cfg = core.load_config()
        if not workdir:
            last = cfg.get("workdir", "")
            workdir = last if last and os.path.isdir(last) else os.getcwd()
        self.cur_workdir = os.path.abspath(workdir)
        self.run = None
        self.calc = None
        self.panels = {}           # 已创建的查看器面板：种类 -> 面板 (第一次切换到该标签页时创建)
        self.pending = {}          # 重新创建面板后要恢复的勾选文件：种类 -> [路径]
        gc.PANEL_FACTORIES.update(wfn=wfn_panel, levels=lambda d: level_panel(d, lambda: self.run.values()))
        self.build()
        self.restore_layout()
        core.save_config(workdir=self.cur_workdir)
        gc.remember_dir(self.cur_workdir)
        self.calc.open_workdir(self.cur_workdir)
        self.show_tab(tab or cfg.get("main_tab", "calc"))

    # ---- 布局 ----
    def build(self):
        """创建全部控件；切换语言或字号时重新调用 (面板由 rebuild 保留或重新创建)。"""
        # 中文界面优先用中文字体，英文界面优先用西文字体；另一种文字由 Qt 自动换用其他字体显示
        app = QApplication.instance()
        family = gc.first_family(gc.CJK_FONTS + gc.LATIN_FONTS if core.LANG == "zh"
                                 else gc.LATIN_FONTS + gc.CJK_FONTS)
        font = app.font()
        if family:
            font.setFamily(family)
        font.setPointSizeF(core.load_config().get("font_size") or DEFAULT_PT)
        app.setFont(font)
        self.setWindowTitle(T("GRASP 图形界面"))
        self.build_menu()

        central = QWidget()
        lay = QVBoxLayout(central)
        self.run = gc.RunSettings(self.run.values() if self.run else gc.saved_settings())
        row = QHBoxLayout()
        row.addWidget(QLabel(T("工作目录")))
        self.workdir = QLineEdit(self.cur_workdir)
        self.workdir.editingFinished.connect(lambda: self.change_workdir(self.workdir.text()))
        row.addWidget(self.workdir, 1)
        row.addWidget(button("浏览…", self.pick_workdir))
        row.addWidget(gc.recent_button(self.change_workdir))
        row.addWidget(self.run.toggle)
        lay.addLayout(row)
        lay.addWidget(self.run)

        self.tabs = QTabWidget()
        self.tabs.tabBar().setStyleSheet("QTabBar::tab { font-weight: bold; padding: 6px 28px; }")
        if self.calc is None:
            self.calc = CalcPanel(self.run, self.cur_workdir)
            self.calc.message.connect(self.statusBar().showMessage)
            # 运行进度放在常驻标签中：其他页的消息和菜单提示只替换左侧的临时消息
            self.progress_label = QLabel()
            self.statusBar().addPermanentWidget(self.progress_label)
            self.calc.progress.connect(self.progress_label.setText)
            self.calc.runFinished.connect(self.refresh_panels)
            self.calc.filesChanged.connect(self.refresh_panels)
            self.calc.workdirRequested.connect(self.change_workdir)
        else:
            self.calc.run = self.run
            self.calc.rebuild()
        self.pages = {}            # 种类 -> 标签页 (查看器面板放在其中)
        for kind, title in self.TABS:
            if kind == "calc":
                page = self.calc
            else:
                page = QWidget()
                QVBoxLayout(page).setContentsMargins(0, 0, 0, 0)
                if kind in self.panels:
                    page.layout().addWidget(self.panels[kind])
            self.pages[kind] = page
            self.tabs.addTab(page, T(title))
        detach = QToolButton()
        detach.setText(T("在新窗口中打开"))
        detach.setToolTip(T("在单独的窗口中打开当前的查看器，可以查看其他目录或与计算页并排"))
        detach.clicked.connect(self.detach)
        self.tabs.setCornerWidget(detach, Qt.TopRightCorner)
        self.detach_btn = detach
        self.tabs.currentChanged.connect(self.tab_changed)
        lay.addWidget(self.tabs, 1)
        self.setCentralWidget(central)
        self.statusBar().showMessage(T("就绪"))

    def build_menu(self):
        mb = self.menuBar()
        mb.clear()
        fm = mb.addMenu(T("文件"))
        calc = lambda name: lambda: getattr(self.calc, name)()      # noqa: E731 (self.calc 在菜单之后创建)
        for item in [("导入示例…", calc("import_demo")), ("导入配置文件…", calc("import_file")),
                     ("导出配置…", calc("export_config")), None,
                     ("保存参数…", calc("save_params")), ("载入参数…", calc("load_params")), None,
                     ("导出流程为 shell 脚本…", calc("export_script")),
                     ("导出 Slurm 作业脚本…", calc("export_slurm")), None]:
            if item is None:
                fm.addSeparator()
            else:
                fm.addAction(T(item[0])).triggered.connect(item[1])
        fm.addMenu(gc.recent_menu(fm, self.change_workdir, T("最近的工作目录")))
        fm.addSeparator()
        fm.addAction(T("退出")).triggered.connect(self.close)
        vm = mb.addMenu(T("视图"))
        for text, keys, step in [("放大字体", [QKeySequence.ZoomIn, "Ctrl+="], 1),
                                 ("缩小字体", [QKeySequence.ZoomOut], -1), ("默认字体大小", ["Ctrl+0"], 0)]:
            act = vm.addAction(T(text))
            act.setShortcuts([QKeySequence(k) for k in keys])
            act.triggered.connect(lambda _=False, d=step: self.change_font_size(d))
        vm.addSeparator()
        for i, (kind, title) in enumerate(self.TABS, 1):
            act = vm.addAction(T(title))
            act.setShortcut(QKeySequence(f"Ctrl+{i}"))
            act.triggered.connect(lambda _=False, k=kind: self.show_tab(k))
        wm = mb.addMenu(T("窗口"))
        for kind, title in self.TABS[1:]:
            wm.addAction(T("在新窗口中打开{t}", t=T(title))).triggered.connect(
                lambda _=False, k=kind: gc.PanelWindow.open(gc.PANEL_FACTORIES[k], self.cur_workdir))
        lm = mb.addMenu(T("语言") + " / Language" if core.LANG == "zh" else "Language / 语言")
        group = QActionGroup(self)
        for code, label in [("zh", "中文"), ("en", "English")]:
            act = QAction(label, self, checkable=True, checked=core.LANG == code)
            act.triggered.connect(lambda _=False, c=code: self.set_language(c))
            group.addAction(act)
            lm.addAction(act)

    def restore_layout(self):
        """恢复上次的窗口大小、计算页中参数区 / 输出区以及流程 / 程序输出的分隔位置。"""
        cfg = core.load_config()
        if cfg.get("geometry"):
            self.restoreGeometry(QByteArray.fromBase64(cfg["geometry"].encode()))
        if cfg.get("split"):
            self.calc.split.setSizes(cfg["split"])
        if len(cfg.get("bsplit") or []) == self.calc.bsplit.count():
            self.calc.bsplit.setSizes(cfg["bsplit"])

    def closeEvent(self, event):
        panels = list(self.panels.values()) + [w.panel for w in gc.PANEL_WINDOWS]
        others = any(p.busy() for p in panels)
        stop = False
        if self.calc.busy:
            # 计算由后台运行进程执行 (grasp_bg)：可以留在后台继续，以后打开该目录时接上
            box = QMessageBox(QMessageBox.Question, T("运行中"),
                              T("计算正在运行。可以让它在后台继续，以后再打开这个工作目录即可接上并查看结果。"), parent=self)
            notes = [T("能级图中正在进行的跃迁计算会结束。")] if others else []
            if grasp_bg.logout_kills():
                notes.append(T("注意：本机的 systemd 设置 (KillUserProcesses=yes) 会在这次登录结束 (ssh 断开或退出登录) 时"
                               "结束其中的全部进程，包括后台计算。解决办法见 docs/INTERFACE.md 的“后台运行”一节。"))
            if notes:
                box.setInformativeText("\n\n".join(notes))
            bg = box.addButton(T("后台继续"), QMessageBox.AcceptRole)
            halt = box.addButton(T("停止计算并退出"), QMessageBox.DestructiveRole)
            box.addButton(T("取消"), QMessageBox.RejectRole)
            box.setDefaultButton(bg)
            box.exec()
            if box.clickedButton() not in (bg, halt):
                event.ignore()
                return
            stop = box.clickedButton() is halt
        elif others:
            ans = QMessageBox.question(self, T("运行中"), T("有程序正在运行，退出将终止它。确定退出吗？"))
            if ans != QMessageBox.Yes:
                event.ignore()
                return
        self.calc.shutdown(stop)
        for p in self.panels.values():
            p.shutdown()
        for w in list(gc.PANEL_WINDOWS):
            w.close()
        self.run.save()
        core.save_config(geometry=bytes(self.saveGeometry().toBase64()).decode(), split=self.calc.split.sizes(),
                         bsplit=self.calc.bsplit.sizes(), main_tab=self.current_tab())
        event.accept()

    # ---- 标签页 ----
    def current_tab(self):
        return self.TABS[max(self.tabs.currentIndex(), 0)][0]

    def show_tab(self, kind):
        kinds = [k for k, _ in self.TABS]
        i = kinds.index(kind) if kind in kinds else 0
        if i == self.tabs.currentIndex():
            self.tab_changed()
        else:
            self.tabs.setCurrentIndex(i)            # 由 currentChanged 调用 tab_changed

    def tab_changed(self, *_):
        kind = self.current_tab()
        self.detach_btn.setVisible(kind != "calc")
        if kind != "calc":
            self.panel(kind).scan()             # 切换过来时刷新 (计算可能产生了新文件)

    def panel(self, kind):
        """查看器面板 (第一次用到时创建)。"""
        if kind not in self.panels:
            p = gc.PANEL_FACTORIES[kind](self.cur_workdir)
            p.host = self
            p.message.connect(lambda text, k=kind: self.panel_message(k, text))
            self.pages[kind].layout().addWidget(p)
            self.panels[kind] = p
            p.set_checked_paths(self.pending.pop(kind, []))
        return self.panels[kind]

    def panel_message(self, kind, text):
        """查看器的状态消息只在它的标签页显示时出现 (计算完成后后台刷新不覆盖计算页的消息)。"""
        if self.current_tab() == kind:
            self.statusBar().showMessage(text)

    def refresh_panels(self, *_):
        for p in self.panels.values():
            p.scan()

    def detach(self):
        kind = self.current_tab()
        if kind != "calc":
            win = gc.PanelWindow.open(gc.PANEL_FACTORIES[kind], self.cur_workdir)
            win.panel.set_checked_paths(self.panel(kind).checked_paths())

    # ---- 工作目录 ----
    def pick_workdir(self):
        d = QFileDialog.getExistingDirectory(self, "", self.cur_workdir)
        if d:
            self.change_workdir(d)

    def change_workdir(self, d):
        """切换工作目录：计算页载入该目录的记录，查看器显示该目录。"""
        new = os.path.abspath(os.path.expanduser(d.strip() or "."))
        self.workdir.setText(new)
        if new == self.cur_workdir:
            return
        self.cur_workdir = new
        self.calc.set_workdir(new)
        for p in self.panels.values():
            p.set_dir(new)
        core.save_config(workdir=new)
        gc.remember_dir(new)

    def open_path(self, path):
        """打开一个目录 (作为工作目录) 或文件 (切换到它所在的目录和对应的查看器，只勾选这个文件)。"""
        path = os.path.abspath(path)
        if os.path.isdir(path):
            self.change_workdir(path)
            return
        self.change_workdir(os.path.dirname(path))
        kind = gc.viewer_for(path)
        if kind:
            self.show_tab(kind)
            self.panel(kind).open_file(path)

    # ---- 语言和字号 ----
    def set_language(self, lang):
        if lang == core.LANG:
            return
        core.set_lang(lang)
        core.save_config(lang=lang)
        self.rebuild()

    def change_font_size(self, step):
        """step = +1 / -1 磅，0 恢复系统默认；保存在配置中，下次启动沿用。"""
        cur = QApplication.font().pointSizeF()
        size = DEFAULT_PT if step == 0 else min(max(cur + step, 6.0), 30.0)
        if size == cur:
            return
        core.save_config(font_size=None if step == 0 else size)
        self.rebuild()
        self.statusBar().showMessage(T("字体大小 {pt} 磅", pt=f"{size:g}"))

    def rebuild(self):
        """重新创建全部控件：计算页保留参数、流程和输出；查看器面板重新创建 (保留勾选的文件)，
        正在计算跃迁的面板保留原样。"""
        tab = self.current_tab()
        self.calc.setParent(None)
        for kind, p in list(self.panels.items()):
            p.setParent(None)
            if not p.busy():
                self.pending[kind] = p.checked_paths()
                p.deleteLater()
                del self.panels[kind]
        self.build()
        self.show_tab(tab)


# ---------------------------------------------------------------------------
#  启动
# ---------------------------------------------------------------------------

USAGE = """用法：
  grasp_gui [目录]           打开主界面 (默认为上次的工作目录)
  grasp_gui levels [目录]    打开主界面的能级图页 (默认为当前目录)
  grasp_gui wfn [目录]       打开主界面的径向波函数页 (默认为当前目录)
  grasp_gui 文件             按文件内容打开对应的页并只勾选这个文件：
                             混合系数文件 (.m、.cm) → 能级图，径向波函数文件 (.w) → 径向波函数
  grasp_gui check|script|steps|reference …   共享配置的命令行工具 (不打开窗口)，见 grasp_gui check --help
  选项 --lang zh|en          界面语言

以 grasp_levels 或 grasp_wfn 为名的符号链接直接打开对应的页，例如：
  ln -s /路径/grasp_gui ~/.local/bin/grasp_levels
  ln -s /路径/grasp_gui ~/.local/bin/grasp_wfn"""
LINK_NAMES = {"grasp_levels": "levels", "grasp_wfn": "wfn"}     # 以这些名字 (符号链接) 启动时直接打开对应的页


def start_window(args):
    """由命令行参数决定启动时的工作目录、标签页和要打开的文件：(目录, 标签页, 文件)；
    参数有误时为 None (已输出错误信息)。"""
    tab = LINK_NAMES.get(os.path.splitext(os.path.basename(sys.argv[0]))[0])
    if args and args[0] in ("levels", "wfn"):
        tab = args.pop(0)
    if not args:
        return (os.getcwd() if tab else None), tab, None
    path = os.path.abspath(args[0])
    if os.path.isfile(path):
        kind = gc.viewer_for(path)
        if kind is None:
            print(T("无法识别 {p}：不是 GRASP 的混合系数文件或径向波函数文件", p=args[0]), file=sys.stderr)
            return None
        return os.path.dirname(path), kind, path
    if tab and not os.path.isdir(path):
        print(T("找不到目录 {p}", p=args[0]), file=sys.stderr)
        return None
    return path, tab, None


class SplitterGrip(QObject):
    """画分隔条：比 Qt 默认的三个浅色小点更明显，鼠标停在上面时加亮。"""
    DOTS, SPACING, RADIUS = 5, 7, 2.0

    def eventFilter(self, handle, event):
        if event.type() != QEvent.Paint:
            return False
        p = QPainter(handle)
        p.setRenderHint(QPainter.Antialiasing)
        hover = handle.underMouse()
        p.fillRect(handle.rect(), QColor("#c3cede") if hover else QColor("#e2e6ec"))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#2f4566") if hover else QColor("#6f7b8e"))
        c = QPointF(handle.rect().center()) + QPointF(0.5, 0.5)
        along = QPointF(0, 1) if handle.orientation() == Qt.Horizontal else QPointF(1, 0)
        for i in range(self.DOTS):
            p.drawEllipse(c + along * ((i - (self.DOTS - 1) / 2) * self.SPACING), self.RADIUS, self.RADIUS)
        p.end()
        return True


class WindowStyle(QObject):
    """界面上不容易看清的部分：
    - 分隔条加宽，由 SplitterGrip 绘制；
    - frames 为真时 (Wayland，如 WSLg) 窗口的标题栏和边框由 Qt 自己绘制 (bradient 装饰)，颜色取自程序调色板的
      窗口背景色，与窗口内容相同，又没有窗口阴影，对话框叠在主窗口上时分不清边界。因此把程序调色板的
      窗口背景色换成 FRAME_COLOR (只有标题栏和边框直接使用程序调色板)，各窗口在显示前恢复原来的调色板。"""
    FRAME_COLOR = QColor("#c3cede")
    HANDLE_WIDTH = 9

    def __init__(self, app, frames):
        super().__init__(app)
        self.grip = SplitterGrip(self)
        self.normal = None
        if frames:
            framed = app.palette()
            # 每种颜色都显式设置：复制来的调色板不带“已设置”标记，设给窗口后仍会沿用程序调色板
            self.normal = QPalette()
            for group in (QPalette.Active, QPalette.Inactive, QPalette.Disabled):
                for role in range(QPalette.NColorRoles.value):
                    role = QPalette.ColorRole(role)
                    self.normal.setBrush(group, role, framed.brush(group, role))
            for group in (QPalette.Active, QPalette.Inactive):
                framed.setColor(group, QPalette.Window, self.FRAME_COLOR)
            app.setPalette(framed)
        app.installEventFilter(self)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Polish and obj.isWidgetType():
            if isinstance(obj, QSplitter):
                obj.setHandleWidth(self.HANDLE_WIDTH)
            elif isinstance(obj, QSplitterHandle):
                obj.setAttribute(Qt.WA_Hover)           # 鼠标进出时重画
                obj.installEventFilter(self.grip)
            if self.normal is not None and obj.isWindow():
                obj.setPalette(self.normal)
        return False


def main():
    args = sys.argv[1:]
    if "--lang" in args and args.index("--lang") + 1 < len(args):
        i = args.index("--lang")
        core.set_lang(args[i + 1])
        core.save_config(lang=core.LANG)
        del args[i:i + 2]
    else:
        core.set_lang(core.load_config().get("lang", "zh"))
    if args[:1] in (["-h"], ["--help"]):
        print(T(USAGE))
        return
    import grasp_config
    if args[:1] and args[0] in grasp_config.COMMANDS:      # 共享配置的命令行工具，不打开窗口
        sys.exit(grasp_config.cli(args))
    start = start_window(args)
    if start is None:
        sys.exit(2)
    workdir, tab, path = start
    app = QApplication(sys.argv[:1])
    app.setApplicationName("GRASP GUI")
    global DEFAULT_PT
    DEFAULT_PT = app.font().pointSizeF() if app.font().pointSizeF() > 0 else DEFAULT_PT
    load_fonts()
    app.window_style = WindowStyle(app, frames=app.platformName().startswith("wayland"))
    win = MainWindow(workdir, tab)
    win.show()
    if path:
        win.open_path(path)
    rc = app.exec()
    # 在 WSLg 的 Wayland 下，进程退出时 Qt 的收尾清理会卡住几十秒以上；
    # 参数在操作时已保存，运行中的程序已在 closeEvent 中结束，因此直接退出
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(rc)


if __name__ == "__main__":
    main()
