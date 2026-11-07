"""结果摘要的后台读取、勾选、复制与保存 (能级图页)。"""

import copy
import os
import queue

from PySide6.QtCore import QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QCheckBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit,
                               QPushButton, QSpinBox, QVBoxLayout)

import grasp_index as index
import grasp_summary as summary
from grasp_core import T
from gui_common import fixed_font


class SummaryDialog(QDialog):
    def __init__(self, parent, workdir, cfg, jobs, history=()):
        """cfg 为 None 时 (目录没有流程记录) 不能附带配置。"""
        super().__init__(parent)
        self.setWindowTitle(T("结果摘要（给 agent）"))
        self.resize(960, 720)
        self.workdir, self.cfg, self.jobs, self.history = workdir, cfg, copy.deepcopy(jobs), copy.deepcopy(history)
        self.worker = index.ReadWorker()
        self.data, self.content, self.token = None, "", 0
        layout = QVBoxLayout(self)
        note = QLabel(T("先预览并选择包含的部分。默认每个对称块前 5 个能级，文本最多 200 KB；结果读取在后台进行。"))
        note.setWordWrap(True)
        layout.addWidget(note)
        bar = QHBoxLayout()
        self.sections = {}
        for key, label in summary.SECTIONS.items():
            check = QCheckBox(T("附带完整配置") if key in summary.OPTIONAL else T(label))
            check.setChecked(key not in summary.OPTIONAL)
            check.setEnabled(key != "configuration" or cfg is not None)
            check.toggled.connect(self.render)
            self.sections[key] = check
            if key in summary.OPTIONAL:
                bar.addSpacing(16)
            bar.addWidget(check)
        bar.addStretch()
        layout.addLayout(bar)
        bar = QHBoxLayout()
        bar.addWidget(QLabel(T("每个对称块的能级数")))
        self.limit = QSpinBox()
        self.limit.setRange(1, 200)
        self.limit.setValue(5)
        self.limit.valueChanged.connect(self.load)
        bar.addWidget(self.limit)
        self.status = QLabel()
        bar.addWidget(self.status, 1)
        layout.addLayout(bar)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setFont(fixed_font())
        layout.addWidget(self.preview, 1)
        bar = QHBoxLayout()
        bar.addStretch()
        self.copy_button = QPushButton(T("复制到剪贴板"))
        self.copy_button.setEnabled(False)
        self.copy_button.clicked.connect(self.copy)
        bar.addWidget(self.copy_button)
        self.save_button = QPushButton(T("保存为文件…"))
        self.save_button.setEnabled(False)
        self.save_button.clicked.connect(self.save)
        bar.addWidget(self.save_button)
        cancel = QPushButton(T("取消"))
        cancel.clicked.connect(self.reject)
        bar.addWidget(cancel)
        layout.addLayout(bar)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(50)
        self.load()

    def load(self):
        self.token += 1
        self.data = None
        self.set_ready(False)
        self.status.setText(T("正在读取结果…"))
        self.worker.submit(self.token, summary.collect, self.workdir, self.cfg, self.jobs,
                           self.history, self.limit.value())

    def poll(self):
        try:
            while True:
                kind, token, value = self.worker.events.get_nowait()
                if token != self.token:
                    continue
                if kind == "result":
                    self.data = value
                    self.render()
                elif kind == "error":
                    self.preview.setPlainText(str(value))
                    self.status.setText(T("无法生成摘要"))
                elif kind == "progress":
                    self.status.setText(T("正在读取结果…"))
        except queue.Empty:
            pass

    def render(self):
        if self.data is None:
            return
        try:
            content = summary.render(self.data, [k for k, check in self.sections.items() if check.isChecked()])
        except ValueError as e:
            self.content = ""
            self.preview.setPlainText(str(e))
            self.set_ready(False)
            return
        self.content = content
        self.preview.setPlainText(content)
        self.status.setText(T("摘要大小：{n} KB", n=round(len(content.encode("utf-8")) / 1024, 1)))
        self.set_ready(True)

    def set_ready(self, ready):
        self.copy_button.setEnabled(ready)
        self.save_button.setEnabled(ready)

    def copy(self):
        if self.content:
            QGuiApplication.clipboard().setText(self.content)
            self.accept()

    def save(self):
        if not self.content:
            return
        name = (os.path.basename(os.path.normpath(self.workdir)) or "results") + "_summary.md"
        path, _ = QFileDialog.getSaveFileName(self, T("保存结果摘要"), os.path.join(self.workdir, name),
                                              T("Markdown 文件 (*.md)"))
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.content)
        except OSError as e:
            QMessageBox.warning(self, T("保存结果摘要"), str(e))
            return
        self.saved_path = path
        self.accept()

    def done(self, result):
        self.timer.stop()
        self.worker.close()
        super().done(result)
