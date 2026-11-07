"""结果摘要的后台读取、勾选与复制预览。"""

import copy
import queue

from PySide6.QtCore import QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QCheckBox, QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton,
                               QSpinBox, QVBoxLayout)

import grasp_index as index
import grasp_summary as summary
from grasp_core import T
from gui_common import fixed_font


class SummaryDialog(QDialog):
    def __init__(self, parent, workdir, cfg, jobs, history=()):
        super().__init__(parent)
        self.setWindowTitle(T("复制配置和结果摘要（给 agent）"))
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
            check = QCheckBox(T(label))
            check.setChecked(True)
            check.toggled.connect(self.render)
            self.sections[key] = check
            bar.addWidget(check)
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
        self.copy_button.setEnabled(False)
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
            self.copy_button.setEnabled(False)
            return
        self.content = content
        self.preview.setPlainText(content)
        self.status.setText(T("摘要大小：{n} KB", n=round(len(content.encode("utf-8")) / 1024, 1)))
        self.copy_button.setEnabled(True)

    def copy(self):
        if self.content:
            QGuiApplication.clipboard().setText(self.content)
            self.accept()

    def done(self, result):
        self.timer.stop()
        self.worker.close()
        super().done(result)
