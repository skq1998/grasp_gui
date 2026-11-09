"""文件传输清单、大文件选择和后台 rsync 进度。"""

from pathlib import Path
import queue
import threading

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout,
                               QLabel, QLineEdit, QMessageBox, QProgressBar, QSpinBox, QTableWidget,
                               QTableWidgetItem, QVBoxLayout)

import grasp_core as core
import grasp_transfer as transfer
from grasp_core import T
from gui_common import button


class TransferDialog(QDialog):
    transferred = Signal(object)
    transferFailed = Signal(str)
    def __init__(self, source, directory, local=None, names=None, parent=None, *, upload=None):
        super().__init__(parent)
        upload = local is not None if upload is None else upload
        self.setWindowTitle(T("从本机上传…") if upload else T("下载到本机…"))
        self.resize(860, 650)
        self.source, self.directory, self.local, self.names = source, directory, local, names
        self.upload, self.rows, self.busy, self.closed = upload, [], False, False
        self.job, self.events, self.generation = None, queue.Queue(), 0
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(T("服务器 {h}:{p}", h=source.client.host, p=directory)))
        form = QFormLayout()
        profile = core.load_config().get("remote_hosts", {}).get(source.client.host, {})
        self.rsync = QLineEdit(profile.get("rsync", "rsync"))
        form.addRow(T("服务器 rsync"), self.rsync)
        self.target = QLineEdit(directory if self.upload else
                                transfer.download_directory(source.client.host, directory))
        target_bar = QHBoxLayout()
        target_bar.addWidget(self.target)
        self.browse_button = button("浏览…", self.choose_target)
        self.browse_button.setVisible(not self.upload)
        target_bar.addWidget(self.browse_button)
        self.target.setReadOnly(self.upload)
        form.addRow(T("目标目录"), target_bar)
        self.threshold = QSpinBox()
        self.threshold.setRange(1, 1_000_000)
        self.threshold.setSuffix(" MB")
        self.threshold.setToolTip(T("每个文件的大小阈值；1 MB = 1,000,000 字节"))
        self.threshold.setValue(profile.get("transfer_threshold_mb", 100))
        form.addRow(T("大文件阈值"), self.threshold)
        self.policy = QComboBox()
        for text, value in (("跳过已有文件", "skip"), ("续传 / 校验", "resume"), ("覆盖已有文件", "overwrite")):
            self.policy.addItem(T(text), value)
        form.addRow(T("同名文件"), self.policy)
        layout.addLayout(form)
        self.large = QCheckBox(T("包括大文件"))
        self.large.setTristate(True)
        self.large.stateChanged.connect(self.include_large)
        layout.addWidget(self.large)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels([T("文件"), T("大小"), T("大文件")])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 520)
        self.table.itemChanged.connect(self.changed)
        layout.addWidget(self.table)
        self.note = QLabel(T(".w、.c、.cm、角系数等大文件通常不需要下载；能级、组成和波函数曲线可直接在远程窗口查看。"))
        self.note.setWordWrap(True)
        self.note.hide()
        layout.addWidget(self.note)
        self.counts = QLabel()
        self.counts.setWordWrap(True)
        layout.addWidget(self.counts)
        self.status = QLabel(T("正在读取文件清单…"))
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.progress = QProgressBar()
        layout.addWidget(self.progress)
        bar = QHBoxLayout()
        self.refresh_button = button("刷新清单", self.load)
        bar.addWidget(self.refresh_button)
        self.start_button = button("确认传输…", self.start_transfer)
        self.start_button.setEnabled(False)
        bar.addWidget(self.start_button)
        self.cancel_button = button("取消传输", self.cancel_transfer)
        self.cancel_button.setEnabled(False)
        bar.addWidget(self.cancel_button)
        bar.addWidget(button("关闭", self.reject))
        layout.addLayout(bar)
        self.threshold.valueChanged.connect(self.reset_checks)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(50)
        self.load()

    def choose_target(self):
        path = QFileDialog.getExistingDirectory(self, T("下载到本机…"), self.target.text())
        if path:
            self.target.setText(path)

    def load(self):
        if self.busy:
            return
        self.start_button.setEnabled(False)
        self.rows = []
        self.generation += 1
        generation = self.generation
        args = dict(directory=self.directory, names=[] if self.upload else None, rsync=self.rsync.text().strip())
        def loaded(value, error):
            if self.closed or generation != self.generation:
                return
            if error:
                self.status.setText(str(error))
            elif self.upload:
                def scan():
                    try:
                        self.events.put(("files", (generation, transfer.scan(self.local, self.names))))
                    except (OSError, ValueError) as exc:
                        self.events.put(("load_error", (generation, exc)))
                threading.Thread(target=scan, daemon=True).start()
            else:
                self.show_files(value)
        self.source.submit("transfer_files", args, loaded)

    def show_files(self, data):
        self.rows = data["files"]
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.rows))
        for i, row in enumerate(self.rows):
            item = QTableWidgetItem(row["name"])
            item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
            self.table.setItem(i, 0, item)
            for col, text in ((1, f"{row['size'] / 1_000_000:.2f} MB"), (2, "")):
                cell = QTableWidgetItem(text)
                cell.setFlags(Qt.ItemIsEnabled)
                self.table.setItem(i, col, cell)
        self.table.blockSignals(False)
        self.reset_checks()
        self.status.setText(T("已跳过符号链接或特殊文件：{n}", n=len(data["skipped"])))

    def reset_checks(self):
        self.table.blockSignals(True)
        threshold = self.threshold.value() * 1_000_000
        for i, row in enumerate(self.rows):
            large = row["size"] > threshold
            self.table.item(i, 0).setCheckState(Qt.Unchecked if large else Qt.Checked)
            self.table.item(i, 2).setText(T("大文件") if large else "")
        self.table.blockSignals(False)
        self.changed()

    def selected(self):
        return [row for i, row in enumerate(self.rows) if self.table.item(i, 0).checkState() == Qt.Checked]

    def include_large(self, *_args):
        state = self.large.checkState()
        if state == Qt.PartiallyChecked:
            state = Qt.Checked
        self.table.blockSignals(True)
        for i, row in enumerate(self.rows):
            if row["size"] > self.threshold.value() * 1_000_000:
                self.table.item(i, 0).setCheckState(Qt.Checked if state == Qt.Checked else Qt.Unchecked)
        self.table.blockSignals(False)
        self.changed()

    def changed(self, *_args):
        selected = {row["name"] for row in self.selected()}
        self.total = transfer.totals(self.rows, selected, self.threshold.value() * 1_000_000)
        self.counts.setText(T("小文件：{n} 个，{b} MB；选中大文件：{l} 个，{s} MB；未选大文件：{u} 个，{t} MB",
            n=self.total["small"]["count"], b=round(self.total["small"]["size"] / 1_000_000, 2),
            l=self.total["large"]["count"], s=round(self.total["large"]["size"] / 1_000_000, 2),
            u=self.total["skipped_large"]["count"], t=round(self.total["skipped_large"]["size"] / 1_000_000, 2)))
        self.note.setVisible(self.total["large"]["count"] > 0)
        state = Qt.Unchecked if not self.total["large"]["count"] else (
            Qt.PartiallyChecked if self.total["skipped_large"]["count"] else Qt.Checked)
        self.large.blockSignals(True)
        self.large.setCheckState(state)
        self.large.blockSignals(False)
        self.start_button.setEnabled(bool(selected) and not self.busy)

    def controls(self, busy):
        self.busy = busy
        for widget in (self.refresh_button, self.start_button, self.threshold, self.large, self.table,
                       self.rsync, self.target, self.browse_button, self.policy):
            widget.setEnabled(not busy)
        self.cancel_button.setEnabled(busy)
        if not busy:
            self.changed()

    def start_transfer(self):
        rows = self.selected()
        if not rows or self.busy:
            return
        if not self.target.text().strip():
            self.status.setText(T("请选择目标目录"))
            return
        text = self.counts.text() + "\n" + self.policy.currentText() + "\n" + self.target.text()
        if QMessageBox.question(self, self.windowTitle(), text, QMessageBox.Yes | QMessageBox.No,
                                QMessageBox.No) != QMessageBox.Yes:
            return
        self.begin_transfer(rows)

    def begin_transfer(self, rows):
        """调用者已确认清单；流程上传在写入记录成功后复用同一传输。"""
        profiles = core.load_config().get("remote_hosts", {})
        profiles.setdefault(self.source.client.host, {}).update(
            rsync=self.rsync.text().strip(), transfer_threshold_mb=self.threshold.value())
        core.save_config(remote_hosts=profiles)
        self.controls(True)
        self.job = transfer.Transfer(self.source.client)
        local = self.local if self.upload else self.target.text()
        policy, rsync = self.policy.currentData(), self.rsync.text().strip()
        def work():
            try:
                value = self.job.run(local, self.directory, rows, self.upload, policy, rsync,
                                     lambda state: self.events.put(("progress", state)))
                self.events.put(("done", value))
            except Exception as error:
                self.events.put(("error", error))
        threading.Thread(target=work, daemon=True).start()

    def cancel_transfer(self):
        if self.job is not None:
            self.job.cancel()

    def poll(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind in ("files", "load_error"):
                    generation, data = value
                    if generation == self.generation and not self.closed:
                        if kind == "files":
                            self.show_files(data)
                        else:
                            self.status.setText(str(data))
                    continue
                if kind == "progress":
                    self.progress.setValue(value["percent"])
                    self.status.setText(T("传输 {n}% · {s} · 剩余 {t}", n=value["percent"],
                                          s=value["speed"], t=value["eta"]))
                else:
                    self.controls(False)
                    self.status.setText((str(value) or T("传输失败，请查看 SSH 连接状态")) if kind == "error" else
                                        T("已按所选规则处理 {n} 个文件", n=value["count"]))
                    if kind == "done":
                        self.progress.setValue(100)
                        self.transferred.emit(value)
                    else:
                        self.transferFailed.emit(str(value))
                if self.parent() and hasattr(self.parent(), "statusBar"):
                    self.parent().statusBar().showMessage(self.status.text())
        except queue.Empty:
            pass

    def done(self, result):
        if self.busy and not self.closed:
            if QMessageBox.question(self, T("取消传输"),
                                    T("传输仍在进行，关闭窗口将取消传输。是否取消并关闭？"),
                                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                return
        self.closed = True
        self.cancel_transfer()
        self.timer.stop()
        super().done(result)

    def shutdown(self):
        # 远程窗口关闭时连接也会结束，直接取消其传输，不再逐个确认。
        self.closed = True
        self.done(QDialog.Rejected)
