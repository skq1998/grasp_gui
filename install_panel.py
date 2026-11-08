"""服务器版安装、更新和卸载的显式确认；后台结果由主线程显示。"""

import queue
import threading

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                               QPlainTextEdit, QProgressBar, QVBoxLayout)

import grasp_install as install
from grasp_core import T
from gui_common import button


class InstallDialog(QDialog):
    def __init__(self, client, parent=None, remove=False):
        super().__init__(parent)
        self.setWindowTitle(T("从服务器移除") if remove else T("安装 / 更新服务器版"))
        self.resize(720, 440)
        self.client, self.remove, self.installer = client, remove, install.Installer(client)
        self.events, self.busy, self.package, self.checked = queue.Queue(), False, None, None
        self.installed_path = None
        layout = QVBoxLayout(self)
        self.note = QLabel(T("安装与分析会话依次进行；不使用 sudo，不修改 PATH 或 shell 配置。"))
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        form = QFormLayout()
        form.addRow(T("SSH 主机（沿用 ~/.ssh/config）"), QLabel(client.host))
        self.root = QLineEdit(install.DEFAULT_ROOT)
        self.root.textChanged.connect(self.invalidate)
        form.addRow(T("安装目录"), self.root)
        layout.addLayout(form)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        layout.addWidget(self.details)
        self.progress = QProgressBar()
        layout.addWidget(self.progress)
        bar = QHBoxLayout()
        self.check_button = button("检查服务器", self.check)
        bar.addWidget(self.check_button)
        self.action = button("确认移除" if remove else "确认安装", self.apply)
        self.action.setEnabled(False)
        bar.addWidget(self.action)
        bar.addWidget(button("取消", self.reject))
        layout.addLayout(bar)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(50)
        if not remove:
            try:
                self.package = install.bundle()
                self.details.setPlainText(T("捆绑版本：{v}；大小：{n} MiB", v=self.package["version"],
                                            n=round(self.package["size"] / (1024 * 1024), 1)))
            except (OSError, ValueError) as error:
                self.details.setPlainText(str(error))
                self.check_button.setEnabled(False)

    def invalidate(self):
        self.checked = None
        self.action.setEnabled(False)

    def start(self, function, done):
        if self.busy:
            return
        self.busy = True
        self.check_button.setEnabled(False)
        self.action.setEnabled(False)
        self.root.setEnabled(False)
        def work():
            try:
                value, error = function(), None
            except Exception as exc:
                value, error = None, exc
            self.events.put((done, value, error))
        threading.Thread(target=work, daemon=True).start()

    def check(self):
        root = self.root.text().strip()
        def probe():
            self.client.close()
            return self.installer.probe(self.package, root)
        self.start(probe, self.checked_server)

    def checked_server(self, value):
        self.checked = value
        versions = "\n".join(value["versions"]) or T("没有本程序安装的版本")
        self.details.appendPlainText(T("架构：{a}；{g}；可用空间：{n} MiB", a=value["architecture"],
                                      g=value["glibc"], n=round(value["free"] / (1024 * 1024))))
        self.details.appendPlainText(T("安装目录：{p}\n链接：{l}\n已安装版本：\n{v}", p=value["root"],
                                      l=value["link"], v=versions))
        self.action.setEnabled(True)

    def apply(self):
        if self.checked is None:
            return
        text = T("移除下面列出的版本目录及本程序创建的链接；不删除计算目录或结果。") if self.remove else T(
            "将传输捆绑包、校验并切换链接；保留上一版和仍在运行的旧版。")
        if not self.remove:
            text += "\n" + T("捆绑版本：{v}；大小：{n} MiB", v=self.package["version"],
                              n=round(self.package["size"] / (1024 * 1024), 1))
        text += "\n" + self.client.host + "\n" + self.checked["root"] + "\n" + self.checked["link"]
        if self.remove:
            text += "\n" + "\n".join(self.checked["versions"])
        if QMessageBox.question(self, self.windowTitle(), text, QMessageBox.Yes | QMessageBox.No,
                                QMessageBox.No) != QMessageBox.Yes:
            return
        root = self.root.text().strip()
        self.start((lambda: self.installer.remove(root)) if self.remove else
                   (lambda: self.installer.install(self.package, root, self.report_progress)), self.finished_install)

    def report_progress(self, sent, total):
        self.events.put((None, (sent, total), None))

    def finished_install(self, value):
        self.installed_path = None if self.remove else value
        self.accept()

    def poll(self):
        try:
            while True:
                done, value, error = self.events.get_nowait()
                if done is None:
                    self.progress.setValue(round(value[0] * 100 / max(1, value[1])))
                    continue
                self.busy = False
                self.check_button.setEnabled(self.remove or self.package is not None)
                self.root.setEnabled(True)
                if error:
                    self.details.appendPlainText(str(error))
                else:
                    done(value)
        except queue.Empty:
            pass

    def done(self, result):
        self.timer.stop()
        if self.busy:
            self.installer.cancel()
        super().done(result)
