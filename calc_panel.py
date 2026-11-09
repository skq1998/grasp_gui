"""
计算页：每个 GRASP 程序一个参数表单 (字段与生成规则见 grasp_core.py)，自动生成交互输入 (stdin) 并在工作目录中
运行；多个步骤可以加入“流程”后顺序运行 (可选 MPI)，或导出为 shell 脚本 / Slurm 作业脚本。
"""

import codecs
import datetime
import difflib
import json
import os
import queue
import re
import threading
import time

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog, QFileDialog, QGridLayout, QHBoxLayout,
                               QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu, QMessageBox, QPlainTextEdit,
                               QPushButton, QScrollArea, QSplitter, QTabWidget, QTextBrowser, QVBoxLayout, QWidget)

import grasp_bg
import grasp_config
import grasp_core as core
import grasp_mix
import grasp_session as sessions
import grasp_summary as summary
import grasp_workdir as workdir_ops
from grasp_core import T
from gui_common import button, fixed_font, hint_label, recent_menu, text_edit


# ---------------------------------------------------------------------------
#  表单
# ---------------------------------------------------------------------------

class CsfListsWidget(QWidget):
    """rcsfgenerate 的多个 CSF 列表，每个列表一个标签页。"""

    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 6, 0, 0)
        bar = QHBoxLayout()
        bar.addWidget(QLabel(T("CSF 列表:")))
        bar.addWidget(button("+ 添加列表", lambda: self.add_list(dict(core.CsfLists.EMPTY))))
        bar.addWidget(button("− 删除当前列表", self.del_list))
        bar.addStretch()
        lay.addLayout(bar)
        self.tabs = QTabWidget()
        lay.addWidget(self.tabs)
        self.lists = []

    def add_list(self, d):
        page = QWidget()
        g = QGridLayout(page)
        lst = {"configs": text_edit(d["configs"], 4)}
        g.addWidget(QLabel(T("参考组态 (每行一个)")), 0, 0, Qt.AlignTop)
        g.addWidget(lst["configs"], 0, 1, 1, 2)
        for r, (key, label, hint) in enumerate(core.CsfLists.LIST_FIELDS, start=1):
            lst[key] = QLineEdit(d[key])
            g.addWidget(QLabel(T(label)), r, 0)
            g.addWidget(lst[key], r, 1)
            g.addWidget(hint_label(hint), r, 2)
        g.setColumnStretch(1, 1)
        self.lists.append(lst)
        self.tabs.addTab(page, T("列表 {n}", n=len(self.lists)))
        self.tabs.setCurrentWidget(page)

    def del_list(self):
        if len(self.lists) <= 1:
            return
        i = self.tabs.currentIndex()
        self.tabs.removeTab(i)
        self.lists.pop(i)
        for k in range(len(self.lists)):
            self.tabs.setTabText(k, T("列表 {n}", n=k + 1))

    def value(self):
        return [dict(configs=l["configs"].toPlainText(),
                     **{k: l[k].text() for k, _, _ in core.CsfLists.LIST_FIELDS}) for l in self.lists]

    def set_value(self, lists):
        self.tabs.clear()
        self.lists = []
        for d in lists:
            self.add_list(d)
        self.tabs.setCurrentIndex(0)


class StepForm(QScrollArea):
    """根据 step.fields 生成的参数表单。"""

    def __init__(self, step, bindir):
        super().__init__()
        self.step = step
        self.setWidgetResizable(True)
        inner = QWidget()
        g = QGridLayout(inner)
        g.setColumnStretch(1, 1)
        self.get, self.put = {}, {}
        controls = {}
        r = 0
        for f in step.fields:
            if isinstance(f, core.Note):
                w = QLabel(T(f.text))
                w.setWordWrap(True)
                w.setStyleSheet("color: #555; padding-bottom: 4px;")
                g.addWidget(w, r, 0, 1, 3)
            elif isinstance(f, core.Check):
                w = QCheckBox(T(f.label))
                g.addWidget(w, r, 0, 1, 2)
                g.addWidget(hint_label(f.hint), r, 2)
                self.get[f.key], self.put[f.key] = w.isChecked, w.setChecked
            elif isinstance(f, core.CsfLists):
                w = CsfListsWidget()
                g.addWidget(w, r, 0, 1, 3)
                self.get[f.key], self.put[f.key] = w.value, w.set_value
            else:
                if isinstance(f, core.Entry):
                    w = QLineEdit()
                    self.get[f.key], self.put[f.key] = w.text, w.setText
                elif isinstance(f, core.Text):
                    w = text_edit("", f.lines)
                    self.get[f.key], self.put[f.key] = w.toPlainText, w.setPlainText
                elif isinstance(f, core.Choice):
                    w = QComboBox()
                    for code, desc in f.options:
                        w.addItem(f"{code}   {T(desc)}", code)
                    self.get[f.key] = w.currentData
                    self.put[f.key] = lambda v, w=w: w.setCurrentIndex(max(w.findData(v), 0))
                elif isinstance(f, core.Program):
                    w = QComboBox()
                    w.setEditable(True)
                    if os.path.isdir(bindir):
                        w.addItems(sorted(os.listdir(bindir)))
                    self.get[f.key], self.put[f.key] = w.currentText, w.setCurrentText
                label = QLabel(T(f.label))
                g.addWidget(label, r, 0, Qt.AlignTop if isinstance(f, core.Text) else Qt.AlignVCenter)
                g.addWidget(w, r, 1)
                g.addWidget(hint_label(f.hint), r, 2)
            if isinstance(f, core.Field):
                controls[f.key] = w
            r += 1
        g.setRowStretch(r, 1)
        self.setWidget(inner)
        self.enabled_controls = []
        for f in step.fields:
            if getattr(f, "enabled_by", None):
                source, target = controls[f.enabled_by], controls[f.key]
                source.toggled.connect(target.setEnabled)
                self.enabled_controls.append((source, target))
        self.set_values(step.defaults())

    def values(self):
        return {k: get() for k, get in self.get.items()}

    def set_values(self, d):
        fields = {f.key: f for f in self.step.fields if isinstance(f, core.Field)}
        for k, val in d.items():
            if k in self.put:
                self.put[k](core.normalize(fields[k], val))
        for source, target in self.enabled_controls:
            target.setEnabled(source.isChecked())

    def job(self):
        return self.step.job(self.values())


def elapsed(seconds):
    """时间间隔，如 47 s、12:05、3:04:05。"""
    seconds = max(0, int(seconds))
    h, rest = divmod(seconds, 3600)
    m, sec = divmod(rest, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}" if m else f"{sec} s"


def run_summary(st, titles):
    """上次运行的结果，如“上次运行在第 3/7 步 rmcdhf 出错 (结束于 …)”：停下来的是哪一步，不必翻日志。"""
    i, n = st.get("step", 0), len(titles)
    title = titles[i] if 0 <= i < n else "?"
    end = st.get("end") or time.strftime("%Y-%m-%d %H:%M:%S")
    text = {"ok": T("上次运行完成：共 {n} 步", n=n),
            "stopped": T("上次运行在第 {i}/{n} 步 {t} 停止", i=i + 1, n=n, t=title),
            "failed": T("上次运行在第 {i}/{n} 步 {t} 出错", i=i + 1, n=n, t=title)}.get(
        st.get("status"), T("上次运行意外结束于第 {i}/{n} 步 {t}", i=i + 1, n=n, t=title))
    return text + T(" (结束于 {e})", e=end)


class ButtonColumn(QWidget):
    """按组排列的按钮：高度放得下时排成一列 (组间留空)，放不下时排成两列，面板变矮时不会被按钮撑住。"""
    GAP = 8

    def __init__(self, groups):
        super().__init__()
        self.groups = groups
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setVerticalSpacing(3)
        self.columns = 0
        self.arrange(1)

    def one_column_height(self):
        buttons = [b for g in self.groups for b in g]
        return (sum(b.sizeHint().height() for b in buttons) + 3 * (len(buttons) - 1)
                + self.GAP * (len(self.groups) - 1))

    def arrange(self, columns):
        if columns == self.columns:
            return
        self.columns = columns
        while self.grid.count():
            self.grid.takeAt(0)
        for r in range(self.grid.rowCount()):
            self.grid.setRowMinimumHeight(r, 0)
            self.grid.setRowStretch(r, 0)
        row = 0
        if columns == 1:
            for k, group in enumerate(self.groups):
                if k:
                    self.grid.setRowMinimumHeight(row, self.GAP - 3)
                    row += 1
                for b in group:
                    self.grid.addWidget(b, row, 0)
                    row += 1
        else:
            for i, b in enumerate(b for g in self.groups for b in g):
                self.grid.addWidget(b, i // 2, i % 2)
            row = (sum(len(g) for g in self.groups) + 1) // 2
        self.grid.setRowStretch(row, 1)

    def minimumSizeHint(self):
        size = super().minimumSizeHint()
        size.setHeight(0)           # 高度不足时改为两列，不由按钮撑高面板
        return size

    def resizeEvent(self, event):
        self.arrange(1 if event.size().height() >= self.one_column_height() else 2)
        super().resizeEvent(event)


# ---------------------------------------------------------------------------
#  Slurm 对话框
# ---------------------------------------------------------------------------

class SlurmDialog(QDialog):
    FIELDS = [("job", "作业名", ""), ("partition", "分区 (partition)", "空 = 默认分区"),
              ("nodes", "节点数", ""), ("ntasks", "每节点 MPI 进程数", ""),
              ("time", "时间限制", "如 2-00:00:00 (2 天)"), ("mem", "内存", "如 64G；空 = 不指定"),
              ("bindir", "服务器上的 GRASP bin 目录", ""),
              ("modules", "加载模块等命令", "每行一条，如 module load openmpi"),
              ("mpirun", "MPI 启动命令", ""), ("mpitmp", "MPI 临时目录 (MPI_TMP)", ""),
              ("mail", "邮件通知地址", "空 = 不通知"), ("extra", "其他 #SBATCH 选项", "每行一条，如 --account=xxx")]
    MULTILINE = ("modules", "extra")

    def __init__(self, parent, settings, submit=False):
        super().__init__(parent)
        self.setWindowTitle(T("提交 Slurm 作业") if submit else T("Slurm 作业脚本"))
        lay = QVBoxLayout(self)
        note = QLabel(T("脚本在服务器现有工作目录中通过 sbatch 提交；请核对分区、资源和模块命令。") if submit else
                      T("把当前流程导出为可直接 sbatch 提交的作业脚本。把脚本和工作目录中的输入文件 "
                        "(isodata, *.c, *.w …) 一起上传到服务器的同一目录，然后运行 sbatch 脚本名。"))
        note.setWordWrap(True)
        lay.addWidget(note)
        g = QGridLayout()
        g.setColumnStretch(1, 1)
        self.w = {}
        for r, (key, label, hint) in enumerate(self.FIELDS):
            if key in self.MULTILINE:
                w = text_edit(settings[key], 3)
            else:
                w = QLineEdit(settings[key])
                w.setMinimumWidth(320)
            self.w[key] = w
            g.addWidget(QLabel(T(label)), r, 0, Qt.AlignTop if key in self.MULTILINE else Qt.AlignVCenter)
            g.addWidget(w, r, 1)
            g.addWidget(hint_label(hint), r, 2)
        lay.addLayout(g)
        bar = QHBoxLayout()
        bar.addStretch()
        ok = button("提交" if submit else "导出…", self.accept)
        ok.setDefault(True)
        bar.addWidget(ok)
        bar.addWidget(button("取消", self.reject))
        lay.addLayout(bar)

    def settings(self):
        return {k: (w.toPlainText() if isinstance(w, QPlainTextEdit) else w.text()).strip()
                for k, w in self.w.items()}


# ---------------------------------------------------------------------------
#  共享配置：导入 (包括随程序提供的手册算例) 和导出
# ---------------------------------------------------------------------------

DEMO_ROOT = os.path.join(os.path.expanduser("~"), "grasp_demos")
CALC_ROOT = os.path.join(os.path.expanduser("~"), "grasp_calcs")


def free_dir(path):
    """path 不存在或为空时原样返回，否则依次尝试 path_2, path_3 …"""
    cand, k = path, 2
    while os.path.isdir(cand) and os.listdir(cand):
        cand, k = f"{path}_{k}", k + 1
    return cand


def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class ConfigDialog(QDialog):
    """选择一个配置，默认导入空目录，也可预览后更新当前目录。
    entries：[dict(cfg, warnings, path)]；剪贴板条目另带 clipboard=True，按外部配置确认。"""

    def __init__(self, parent, entries, bindir, current_dir=None):
        super().__init__(parent)
        self.setWindowTitle(T("导入配置"))
        self.resize(960, 620)
        self.entries, self.bindir = list(entries), bindir
        self.current_dir, self.new_target = current_dir, ""
        lay = QVBoxLayout(self)
        note = QLabel(T("配置只包含各程序的参数和计算流程 (以及少量附带的输入文件)，不含计算结果。导入到一个空目录后，"
                        "点“▶ 运行全部”即可从头计算；各程序页显示该程序在流程中最后一次使用的参数。"))
        note.setWordWrap(True)
        lay.addWidget(note)
        split = QSplitter()
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        self.list = QListWidget()
        self.list.setWordWrap(True)             # 标题较长：换行显示，不出现横向滚动条
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.setSpacing(2)
        ll.addWidget(self.list)
        source_buttons = QHBoxLayout()
        source_buttons.addWidget(button("打开配置文件…", self.open_file))
        source_buttons.addWidget(button("从剪贴板读入", self.open_clipboard))
        ll.addLayout(source_buttons)
        split.addWidget(left)
        self.info = QTextBrowser()
        split.addWidget(self.info)
        split.setStretchFactor(1, 2)
        split.setSizes([300, 640])
        lay.addWidget(split, 1)
        self.update = QCheckBox(T("更新当前工作目录"))
        self.update.setEnabled(current_dir is not None)
        self.update.toggled.connect(self.update_mode)
        lay.addWidget(self.update)
        row = QHBoxLayout()
        row.addWidget(QLabel(T("导入到目录")))
        self.dir = QLineEdit()
        self.dir.textEdited.connect(lambda: setattr(self, "dir_edited", True))
        row.addWidget(self.dir, 1)
        self.browse = button("浏览…", self.pick)
        row.addWidget(self.browse)
        lay.addLayout(row)
        bar = QHBoxLayout()
        bar.addStretch()
        self.ok = button("导入", self.accept)
        self.ok.setDefault(True)
        self.ok.setEnabled(bool(self.entries))
        bar.addWidget(self.ok)
        bar.addWidget(button("取消", self.reject))
        lay.addLayout(bar)
        self.dir_edited = False
        self.list.currentRowChanged.connect(self.show_entry)
        self.list.itemDoubleClicked.connect(lambda _: self.accept())
        for e in self.entries:
            self.add_item(e)
        self.list.setCurrentRow(0)

    def add_item(self, e):
        title = grasp_config.text(e["cfg"], "title") or os.path.basename(e["path"] or "")
        source = T("剪贴板") if e.get("clipboard") else os.path.basename(e["path"] or "")
        self.list.addItem(title if e["path"] is None and not e.get("clipboard") else f"{title}\n({source})")

    def open_file(self, path=None):
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, T("打开配置文件…"), os.path.expanduser("~"),
                                                  T("配置文件 (*.json)"))
        if not path:
            return False
        try:
            cfg, warnings = grasp_config.load(path)
        except grasp_config.ConfigError as e:
            QMessageBox.warning(self, T("导入配置"), str(e))
            return False
        self.add_entry(dict(cfg=cfg, warnings=warnings, path=os.path.abspath(path)))
        return True

    def open_clipboard(self):
        content = QGuiApplication.clipboard().text()
        try:
            cfg, warnings = grasp_config.loads(content)
        except grasp_config.ConfigError as e:
            preview = repr(content.strip()[:80])
            QMessageBox.warning(self, T("导入配置"), T("无法读取剪贴板：{e}\n内容开头：{text}", e=e, text=preview))
            return False
        self.add_entry(dict(cfg=cfg, warnings=warnings, path=None, clipboard=True))
        return True

    def add_entry(self, entry):
        self.entries.insert(0, entry)
        self.list.clear()
        for e in self.entries:
            self.add_item(e)
        self.dir_edited = False
        self.list.setCurrentRow(0)

    def entry(self):
        return self.entries[max(self.list.currentRow(), 0)]

    def show_entry(self, row):
        self.ok.setEnabled(row >= 0)
        if row < 0:
            return
        e = self.entries[row]
        d = e["cfg"]
        html = [f"<h3>{esc(grasp_config.text(d, 'title') or T('(无标题)'))}</h3>"]
        source = T("剪贴板") if e.get("clipboard") else (
            T("随程序提供的示例") if e["path"] is None else esc(e["path"]))
        html.append(f"<p style='color:gray'>{source}</p>")
        if grasp_config.text(d, "summary"):
            html.append(f"<p>{esc(grasp_config.text(d, 'summary'))}</p>")
        meta = [(T("作者"), d.get("author")), (T("日期"), d.get("created")),
                (T("预计时间"), grasp_config.text(d, "runtime")), (T("程序包"), d.get("package")),
                (T("导出自"), d.get("created_by")), (T("流程"), T("{n} 步", n=len(d["jobs"]))),
                (T("用到的程序"), ", ".join(grasp_config.programs(d)))]
        html.append("<p>" + "<br>".join(f"<b>{k}:</b> {esc(v)}" for k, v in meta if v) + "</p>")
        missing = grasp_config.missing_programs(d, self.bindir)
        if missing:
            html.append(f"<p style='color:#c00000'><b>{T('在 bin 目录和 PATH 中找不到')}:</b> "
                        f"{esc(', '.join(missing))}<br>{T('流程运行到这些程序时会出错。')}</p>")
            for step in core.STEPS:
                if step.prog in missing and getattr(step, "source_url", ""):
                    html.append(f"<p>{esc(T('外部程序 {p}：请从 {url} 安装', p=step.prog, url=step.source_url))}</p>")
        commands = grasp_config.custom_commands(d)
        if commands:
            html.append(f"<p style='color:#a05000'><b>{T('自定义命令')}</b> "
                        f"({T('可以运行任意程序；导入他人的配置前请确认这些命令是安全的')}):</p>"
                        "<pre style='background:#fff4e0'>" +
                        "\n".join(f"{i:2d}. " + esc(c).replace("\n", "\n    ") for i, c in commands) + "</pre>")
        files = d.get("files", [])
        if files:
            html.append(f"<p><b>{T('附带文件')}</b> ({T('导入时写入工作目录')}):</p><ul>" +
                        "".join(f"<li>{esc(f['name'])} ({len(f['text'].encode('utf-8'))} B)</li>" for f in files) +
                        "</ul>")
        if e["warnings"]:
            html.append(f"<p style='color:#a05000'><b>{T('兼容性')}:</b></p><ul>" +
                        "".join(f"<li>{esc(w)}</li>" for w in e["warnings"]) + "</ul>")
        refs = d.get("reference", [])
        if refs:
            html.append(f"<p><b>{T('参考结果')}</b> ({T('配置作者算出的能量，可与自己的结果对照')}):</p><ul>")
            for r in refs:
                en = r["energies"]
                html.append(f"<li>{esc(r['file'])}: {T('{n} 个能级', n=len(en))}"
                            + (f", {T('最低')} {min(en):.8f} Eh" if en else "") + "</li>")
            html.append("</ul>")
        self.info.setHtml("".join(html))
        if not self.dir_edited and not self.update.isChecked():
            if e.get("clipboard"):
                name = str(d.get("id") or grasp_config.text(d, "title") or "clipboard")
                stem = re.sub(r"[^\w.-]+", "_", name).strip("._") or "clipboard"
                self.dir.setText(free_dir(os.path.join(CALC_ROOT, stem)))
            elif e["path"] is None:
                self.dir.setText(free_dir(os.path.join(DEMO_ROOT, d.get("id") or "demo")))
            else:
                stem = os.path.basename(e["path"]).split(".")[0] or "calc"
                self.dir.setText(free_dir(os.path.join(CALC_ROOT, stem)))

    def accept(self):
        if not self.entries:
            return
        e = self.entry()
        commands = grasp_config.custom_commands(e["cfg"])
        if (e["path"] is not None or e.get("clipboard")) and commands and QMessageBox.question(
                self, T("导入配置"), T("这个配置含有 {n} 条自定义命令 (见右侧)，运行流程时会执行它们。确定导入吗？",
                                      n=len(commands))) != QMessageBox.Yes:
            return
        super().accept()

    def update_mode(self, enabled):
        if enabled:
            self.new_target = self.dir.text()
            self.dir.setText(self.current_dir or "")
        else:
            self.dir.setText(self.new_target)
        self.dir.setReadOnly(enabled)
        self.browse.setEnabled(not enabled)

    def pick(self):
        d = QFileDialog.getExistingDirectory(self, "", os.path.dirname(self.dir.text()) or os.path.expanduser("~"))
        if d:
            self.dir.setText(d)
            self.dir_edited = True

    def target(self):
        return os.path.abspath(os.path.expanduser(self.dir.text().strip()))


class SessionChangedDialog(QDialog):
    """外部记录默认载入，覆盖需要在调用方另行确认。"""

    def __init__(self, parent, text):
        super().__init__(parent)
        self.setWindowTitle(T("工作目录记录已改变"))
        self.resize(850, 560)
        self.choice = "load"
        lay = QVBoxLayout(self)
        label = QLabel(T("工作目录记录已被其他程序修改。请选择载入新记录，或保留并覆盖为当前修改。"))
        label.setWordWrap(True)
        lay.addWidget(label)
        summary = QPlainTextEdit(text)
        summary.setReadOnly(True)
        lay.addWidget(summary)
        bar = QHBoxLayout()
        load = button("载入新的记录", lambda: self.choose("load"))
        load.setDefault(True)
        bar.addWidget(load)
        bar.addWidget(button("保留我的修改", lambda: self.choose("keep")))
        bar.addStretch()
        bar.addWidget(button("取消", self.reject))
        lay.addLayout(bar)

    def choose(self, choice):
        self.choice = choice
        self.accept()


class UpdateDialog(QDialog):
    """应用前显示可复制的改动摘要；附带文件冲突逐项选择，默认保留。"""

    def __init__(self, parent, plan):
        super().__init__(parent)
        self.plan, self.run_added = plan, False
        self.setWindowTitle(T("更新当前工作目录"))
        self.resize(940, 760)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(plan["root"]))
        self.summary = QPlainTextEdit(plan["text"])
        self.summary.setReadOnly(True)
        lay.addWidget(self.summary, 2)
        copy = button("复制摘要", self.copy_summary)
        lay.addWidget(copy, alignment=Qt.AlignLeft)
        lay.addWidget(QLabel(T("附带文件：冲突默认保留原文件，勾选才覆盖")))
        self.files = QListWidget()
        labels = {"new": T("新增"), "same": T("内容相同"), "conflict": T("冲突（默认保留）")}
        for item in plan["files"]:
            row = QListWidgetItem(f"{item['name']} — {labels[item['kind']]}")
            row.setData(Qt.UserRole, item)
            if item["kind"] == "conflict":
                row.setFlags(row.flags() | Qt.ItemIsUserCheckable)
                row.setCheckState(Qt.Unchecked)
            self.files.addItem(row)
        lay.addWidget(self.files)
        self.diff = QPlainTextEdit()
        self.diff.setReadOnly(True)
        lay.addWidget(self.diff)
        self.files.currentRowChanged.connect(self.show_diff)
        self.files.setCurrentRow(0)
        bar = QHBoxLayout()
        self.added = button("更新并运行新增的步骤", lambda: self.accept_update(True))
        self.added.setVisible(plan["append_from"] is not None)
        self.added.setToolTip(T("覆盖已有输入时请先更新，再核对需要重算的步骤"))
        bar.addWidget(self.added)
        self.files.itemChanged.connect(self.overwrite_changed)
        bar.addStretch()
        bar.addWidget(button("只更新", lambda: self.accept_update(False)))
        bar.addWidget(button("取消", self.reject))
        lay.addLayout(bar)

    def copy_summary(self):
        self.summary.selectAll()
        self.summary.copy()

    def show_diff(self, row):
        if row < 0:
            self.diff.clear()
            return
        item = self.plan["files"][row]
        before = (item["previous"] or "").splitlines()
        after = item["text"].splitlines()
        self.diff.setPlainText("\n".join(difflib.unified_diff(before, after, fromfile=T("原文件"),
                                                          tofile=T("新配置"), lineterm="")))

    def overwrite(self):
        return {self.files.item(i).data(Qt.UserRole)["name"] for i in range(self.files.count())
                if self.files.item(i).checkState() == Qt.Checked}

    def overwrite_changed(self):
        self.added.setEnabled(not self.overwrite())

    def accept_update(self, run):
        if run and self.overwrite():
            return
        self.run_added = run
        self.accept()


def file_size(size):
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}"
        size /= 1024


class ClearDialog(QDialog):
    """永久删除前列出候选和保留项；全部删除需要再次输入目录名。"""

    def __init__(self, parent, workdir, allow_untracked=False):
        super().__init__(parent)
        self.workdir, self.allow_untracked = workdir, allow_untracked
        self.setWindowTitle(T("清空工作目录"))
        self.resize(850, 680)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(os.path.realpath(workdir)))
        warning = QLabel(T("永久删除，不能恢复。选中的子目录及其中的内容将一并删除；符号链接只删除链接本身。"))
        warning.setWordWrap(True)
        lay.addWidget(warning)
        self.all_files = QCheckBox(T("全部删除（默认只删除计算结果）"))
        lay.addWidget(self.all_files)
        self.note = QLabel(T("输入清单未知，未自动勾选普通文件；请选择要删除的计算结果。"))
        self.note.setWordWrap(True)
        lay.addWidget(self.note)
        split = QSplitter()
        self.files = QListWidget()
        self.kept = QListWidget()
        split.addWidget(self.files)
        split.addWidget(self.kept)
        lay.addWidget(QLabel(T("左侧：可删除项目（可取消勾选）；右侧：保留项目")))
        selection = QHBoxLayout()
        self.select_all = button("全选", lambda: self.select_files(True))
        self.select_none = button("全不选", lambda: self.select_files(False))
        selection.addWidget(self.select_all)
        selection.addWidget(self.select_none)
        selection.addStretch()
        lay.addLayout(selection)
        lay.addWidget(split, 1)
        self.total = QLabel()
        lay.addWidget(self.total)
        lay.addWidget(QLabel(T("全部删除时请输入目录名：{name}", name=os.path.basename(os.path.realpath(workdir)))))
        self.confirmation = QLineEdit()
        self.confirmation.textChanged.connect(self.count_changed)
        lay.addWidget(self.confirmation)
        bar = QHBoxLayout()
        bar.addStretch()
        self.ok = button("永久删除所选项目", self.accept)
        bar.addWidget(self.ok)
        bar.addWidget(button("取消", self.reject))
        lay.addLayout(bar)
        self.files.itemChanged.connect(self.count_changed)
        self.all_files.toggled.connect(self.refresh)
        self.refresh()

    def refresh(self):
        try:
            self.plan = workdir_ops.prepare_clear(self.workdir, self.all_files.isChecked(), self.allow_untracked)
        except (OSError, ValueError) as e:
            self.plan = None
            QMessageBox.warning(self, T("清空工作目录"), str(e))
            self.ok.setEnabled(False)
            self.reject()
            return
        self.note.setVisible(self.plan["unknown_inputs"])
        self.files.blockSignals(True)
        self.files.clear()
        for item in self.plan["entries"]:
            row = QListWidgetItem(f"{item['name']} — {item['count']} / {file_size(item['size'])}")
            row.setData(Qt.UserRole, item)
            row.setFlags(row.flags() | Qt.ItemIsUserCheckable)
            row.setCheckState(Qt.Checked if item["checked"] else Qt.Unchecked)
            self.files.addItem(row)
        self.files.blockSignals(False)
        self.kept.clear()
        self.kept.addItems([item["name"] for item in self.plan["kept"]])
        self.confirmation.setEnabled(self.all_files.isChecked())
        self.count_changed()

    def select_files(self, selected):
        self.files.blockSignals(True)
        for i in range(self.files.count()):
            self.files.item(i).setCheckState(Qt.Checked if selected else Qt.Unchecked)
        self.files.blockSignals(False)
        self.count_changed()

    def selected_names(self):
        return {self.files.item(i).data(Qt.UserRole)["name"] for i in range(self.files.count())
                if self.files.item(i).checkState() == Qt.Checked}

    def count_changed(self):
        if self.plan is None:
            self.ok.setEnabled(False)
            return
        selected = self.selected_names()
        count = sum(item["count"] for item in self.plan["entries"] if item["name"] in selected)
        size = sum(item["size"] for item in self.plan["entries"] if item["name"] in selected)
        self.total.setText(T("将删除 {n} 项，共 {size}", n=count, size=file_size(size)))
        confirmed = self.confirmation.text() == os.path.basename(self.plan["root"])
        self.ok.setEnabled(bool(selected) and (not self.all_files.isChecked() or confirmed))

    def accept(self):
        if self.ok.isEnabled():
            super().accept()


class ExportDialog(QDialog):
    """把当前目录的流程导出为共享配置：填写说明，选择参考结果和附带文件。"""

    def __init__(self, parent, workdir, pipeline, clipboard=False):
        super().__init__(parent)
        self.setWindowTitle(T("导出配置"))
        self.resize(820, 680)
        self.workdir, self.pipeline = workdir, pipeline
        self.to_clipboard, self.cfg = clipboard, None
        lay = QVBoxLayout(self)
        note = QLabel(T("配置保存当前流程中各步的参数，不含计算结果，也不含 GRASP bin 目录、MPI 进程数等与机器有关的设置。"
                        "别人导入后可以从头重复这个计算。"))
        note.setWordWrap(True)
        lay.addWidget(note)
        g = QGridLayout()
        g.setColumnStretch(1, 1)
        cfg = core.load_config()
        self.title = QLineEdit(os.path.basename(workdir.rstrip("/")))
        self.summary = text_edit("", 3)
        self.summary.setFont(self.font())
        self.author = QLineEdit(cfg.get("config_author", ""))
        self.runtime = QLineEdit()
        for r, (label, w, hint) in enumerate([("标题", self.title, ""), ("说明", self.summary, "计算的内容和目的"),
                                              ("作者", self.author, "可选"), ("预计时间", self.runtime, "可选，如“约 10 分钟”")]):
            g.addWidget(QLabel(T(label)), r, 0, Qt.AlignTop)
            g.addWidget(w, r, 1)
            g.addWidget(hint_label(hint), r, 2, Qt.AlignTop)
        lay.addLayout(g)

        lists = QHBoxLayout()
        rb = QVBoxLayout()
        rb.addWidget(QLabel(T("参考结果 (勾选的混合系数文件中的能级能量)")))
        self.refs = QListWidget()
        for name in grasp_mix.find_mix_files(workdir):
            it = QListWidgetItem(name)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if name.endswith(".cm") else Qt.Unchecked)
            self.refs.addItem(it)
        rb.addWidget(self.refs)
        lists.addLayout(rb)
        fb = QVBoxLayout()
        fb.addWidget(QLabel(T("附带文件 (界面不能生成的小文本输入文件，每个最多 {n} MB)", n=grasp_config.MAX_FILE >> 20)))
        self.files = QListWidget()
        fb.addWidget(self.files)
        frow = QHBoxLayout()
        frow.addWidget(button("添加…", self.add_files))
        frow.addWidget(button("删除", lambda: self.files.takeItem(self.files.currentRow())))
        frow.addStretch()
        fb.addLayout(frow)
        lists.addLayout(fb)
        lay.addLayout(lists, 1)

        lay.addWidget(QLabel(T("提示")))
        self.notes = QTextBrowser()
        self.notes.setMaximumHeight(130)
        lay.addWidget(self.notes)
        bar = QHBoxLayout()
        bar.addStretch()
        copy = button("复制到剪贴板", lambda: self.accept(True))
        copy.setDefault(clipboard)
        bar.addWidget(copy)
        ok = button("保存为文件…", self.accept)
        ok.setDefault(not clipboard)
        bar.addWidget(ok)
        bar.addWidget(button("取消", self.reject))
        lay.addLayout(bar)
        self.show_notes()

    def show_notes(self):
        cfg = dict(jobs=[grasp_config.export_job(j) for j in self.pipeline])
        notes = grasp_config.portability(cfg) or [T("没有发现可移植性问题。")]
        self.notes.setHtml("<ul>" + "".join(f"<li>{esc(n)}</li>" for n in notes) + "</ul>")

    def add_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, T("附带文件"), self.workdir)
        have = {self.files.item(i).data(Qt.UserRole) for i in range(self.files.count())}
        for p in paths:
            try:
                grasp_config.read_text_file(p)
            except (grasp_config.ConfigError, OSError) as e:
                QMessageBox.warning(self, T("附带文件"), str(e))
                continue
            if p not in have:
                it = QListWidgetItem(os.path.basename(p))
                it.setData(Qt.UserRole, p)
                self.files.addItem(it)

    def meta(self):
        return dict(title=self.title.text().strip(), summary=self.summary.toPlainText().strip(),
                    author=self.author.text().strip(), runtime=self.runtime.text().strip(),
                    created=datetime.date.today().isoformat())

    def file_paths(self):
        return [self.files.item(i).data(Qt.UserRole) for i in range(self.files.count())]

    def reference_paths(self):
        return [os.path.join(self.workdir, self.refs.item(i).text()) for i in range(self.refs.count())
                if self.refs.item(i).checkState() == Qt.Checked]

    def accept(self, clipboard=False):
        if not self.title.text().strip():
            QMessageBox.information(self, T("导出配置"), T("请填写标题。"))
            return
        try:
            cfg = grasp_config.export(self.pipeline, self.meta(), self.file_paths(), self.reference_paths())
            content = grasp_config.dumps(cfg)
        except (grasp_config.ConfigError, OSError, ValueError) as e:
            QMessageBox.warning(self, T("导出配置"), str(e))
            return
        if clipboard and len(content.encode("utf-8")) > grasp_config.MAX_CLIPBOARD:
            QMessageBox.warning(self, T("导出配置"), T("配置太大，无法复制到剪贴板；请保存为文件。"))
            return
        self.cfg, self.to_clipboard = cfg, clipboard
        if clipboard:
            QGuiApplication.clipboard().setText(content)
        core.save_config(config_author=self.author.text().strip())
        super().accept()


# ---------------------------------------------------------------------------
#  程序页：上层按功能分组，下层是组内的程序 (每层都能一行放下，无需滚动)
# ---------------------------------------------------------------------------

class StepNav(QWidget):
    def __init__(self, forms):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.groups = QTabWidget()
        self.groups.tabBar().setStyleSheet("QTabBar::tab { font-weight: bold; padding: 5px 18px; }")
        by_key = {f.step.key: f for f in forms}
        self.inner = {}                         # 程序 key -> 所在分组的下层标签页
        for title, keys in core.STEP_GROUPS:
            tabs = QTabWidget()
            tabs.setDocumentMode(True)
            for key in keys:
                tabs.addTab(by_key[key], T(by_key[key].step.name))
                self.inner[key] = tabs
            self.groups.addTab(tabs, T(title).replace("&", "&&"))     # "&" 在标签里是快捷键标记
        lay.addWidget(self.groups)

    def currentWidget(self):
        return self.groups.currentWidget().currentWidget()

    def setCurrentWidget(self, form):
        tabs = self.inner[form.step.key]
        self.groups.setCurrentWidget(tabs)
        tabs.setCurrentWidget(form)

    def setText(self, form, text):
        tabs = self.inner[form.step.key]
        tabs.setTabText(tabs.indexOf(form), text)

    def text(self, form):
        tabs = self.inner[form.step.key]
        return tabs.tabText(tabs.indexOf(form))


# ---------------------------------------------------------------------------
#  计算页
# ---------------------------------------------------------------------------

TAG_COLORS = {"": QColor("#dcdcdc"), "head": QColor("#6cb6ff"), "info": QColor("#a0d080"), "err": QColor("#ff7070")}


class CalcPanel(QWidget):
    """计算：各程序的参数表单、流程、运行和程序输出。工作目录和运行设置 (RunSettings) 由主窗口提供；
    参数和流程按工作目录保存在 core.SESSION_FILE 中。"""
    message = Signal(str)                  # 状态栏消息
    progress = Signal(str)                 # 运行进度 (状态栏右侧的常驻标签；空字符串 = 没有运行)
    filesChanged = Signal()                # 更新目录内容后刷新查看器
    runFinished = Signal(bool)             # 一次运行结束 (查看器据此刷新)
    workdirRequested = Signal(str)         # 需要切换工作目录 (导入示例、载入参数)
    copyChanged = Signal(str)

    def __init__(self, run, workdir):
        super().__init__()
        self.run = run
        self.cur_workdir = os.path.abspath(workdir)
        self.q = queue.Queue()
        self.client = None             # 与后台运行进程的连接 (见 grasp_bg)；连着时 busy
        self.titles = []               # 正在运行的各步标题
        self.last_run = ""             # 本目录上次运行的结果 (结束后留在状态栏的常驻标签中)
        self.remote = None             # 本目录的计算在另一台机器上运行时：读取其 RUN_LOG 的线程的标记
        self.pipeline = []
        self.session_cleared = False
        self.session_revision = None
        self.checking_session = False
        self.summary_runs = {}
        self.summary_record = None
        self.busy = False
        self.group = core.GROUPS[0]
        self.groups = {g: core.group_defaults(g) for g in core.GROUPS}
        self.slurm = dict(core.SLURM_DEFAULTS, **core.load_config().get("slurm", {}))
        self.formats = {}
        for tag, color in TAG_COLORS.items():
            fmt = QTextCharFormat()
            fmt.setForeground(color)
            if tag == "head":
                fmt.setFontWeight(QFont.Bold)
            self.formats[tag] = fmt
        self.body = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.build()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(100)
        self.ptimer = QTimer(self)
        self.ptimer.timeout.connect(self.update_progress)
        self.ptimer.start(1000)

    def status(self, text):
        self.message.emit(text)

    def update_progress(self):
        """运行中：当前步骤、本步已运行的时间和最后一次输出距今的时间 (每秒更新)。
        放在常驻标签中，切换标签页或菜单提示不会把它清掉；GRASP 长时间没有输出时也能看出计算仍在进行。"""
        if not self.busy:
            self.progress.emit(self.last_run)
            return
        st = grasp_bg.status(self.cur_workdir) or {}
        i, n = st.get("step", 0), len(self.titles)
        title = self.titles[i] if 0 <= i < n else "?"
        now = time.time()
        text = T("运行中：第 {i}/{n} 步 {t}", i=i + 1, n=n, t=title)
        if st.get("step_start"):
            text += T("，本步 {d}", d=elapsed(now - st["step_start"]))
        try:
            quiet = now - os.path.getmtime(os.path.join(self.cur_workdir, grasp_bg.RUN_LOG))
            if quiet >= 60:
                text += T("，最后输出于 {d} 前", d=elapsed(quiet))
        except OSError:
            pass
        self.progress.emit(text)

    # ---- 布局 ----
    def build(self):
        """创建全部控件；切换语言或字号时重新调用 (见 rebuild)。"""
        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(0, 0, 0, 0)
        self.copy_label = QLabel()
        self.copy_label.setWordWrap(True)
        lay.addWidget(self.copy_label)
        self.refresh_remote_copy()
        self.split = QSplitter(Qt.Vertical)
        self.split.addWidget(self.build_steps())
        self.split.addWidget(self.build_bottom())
        self.split.setStretchFactor(0, 3)
        self.split.setStretchFactor(1, 1)
        self.split.setSizes([620, 240])         # 默认给参数区更多空间
        lay.addWidget(self.split)
        if self.body is not None:
            self.layout().removeWidget(self.body)
            self.body.deleteLater()
        self.body = body
        self.layout().addWidget(body)

    def rebuild(self):
        """重新创建全部控件，保留参数、流程和输出。"""
        data = self.collect()
        output = self.out.toPlainText()
        sizes = self.split.sizes()
        self.build()
        self.split.setSizes(sizes)
        self.apply(data)
        self.append(output, "")

    def build_steps(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)

        # 态组切换放在分组标签栏右侧的空白处，不单独占一行
        corner = QWidget()
        gb = QHBoxLayout(corner)
        gb.setContentsMargins(0, 0, 4, 2)
        group_hint = T("标签上带组名的程序页按态组分别保存参数，其余程序页两组共用。")
        label = QLabel(T("态组"))
        label.setToolTip(group_hint)
        gb.addWidget(label)
        self.group_buttons = QButtonGroup(w)
        for g in core.GROUPS:
            b = QPushButton(g)
            b.setCheckable(True)
            b.setChecked(g == self.group)
            b.setToolTip(group_hint)
            b.setStyleSheet("QPushButton { padding: 2px 16px; } "
                            "QPushButton:checked { background: #2d6cdf; color: white; font-weight: bold; }")
            b.clicked.connect(lambda _=False, g=g: self.switch_group(g))
            self.group_buttons.addButton(b)
            gb.addWidget(b)
        self.copy_btn = QPushButton()
        self.copy_btn.clicked.connect(self.copy_group)
        gb.addWidget(self.copy_btn)

        self.forms = [StepForm(step, self.run.bindir()) for step in core.STEPS]
        self.nav = StepNav(self.forms)
        self.nav.groups.setCornerWidget(corner, Qt.TopRightCorner)
        for f in self.group_forms():
            f.set_values(self.groups[self.group][f.step.key])
        self.update_group_labels()
        lay.addWidget(self.nav)
        bar = QHBoxLayout()
        bar.addWidget(button("预览输入", self.preview))
        check = button("检查输入文件", self.check_inputs)
        check.setToolTip(T("检查流程中从选中开始的步骤；流程为空时检查当前表单。"))
        bar.addWidget(check)
        bar.addWidget(button("▶ 运行当前程序", self.run_current))
        bar.addWidget(button("加入流程 ↓", self.add_to_pipeline))
        bar.addStretch()
        bar.addWidget(button("■ 停止", self.stop_run))
        lay.addLayout(bar)
        return w

    def build_bottom(self):
        split = QSplitter(Qt.Horizontal)
        self.btabs = QTabWidget()

        pw = QWidget()
        pl = QHBoxLayout(pw)
        self.plist = QListWidget()
        self.plist.itemDoubleClicked.connect(self.show_job)
        pl.addWidget(self.plist, 1)
        # 按钮按用途分组；高度够时排成一列，不够时自动排成两列
        export = QPushButton(T("导出"))         # 带菜单的按钮会自动显示下拉箭头
        menu = QMenu(export)
        menu.addAction(T("导出脚本…")).triggered.connect(self.export_script)
        menu.addAction(T("导出 Slurm 作业脚本…")).triggered.connect(self.export_slurm)
        menu.addAction(T("复制配置到剪贴板…")).triggered.connect(lambda: self.export_config(True))
        export.setMenu(menu)
        pl.addWidget(ButtonColumn([[button("▶ 运行全部", self.run_pipeline), button("▶ 从选中开始", self.run_from_selected),
                                    button("上传到服务器…", lambda: self.upload_remote()),
                                    button("上传并运行…", lambda: self.upload_remote(True))],
                                   [button("编辑", self.edit_job), button("更新所选", self.update_job),
                                    button("查看", self.show_job)],
                                   [button("上移", lambda: self.move(-1)), button("下移", lambda: self.move(1))],
                                   [button("删除", self.remove_job), button("清空", self.clear_pipeline)],
                                   [export]]))
        self.btabs.addTab(pw, T("流程"))

        self.prev = QPlainTextEdit()
        self.prev.setFont(fixed_font())
        self.btabs.addTab(self.prev, T("输入预览"))
        split.addWidget(self.btabs)
        # 下方足够宽时，输入预览单独成一栏，与流程、程序输出并列 (见 set_preview_column)
        self.pbox = QWidget()
        pv = QVBoxLayout(self.pbox)
        pv.setContentsMargins(0, 0, 0, 0)
        pv.addWidget(QLabel(T("输入预览")))
        self.pbox.hide()
        split.addWidget(self.pbox)

        ow = QWidget()
        ol = QVBoxLayout(ow)
        ol.setContentsMargins(0, 0, 0, 0)
        top = QHBoxLayout()
        top.addWidget(QLabel(T("程序输出")))
        top.addWidget(hint_label(T("（只显示最后 {n} 行，完整输出请设置日志文件）", n=core.MAX_OUT_LINES)))
        top.addStretch()
        directory = QPushButton(T("工作目录"))
        menu = QMenu(directory)
        menu.addAction(T("打开工作目录…")).triggered.connect(self.pick_workdir)
        menu.addMenu(recent_menu(menu, self.workdirRequested.emit, T("最近的工作目录"), self.pick_workdir))
        menu.addSeparator()
        menu.addAction(T("用配置更新当前目录…")).triggered.connect(lambda: self.import_config(False, update=True))
        menu.addAction(T("恢复上一次的工作目录记录")).triggered.connect(self.restore_workdir)
        menu.addAction(T("清空工作目录…")).triggered.connect(self.clear_workdir)
        directory.setMenu(menu)
        top.addWidget(directory)
        top.addWidget(button("查看文件", self.list_files))
        top.addWidget(button("清屏", lambda: self.out.clear()))
        ol.addLayout(top)
        self.out = QPlainTextEdit()
        self.out.setReadOnly(True)
        self.out.setFont(fixed_font())
        self.out.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.out.setMaximumBlockCount(core.MAX_OUT_LINES)   # 超出部分由 Qt 自动丢弃
        self.out.setStyleSheet("QPlainTextEdit { background: #1e1e1e; color: #dcdcdc; }")
        ol.addWidget(self.out)
        split.addWidget(ow)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 1)
        split.setStretchFactor(2, 2)
        self.bsplit = split
        self.preview_column = False
        split.installEventFilter(self)
        self.refresh_list()
        return split

    PREVIEW_COLUMN_WIDTH = 1300     # 下方区域至少这么宽时，输入预览单独成一栏

    def eventFilter(self, obj, event):
        if obj is getattr(self, "bsplit", None) and event.type() == QEvent.Resize:
            self.set_preview_column(event.size().width() >= self.PREVIEW_COLUMN_WIDTH)
        return super().eventFilter(obj, event)

    def set_preview_column(self, wide):
        """宽时输入预览在流程和程序输出之间单独成一栏；窄时回到流程旁的标签页。"""
        if wide == self.preview_column:
            return
        self.preview_column = wide
        if wide:
            self.btabs.removeTab(self.btabs.indexOf(self.prev))
            self.pbox.layout().addWidget(self.prev)
            self.prev.show()
            self.pbox.show()
            sizes = self.bsplit.sizes()
            if not sizes[1]:                    # 第一次展开：三栏约为 1 : 1 : 1.5
                total = sum(sizes)
                self.bsplit.setSizes([total * 2 // 7, total * 2 // 7, total - total * 4 // 7])
        else:
            self.pbox.hide()
            self.btabs.addTab(self.prev, T("输入预览"))

    def show_preview(self):
        """显示输入预览：并列时它本来就可见，否则切换到它的标签页。"""
        if not self.preview_column:
            self.btabs.setCurrentWidget(self.prev)

    def shutdown(self, stop=False):
        """关闭主窗口前：保存本目录的记录；运行中的计算在后台继续，stop 为真时停止它。"""
        if not self.save_session():
            return False
        if self.busy and stop:
            self.stop_run()
        if getattr(self, "upload_dialog", None) is not None:
            self.upload_dialog.shutdown()
        self.detach()
        return True

    # ---- 态组 ----
    def group_forms(self):
        return [f for f in self.forms if f.step.key in core.GROUP_STEPS]

    def other_group(self):
        return next(g for g in core.GROUPS if g != self.group)

    def flush_group(self):
        """把表单中当前态组的参数存回 self.groups。"""
        for f in self.group_forms():
            self.groups[self.group][f.step.key] = f.values()

    def load_group(self):
        for f in self.group_forms():
            f.set_values(self.groups[self.group][f.step.key])
        self.update_group_labels()

    def update_group_labels(self):
        for b in self.group_buttons.buttons():
            b.setChecked(b.text() == self.group)
        self.copy_btn.setText(T("复制到 {g}（替换名称）", g=self.other_group()))
        for f in self.forms:
            name = T(f.step.name)
            self.nav.setText(f, f"{name} · {self.group}" if f.step.key in core.GROUP_STEPS else name)

    def switch_group(self, g):
        if g == self.group:
            return
        self.flush_group()
        self.group = g
        self.load_group()
        self.status(T("当前态组：{g}", g=g))
        self.save_session()

    def copy_group(self):
        a, b = self.group, self.other_group()
        if QMessageBox.question(self, T("态组"), T("确定用 {a} 组的参数覆盖 {b} 组吗？", a=a, b=b)) != QMessageBox.Yes:
            return
        self.flush_group()
        self.groups[b] = core.rename_group(self.groups[a], a, b)
        self.status(
            T("已把 {a} 组的参数复制到 {b} 组 (名称中的 {a} 已替换为 {b})；请检查 {b} 的参考组态、2J 范围和能级。", a=a, b=b))
        self.save_session()

    # ---- 工作目录中的记录 ----
    def set_workdir(self, d):
        """切换工作目录：先保存旧目录的记录，再载入新目录的记录。"""
        d = os.path.abspath(d)
        if d == self.cur_workdir:
            return True
        if not self.save_session():
            return False
        if self.busy:
            self.write("\n" + T("[原目录 {d} 的计算在后台继续；再次打开该目录即可接上]", d=self.cur_workdir) + "\n", "info")
        self.detach()
        self.cur_workdir = d
        self.session_cleared = False
        self.last_run = ""
        self.open_workdir(d)
        return True

    def open_workdir(self, d):
        self.refresh_remote_copy()
        if not os.path.isdir(d):
            return
        try:
            data, self.session_revision = sessions.read(d)
        except (OSError, ValueError) as e:
            QMessageBox.warning(self, T("工作目录记录"), str(e))
            return
        if data:
            self.session_cleared = False
            self.apply(data)
            self.status(T("已载入此目录上次的记录") + f"  ({os.path.join(d, core.SESSION_FILE)})")
        else:
            self.status(T("此目录还没有记录，沿用当前参数"))
        self.show_background(d)

    def check_external(self):
        if self.checking_session or self.session_cleared:
            return "unchanged"
        self.checking_session = True
        try:
            data, revision = sessions.read(self.cur_workdir)
            if revision == self.session_revision:
                return "unchanged"
            local = self.collect()
            dlg = SessionChangedDialog(self, workdir_ops.compare(local, data or {})["text"])
            if dlg.exec() != QDialog.Accepted:
                return "cancel"
            if dlg.choice == "load":
                self.session_revision = revision
                self.apply(data or dict(steps={s.key: s.defaults() for s in core.STEPS}, groups={}, pipeline=[]))
                self.filesChanged.emit()
                self.status(T("已载入外部修改的记录；新增步骤由用户决定是否运行"))
                return "loaded"
            if QMessageBox.question(self, T("覆盖外部记录"),
                    T("确定用当前修改覆盖外部记录吗？"), QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No) != QMessageBox.Yes:
                return "cancel"
            self._save_record(revision)
            return "saved"
        except sessions.Conflict as e:
            QMessageBox.warning(self, T("工作目录记录"), str(e))
            return "cancel"
        except (OSError, ValueError, KeyError) as e:
            QMessageBox.warning(self, T("工作目录记录"), str(e))
            return "unavailable"
        finally:
            self.checking_session = False

    def _save_record(self, expected):
        data = self.collect()
        for key in ("workdir", "bindir"):
            data.pop(key)
        self.session_revision = sessions.write(self.cur_workdir, data, expected)

    def save_session(self, require_saved=False):
        if self.session_cleared:
            return True
        action = self.check_external()
        if action == "cancel":
            return False
        if action == "unavailable":
            return not require_saved
        if action in ("loaded", "saved"):
            return True
        try:
            self._save_record(self.session_revision)
        except sessions.Conflict as e:
            QMessageBox.warning(self, T("工作目录记录"), str(e))
            return False
        except (OSError, ValueError) as e:
            QMessageBox.warning(self, T("工作目录记录"), str(e))
            return not require_saved
        return True

    # ---- 输出 ----
    def poll(self):
        # 每次把队列中的输出全部取出，合并相同 tag 的连续片段后一次插入
        segs = []
        try:
            while True:
                s, tag = self.q.get_nowait()
                if s == T("[此计算由 MCP agent 启动]") + "\n" and not core.load_config().get("mcp_enabled", False):
                    continue
                if tag in ("done", "step"):
                    if segs:                       # 先显示此前的输出
                        self.q_segments(segs)
                        segs = []
                    if tag == "done":
                        self.finished(s)
                    else:
                        self.show_step(s)
                elif segs and segs[-1][1] == tag:
                    segs[-1][0].append(s)
                else:
                    segs.append(([s], tag))
        except queue.Empty:
            pass
        self.q_segments(segs)

    def q_segments(self, segs):
        for parts, tag in segs:
            text = "".join(parts)
            if text.count("\n") > core.MAX_OUT_LINES:
                text = "\n".join(text.split("\n")[-core.MAX_OUT_LINES - 1:])
            self.append(text, tag)

    def append(self, text, tag):
        if not text:
            return
        bar = self.out.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - 2
        cur = QTextCursor(self.out.document())
        cur.movePosition(QTextCursor.End)
        cur.insertText(text, self.formats[tag])
        if at_bottom:   # 用户向上翻看时不强行滚到底
            bar.setValue(bar.maximum())

    def write(self, s, tag=""):
        self.q.put((s, tag))

    # ---- 动作 ----
    def current_job(self):
        try:
            return self.nav.currentWidget().job()
        except (ValueError, KeyError) as e:
            QMessageBox.critical(self, T("参数错误"), T("参数格式有误：{e}", e=e))
            return None

    def preview(self):
        job = self.current_job()
        if job:
            self.prev.setPlainText(core.describe(job, self.run.mpi_prefix()) + "\n")
            self.show_preview()

    def check_inputs(self):
        form = self.nav.currentWidget()
        jobs = self.pipeline[self.selected() or 0:] if self.pipeline else [
            dict(step=form.step.key, vals=form.values(), prog=form.step.prog)]
        if not all(jobs) or not self.check_dirs():
            return
        try:
            result = summary.check_files(self.cur_workdir, jobs, run=self.run.values(),
                                         offset=summary.locate(self.pipeline, jobs) or 0)
        except (OSError, ValueError) as e:
            QMessageBox.warning(self, T("检查输入文件"), str(e))
            return
        from grasp_remote_edit import association
        deferred = result["missing"] if association(self.cur_workdir) else []
        if deferred:
            result = dict(result, missing=[], issues=result["invalid"])
        text = summary.check_text(result) if result["issues"] else T("没有发现缺失的输入文件或名称问题。")
        if deferred:
            text += "\n" + T("由服务器检查：{names}", names=", ".join(row["file"] for row in deferred))
        if not result["issues"] and result["skipped"]:
            text += "\n" + "\n".join(T("第 {n} 步 {p}：{why}", n=r["step"], p=r["program"], why=r["reason"])
                                       for r in result["skipped"])
        QMessageBox.information(self, T("检查输入文件"), text)

    def check_dirs(self):
        if not os.path.isdir(self.cur_workdir):
            QMessageBox.critical(self, T("错误"), T("工作目录不存在：{d}", d=self.cur_workdir))
            return False
        return True

    def start(self, jobs):
        if self.busy and not self.remote:
            QMessageBox.information(self, T("运行中"), T("已有任务在运行，请等待或先停止。"))
            return
        if not jobs or not self.check_dirs():
            return
        from grasp_remote_edit import association
        link = association(self.cur_workdir)
        if link and QMessageBox.question(self, T("运行服务器副本"),
                T("这是 {h}:{p} 的本机副本，确定在本机运行吗？", h=link["host"], p=link["directory"]),
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        st = grasp_bg.status(self.cur_workdir)
        if st and st["status"] == "running" and st["remote"] and QMessageBox.question(
                self, T("运行中"), T("此目录的计算正在 {h} 上运行 (开始于 {t})。仍要在本机开始新的计算吗？",
                                    h=st.get("host", "?"), t=st.get("start", "?"))) != QMessageBox.Yes:
            return
        cfg = dict(self.run.values(), cwd=self.cur_workdir)
        self.session_cleared = False
        if self.check_external() not in ("unchanged", "saved"):
            return
        self.run.save()
        if not self.save_session(require_saved=True):
            return
        first = summary.locate(self.pipeline, jobs)
        try:
            checked = summary.check_files(self.cur_workdir, jobs, run=cfg, offset=first or 0)
        except (OSError, ValueError) as e:
            QMessageBox.warning(self, T("检查输入文件"), str(e))
            return
        if checked["invalid"]:
            QMessageBox.warning(self, T("名称或参数不合法，不能运行"), summary.check_text(checked))
            return
        if checked["missing"] and QMessageBox.question(
                self, T("缺少输入文件"), summary.check_text(checked) + "\n\n" + T("仍然运行吗？"),
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        if checked["skipped"]:
            self.write(T("[文件检查：{n} 个步骤未声明依赖，需人工核查]", n=len(checked["skipped"])) + "\n", "info")
        self.detach()                      # 不再读取另一台机器上的计算的输出
        try:
            proc = grasp_bg.launch(self.cur_workdir, jobs, cfg)
        except (OSError, RuntimeError) as e:
            QMessageBox.warning(self, T("运行"), str(e))
            return
        threading.Thread(target=proc.wait, daemon=True).start()    # 运行进程结束后回收
        sock = grasp_bg.connect(self.cur_workdir, timeout=10)
        if sock is None:
            QMessageBox.warning(self, T("运行"), T("无法启动后台运行进程"))
            return
        if first is not None:
            state = grasp_bg.status(self.cur_workdir) or dict(status="running", step=0)
            self.summary_record = dict(first=first, keys=[summary.job_key(j) for j in jobs], state=state,
                                       basis=T("本次会话记录；按步骤参数指纹对应"))
            records = self.summary_runs.setdefault(self.cur_workdir, [])
            records.append(self.summary_record)
            del records[:-50]
        self.attach(sock, [j["title"] for j in jobs])

    # ---- 后台运行 (grasp_bg)：计算由独立的运行进程执行，本页只是查看者 ----
    def attach(self, sock, titles):
        """连上运行进程：读取它发来的输出 (先补发此前的输出，然后是实时输出)。"""
        self.client, self.titles, self.busy = sock, titles, True
        self.status(T("运行中：{n} 个步骤 …", n=len(titles)))
        threading.Thread(target=self.read_client, args=(sock,), daemon=True).start()

    def read_client(self, sock):
        done = None
        try:
            for line in sock.makefile("rb"):
                m = json.loads(line)
                if "s" in m:
                    self.q.put((m["s"], m.get("tag", "") if m.get("tag", "") in TAG_COLORS else ""))
                elif "step" in m:
                    self.q.put((m["step"], "step"))
                elif "done" in m:
                    done = m["done"]
        except (OSError, ValueError):
            pass
        if sock is self.client:            # 不是本页主动断开 (detach)
            self.q.put((done or "lost", "done"))

    def detach(self):
        """断开与运行进程的连接；计算在后台继续。"""
        sock, self.client = self.client, None
        self.remote = None
        self.busy = False
        self.summary_record = None
        if sock:
            try:
                sock.close()
            except OSError:
                pass

    def stop_run(self):
        if self.client:
            try:
                self.client.sendall(b"stop\n")
            except OSError:
                pass
        elif self.remote:
            st = grasp_bg.status(self.cur_workdir) or {}
            QMessageBox.information(self, T("停止"), T("计算在 {h} 上运行 (进程 {p})，只能在那台机器上停止。",
                                                      h=st.get("host", "?"), p=st.get("pid", "?")))

    def show_step(self, _i):
        if self.summary_record is not None:
            self.summary_record["state"]["step"] = _i
        self.update_progress()              # 进度只显示在常驻标签中，不再另发一条状态栏消息

    def finished(self, result):
        if not self.busy:
            return
        titles = self.titles
        if self.summary_record is not None:
            self.summary_record["state"] = dict(grasp_bg.status(self.cur_workdir) or {}, status=result)
        self.detach()
        ok = result == "ok"
        if result == "lost":
            self.write("\n" + T("*** 后台运行进程意外结束") + "\n", "err")
        st = dict(grasp_bg.status(self.cur_workdir) or {}, status=result)
        self.last_run = run_summary(st, titles)
        if not ok:
            self.write("\n*** " + self.last_run + "\n", "err")
        self.status("")                     # 结果只显示在常驻标签中，不再另发一条状态栏消息
        self.update_progress()
        grasp_bg.mark_seen(self.cur_workdir)
        self.runFinished.emit(ok)

    def hide_mcp_origin(self):
        """关闭开关后移去来源提示，保留已有计算输出及其格式。"""
        self.poll()
        for text in ("[此计算由 MCP agent 启动]", core.EN["[此计算由 MCP agent 启动]"]):
            cursor = self.out.document().find(text)
            while not cursor.isNull():
                cursor.select(QTextCursor.BlockUnderCursor)
                cursor.removeSelectedText()
                cursor.deleteChar()
                cursor = self.out.document().find(text)

    def show_background(self, d):
        """打开目录时：有计算在运行就接上；上次在后台结束的计算 (尚未看过结果) 显示其输出的末尾和结果。"""
        st = grasp_bg.status(d)
        if not st:
            return
        origin = (T("[此计算由 MCP agent 启动]") + "\n"
                  if st.get("started_by") == "mcp" and core.load_config().get("mcp_enabled", False) else "")
        when = T("开始于 {t}", t=st.get("start", "?"))
        if st["status"] == "running" and not st["remote"]:
            sock = grasp_bg.connect(d, timeout=2)
            if sock:
                self.out.clear()
                self.write(origin, "info")
                self.write(T("[接上后台运行的计算，{w}]", w=when) + "\n", "info")
                self.attach(sock, st.get("titles", []))
                return
            st["status"] = "lost"
        if st["status"] == "running":
            self.write(origin, "info")
            self.write("\n" + T("[此目录的计算正在 {h} 上运行，{w}；以下输出读自 {f}]", h=st.get("host", "?"), w=when,
                                 f=grasp_bg.RUN_LOG) + "\n", "info")
            self.remote = token = object()
            self.busy = True
            self.titles = st.get("titles", [])
            threading.Thread(target=self.tail_remote, args=(d, token), daemon=True).start()
            return
        self.last_run = run_summary(st, st.get("titles", []))
        self.update_progress()
        if st.get("seen"):
            self.write(origin, "info")
            return
        self.out.clear()
        self.write(origin, "info")
        self.write(T("[上次在后台运行的计算，{w}，结束于 {e}]", w=when, e=st.get("end", "?")) + "\n", "info")
        self.write(grasp_bg.log_tail(d))
        i, titles = st.get("step", 0), st.get("titles", [])
        title = titles[i] if 0 <= i < len(titles) else "?"
        self.write("\n" + {"ok": T("*** 后台运行完成"),
                           "stopped": T("*** 后台运行已停止 (第 {i} 步 {t})", i=i + 1, t=title),
                           "failed": T("*** 后台运行在第 {i} 步 {t} 出错", i=i + 1, t=title)}.get(
            st["status"], T("*** 后台运行进程意外结束 (第 {i} 步 {t})", i=i + 1, t=title)) + "\n",
                   "info" if st["status"] == "ok" else "err")
        grasp_bg.mark_seen(d)
        self.runFinished.emit(st["status"] == "ok")

    def tail_remote(self, d, token):
        """计算在另一台机器上运行 (连不上运行进程)：每 0.2 秒读取 RUN_LOG 新增的部分，直到状态不再是 running。"""
        path, pos = os.path.join(d, grasp_bg.RUN_LOG), 0
        dec = codecs.getincrementaldecoder("utf-8")(errors="replace")
        while self.remote is token:
            try:
                with open(path, "rb") as f:
                    f.seek(pos)
                    data = f.read()
                pos += len(data)
                if data:
                    self.q.put((dec.decode(data), ""))
            except OSError:
                pass
            st = grasp_bg.status(d) or {}
            if st.get("status") != "running":
                if self.remote is token:
                    self.q.put((st.get("status", "lost"), "done"))
                return
            time.sleep(0.2)

    def run_current(self):
        job = self.current_job()
        if job:
            self.prev.setPlainText(core.describe(job, self.run.mpi_prefix()) + "\n")
            self.start([job])

    def add_to_pipeline(self):
        self.session_cleared = False
        job = self.current_job()
        if job:
            if job["step"] in core.GROUP_STEPS:
                job["group"] = self.group           # 导出共享配置时需要
            self.pipeline.append(job)
            self.refresh_list()
            self.btabs.setCurrentIndex(0)
            self.save_session()

    def refresh_list(self):
        self.plist.clear()
        self.plist.addItems([f"{i:2d}. {j['title']}" for i, j in enumerate(self.pipeline, 1)])

    def selected(self):
        i = self.plist.currentRow()
        return i if 0 <= i < len(self.pipeline) else None

    def show_job(self, *_):
        i = self.selected()
        if i is not None:
            self.prev.setPlainText(core.describe(self.pipeline[i], self.run.mpi_prefix()) + "\n")
            self.show_preview()

    def move(self, d):
        i = self.selected()
        if i is None or not 0 <= i + d < len(self.pipeline):
            return
        p = self.pipeline
        p[i], p[i + d] = p[i + d], p[i]
        self.refresh_list()
        self.plist.setCurrentRow(i + d)
        self.save_session()

    def edit_job(self):
        """把选中步骤的参数载入它的程序页 (切换到该步骤的态组)，改好后用“更新所选”写回。"""
        i = self.selected()
        if i is None:
            return
        job = self.pipeline[i]
        form = next((f for f in self.forms if f.step.key == job.get("step")), None)
        if form is None or not isinstance(job.get("vals"), dict):
            QMessageBox.information(self, T("编辑"), T("这一步没有程序页参数 (导入的命令)，不能载入表单。"))
            return
        if job["step"] in core.GROUP_STEPS and job.get("group") in core.GROUPS:
            self.switch_group(job["group"])
        form.set_values(job["vals"])
        self.nav.setCurrentWidget(form)
        self.status(T("已把第 {i} 步载入表单；修改后点“更新所选”替换这一步", i=i + 1))

    def update_job(self):
        """用当前程序页的参数替换选中的步骤 (须是同一个程序)。"""
        i = self.selected()
        if i is None:
            return
        form = self.nav.currentWidget()
        if self.pipeline[i].get("step") != form.step.key:
            QMessageBox.information(self, T("更新所选"), T("第 {i} 步不是 {p}：先选中这一步并点“编辑”，修改后再更新。",
                                                          i=i + 1, p=T(form.step.name)))
            return
        job = self.current_job()
        if job:
            if job["step"] in core.GROUP_STEPS:
                job["group"] = self.group
            self.pipeline[i] = job
            self.refresh_list()
            self.plist.setCurrentRow(i)
            self.save_session()
            self.status(T("已更新第 {i} 步", i=i + 1))

    def remove_job(self):
        i = self.selected()
        if i is not None:
            del self.pipeline[i]
            self.refresh_list()
            self.save_session()

    def clear_pipeline(self):
        self.pipeline.clear()
        self.refresh_list()
        self.save_session()

    def run_pipeline(self):
        self.start(list(self.pipeline))

    def upload_remote(self, run=False):
        from remote_edit_panel import upload
        upload(self, run)

    def refresh_remote_copy(self):
        from grasp_remote_edit import association
        link = association(self.cur_workdir)
        text = T("服务器副本 {h}:{p}", h=link["host"], p=link["directory"]) if link else ""
        self.copy_label.setText(text)
        self.copy_label.setVisible(bool(text))
        self.copyChanged.emit(text)

    def run_from_selected(self):
        self.start(self.pipeline[self.selected() or 0:])

    def list_files(self):
        if not self.check_dirs():
            return
        names = sorted(os.listdir(self.cur_workdir))
        self.write("\n" + T("----- {d} ({n} 个文件) -----", d=self.cur_workdir, n=len(names)) + "\n", "head")
        self.write("  ".join(names) + "\n")

    def need_pipeline(self):
        if not self.pipeline:
            QMessageBox.information(self, T("导出"), T("流程为空：先在各标签页点击“加入流程”。"))
            return False
        return True

    def save_text(self, title, name, text):
        path, _ = QFileDialog.getSaveFileName(self, title, os.path.join(self.cur_workdir, name))
        if not path:
            return
        with open(path, "w") as f:
            f.write(text)
        os.chmod(path, 0o755)
        self.status(T("已导出 {p}", p=path))

    def export_script(self):
        if self.need_pipeline():
            self.save_text(T("导出 shell 脚本"), "sh_gui",
                           core.shell_script(self.pipeline, self.run.bindir(), self.cur_workdir,
                                             self.run.mpi_prefix(), self.run.mpitmp()))

    def export_slurm(self):
        if not self.need_pipeline():
            return
        dlg = SlurmDialog(self, self.slurm)
        if dlg.exec() != QDialog.Accepted:
            return
        self.slurm = dlg.settings()
        core.save_config(slurm=self.slurm)
        self.save_text(T("Slurm 作业脚本"), f"{self.slurm['job'] or 'grasp'}.slurm",
                       core.slurm_script(self.pipeline, self.slurm))

    # ---- 参数保存 / 载入 ----
    def collect(self):
        self.flush_group()
        return dict(workdir=self.cur_workdir, **self.run.values(),
                    slurm=self.slurm, steps={f.step.key: f.values() for f in self.forms},
                    groups=self.groups, group=self.group,
                    pipeline=self.pipeline, tab=self.nav.currentWidget().step.key)

    def apply(self, data):
        self.session_cleared = False
        self.run.apply(data)
        if "workdir" in data:
            d = os.path.abspath(str(data["workdir"]))
            if d != self.cur_workdir:
                self.cur_workdir = d
                _, self.session_revision = sessions.read(d)
                self.workdirRequested.emit(d)       # 主窗口更新工作目录一栏 (不再载入该目录的记录)
        self.slurm.update(data.get("slurm", {}))
        steps = data.get("steps", {})
        for f in self.forms:
            # 兼容旧版本以中文标签页名保存的"自定义"参数
            d = steps.get(f.step.key) or steps.get(f.step.name)
            if d and f.step.key not in core.GROUP_STEPS:
                f.set_values(d)
        saved = data.get("groups")
        if saved is None:
            # 旧版本的参数没有态组：作为 odd 组的参数，even 组用默认值
            saved = {core.GROUPS[0]: {k: steps[k] for k in core.GROUP_STEPS if k in steps}}
        self.groups = {g: {k: dict(v, **saved.get(g, {}).get(k, {})) for k, v in core.group_defaults(g).items()}
                       for g in core.GROUPS}
        self.group = data.get("group") if data.get("group") in core.GROUPS else core.GROUPS[0]
        self.load_group()
        self.pipeline = [dict(j, pre=[tuple(a) for a in j["pre"]], post=[tuple(a) for a in j["post"]])
                         for j in data.get("pipeline", [])]
        self.refresh_list()
        tab = data.get("tab")
        if isinstance(tab, int):                # 旧版本按序号保存
            if 0 <= tab < len(self.forms):
                self.nav.setCurrentWidget(self.forms[tab])
        elif tab:
            for f in self.forms:
                if f.step.key == tab:
                    self.nav.setCurrentWidget(f)

    def import_demo(self):
        self.import_config(examples=True)

    def import_file(self):
        self.import_config(examples=False)

    def import_config(self, examples, path=None, update=False):
        """导入共享配置：examples 为真时列出随程序提供的手册算例，否则先选择一个配置文件。"""
        if self.busy:
            QMessageBox.information(self, T("运行中"), T("已有任务在运行，请等待或先停止。"))
            return
        entries = [dict(cfg=c, warnings=[], path=None) for c in grasp_config.list_demos()] if examples else []
        if examples and not entries:
            QMessageBox.warning(self, T("导入示例"), T("找不到示例文件 ({p})", p=grasp_config.DEMO_DIR))
            return
        dlg = ConfigDialog(self, entries, self.run.bindir(), self.cur_workdir)
        dlg.update.setChecked(update)
        if not examples and path and not dlg.open_file(path):
            return
        if dlg.exec() != QDialog.Accepted:
            return
        cfg, path = dlg.entry()["cfg"], dlg.target()
        if dlg.update.isChecked():
            self.update_workdir(cfg)
            return
        try:
            grasp_config.install(cfg, path)
        except OSError as e:
            QMessageBox.warning(self, T("导入配置"), str(e))
            return
        self.session_cleared = False
        if path == self.cur_workdir:
            self.open_workdir(path)
        else:
            self.workdirRequested.emit(path)
        self.status(T("已导入“{t}”到 {p}；点“▶ 运行全部”开始计算", t=grasp_config.text(cfg, "title"), p=path))

    def update_workdir(self, cfg):
        if self.check_external() == "cancel":
            return
        try:
            plan = workdir_ops.prepare_update(self.cur_workdir, cfg, self.collect())
            dlg = UpdateDialog(self, plan)
            if dlg.exec() != QDialog.Accepted:
                return
            data = workdir_ops.apply_update(plan, dlg.overwrite())
            _, self.session_revision = sessions.read(self.cur_workdir)
        except (OSError, ValueError, KeyError) as e:
            QMessageBox.warning(self, T("更新当前工作目录"), str(e))
            return
        self.session_cleared = False
        self.apply(data)
        first = 0 if dlg.overwrite() else plan["first"]
        if first is not None:
            self.plist.setCurrentRow(first)
            self.status(T("已更新当前目录；建议从第 {n} 步开始核对和重算", n=first + 1))
        else:
            self.status(T("已更新当前目录；原有结果文件保留"))
        self.filesChanged.emit()
        if plan.get("warnings"):
            QMessageBox.warning(self, T("更新当前工作目录"),
                                T("更新已完成，但临时备份清理失败：{e}", e="\n".join(plan["warnings"])))
        if dlg.run_added:
            self.plist.setCurrentRow(plan["append_from"])
            self.run_from_selected()

    def pick_workdir(self):
        d = QFileDialog.getExistingDirectory(self, "", self.cur_workdir)
        if d:
            self.workdirRequested.emit(d)

    def restore_workdir(self):
        if self.check_external() == "cancel":
            return
        if QMessageBox.question(self, T("恢复上一次的工作目录记录"),
                               T("只恢复流程和参数，计算结果与附带文件不变。当前记录也会备份；连续恢复两次会回到恢复前的状态。确定恢复吗？")) != QMessageBox.Yes:
            return
        try:
            data = workdir_ops.restore(self.cur_workdir)
            _, self.session_revision = sessions.read(self.cur_workdir)
        except (OSError, ValueError) as e:
            QMessageBox.warning(self, T("恢复上一次的工作目录记录"), str(e))
            return
        self.session_cleared = False
        self.apply(data)
        self.status(T("已恢复上一次的工作目录记录"))

    def clear_workdir(self):
        if self.check_external() == "cancel":
            return
        started = False
        try:
            root = workdir_ops.safe_root(self.cur_workdir, allow_untracked=True)
            tracked = os.path.isfile(os.path.join(root, core.SESSION_FILE)) and not os.path.islink(
                os.path.join(root, core.SESSION_FILE))
            if not tracked and QMessageBox.question(self, T("清空工作目录"),
                    T("此目录没有工作目录记录。仍要查看并选择待删除内容吗？")) != QMessageBox.Yes:
                return
            dlg = ClearDialog(self, root, not tracked)
            if dlg.plan is None:
                return
            if dlg.exec() != QDialog.Accepted:
                return
            started = True
            workdir_ops.clear(dlg.plan, dlg.selected_names(), dlg.confirmation.text())
        except (OSError, ValueError) as e:
            QMessageBox.warning(self, T("清空工作目录"),
                                T("清理未完成，部分项目可能已删除：{e}", e=str(e)))
            if not started:
                return
        else:
            self.status(T("已永久删除所选项目"))
        if not os.path.exists(os.path.join(root, core.SESSION_FILE)):
            self.apply(dict(pipeline=[], steps={s.key: s.defaults() for s in core.STEPS}, groups={}))
            self.session_cleared = True
        self.filesChanged.emit()

    def summary_source(self, d):
        """能级图页的结果摘要：本页当前目录的流程和本次会话的运行记录；其他目录返回 None (由调用方读目录记录)。"""
        if os.path.abspath(d) != os.path.abspath(self.cur_workdir):
            return None
        self.flush_group()
        return self.pipeline, self.summary_runs.get(self.cur_workdir, [])

    def export_config(self, clipboard=False):
        """把当前流程导出为共享配置文件。"""
        if not self.need_pipeline():
            return
        self.flush_group()
        dlg = ExportDialog(self, self.cur_workdir, self.pipeline, clipboard)
        if dlg.exec() != QDialog.Accepted:
            return
        cfg = dlg.cfg
        if dlg.to_clipboard:
            self.status(T("已复制配置到剪贴板 ({n} 步)", n=len(cfg["jobs"])))
            return
        name = re.sub(r"[^\w.-]+", "_", cfg["title"]).strip("_") or "config"
        path, _ = QFileDialog.getSaveFileName(self, T("导出配置"), os.path.join(self.cur_workdir, name + ".json"),
                                              T("配置文件 (*.json)"))
        if not path:
            return
        if not path.endswith(".json"):
            path += ".json"
        try:
            grasp_config.save(cfg, path)
        except OSError as e:
            QMessageBox.warning(self, T("导出配置"), str(e))
            return
        self.status(T("已导出配置 {p} ({n} 步)", p=path, n=len(cfg["jobs"])))

    def save_params(self):
        path, _ = QFileDialog.getSaveFileName(self, T("保存参数"), self.cur_workdir, "JSON (*.json)")
        if not path:
            return
        if not path.endswith(".json"):
            path += ".json"
        with open(path, "w") as f:
            json.dump(self.collect(), f, ensure_ascii=False, indent=1)
        self.status(T("参数已保存到 {p}", p=path))

    def load_params(self):
        path, _ = QFileDialog.getOpenFileName(self, T("载入参数"), self.cur_workdir, "JSON (*.json)")
        if not path:
            return
        with open(path) as f:
            self.apply(json.load(f))
        self.status(T("已载入 {p}", p=path))
