"""SSH 连接/目录浏览、只读远程流程，以及现有查看器的数据源适配。"""

import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import queue

from PySide6.QtCore import Qt, QTimer, Signal, QObject
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox, QPlainTextEdit,
                               QPushButton, QSplitter, QStatusBar, QTabWidget, QToolButton, QVBoxLayout, QWidget)

import grasp_config as config
import grasp_install as install
import grasp_core as core
import grasp_mix as mix
import grasp_ssh as ssh
import grasp_summary as summary
import grasp_trans as transitions
import grasp_wfn as wfn
from grasp_core import T
from gui_common import RunSettings, button, renew_layout
from level_panel import LevelPanel
from wfn_panel import WfnPanel


class Source(QObject):
    notice = Signal(str)
    push = Signal(object)
    connectionChanged = Signal()

    def __init__(self, client, parent=None):
        super().__init__(parent)
        self.client, self.waiting = client, {}
        self.rtt = 0.0
        self.server = {}
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(40)

    def submit(self, method, args, callback, progress=None, cached=False):
        future = self.client.request(method, args, cached=cached)
        self.waiting[future.request_id] = (future, callback, progress)
        return future.request_id

    def cancel(self, ident):
        pending = self.waiting.pop(ident, None)
        if pending is not None:
            pending[0].cancel()

    def poll(self):
        try:
            while True:
                event = self.client.events.get_nowait()
                kind = event.get("event")
                if kind == "progress":
                    pending = self.waiting.get(event.get("id"))
                    if pending and pending[2]:
                        pending[2](event["data"])
                elif kind in ("log", "state"):
                    self.push.emit(event)
                elif kind in ("error", "notice", "disconnected"):
                    self.notice.emit(event.get("text", ""))
                    if kind == "disconnected":
                        self.connectionChanged.emit()
                elif kind in ("connected", "latency"):
                    if kind == "connected":
                        self.server = event
                        self.connectionChanged.emit()
                    self.rtt = event.get("rtt", 0)
                    self.notice.emit(T("服务器 {h} 延迟 {n} ms", h=self.client.host, n=round(self.rtt * 1000)))
        except queue.Empty:
            pass
        for ident, (future, callback, _) in list(self.waiting.items()):
            if not future.done():
                continue
            if self.waiting.pop(ident, None) is None:
                continue
            try:
                value = future.result()
            except Exception as e:
                self.notice.emit(str(e))
                callback(None, e)
            else:
                callback(value, None)

    def close(self):
        self.timer.stop()
        self.client.close(wait=False)
        self.waiting.clear()

    def reconnect(self):
        old = self.client
        if not old.closed.is_set():
            return
        self.client = ssh.Client(old.host, old.program, old.ssh, old.options, old.cache, old.command_override)
        self.client.counter = old.counter
        self.connectionChanged.emit()
        if old.watched:
            self.submit("watch", dict(directory=old.watched), lambda value, error: None)

    def fork(self):
        old = self.client
        source = Source(ssh.Client(old.host, old.program, old.ssh, old.options, old.cache, old.command_override))
        source.server = self.server
        source.server_type = getattr(self, "server_type", "workstation")
        source.workspace_identity = getattr(self, "workspace_identity", None)
        return source


def remote_recent_entries():
    """旧条目只有别名和目录，新条目同时保存已解析的连接标识。"""
    entries = core.load_config().get("remote_recent")
    if not isinstance(entries, list):
        return []
    result = []
    for item in entries:
        if not isinstance(item, list) or len(item) not in (2, 3):
            continue
        host, directory = item[:2]
        identity = item[2] if len(item) == 3 else None
        if isinstance(host, str) and isinstance(directory, str) and (identity is None or isinstance(identity, str)):
            result.append((host, directory, identity))
    return result


class ConnectDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(T("连接服务器"))
        self.resize(780, 620)
        self.source, self.directory, self.entries = None, None, []
        self.run_after_load = False
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.host = QComboBox()
        self.host.setEditable(True)
        profiles = core.load_config().get("remote_hosts", {})
        self.host.addItems(list(dict.fromkeys([*profiles, *ssh.hosts()])))
        self.program = QLineEdit()
        self.program.setPlaceholderText(T("自动尝试 ~/.local/bin/grasp_gui 和 PATH；也可填完整路径"))
        self.path = QLineEdit("~")
        self.recent = QComboBox()
        self.recent.addItem(T("最近的服务器目录"), None)
        for host, directory, _ in remote_recent_entries():
            self.recent.addItem(f"{host}:{directory}", (host, directory))
        self.recent.activated.connect(self.select_recent)
        self.server_type = QComboBox()
        self.server_type.addItem(T("请选择服务器类型"), "")
        self.server_type.addItem(T("工作站"), "workstation")
        self.server_type.addItem(T("Slurm 集群"), "slurm")
        form.addRow(T("SSH 主机（沿用 ~/.ssh/config）"), self.host)
        form.addRow(T("服务器类型"), self.server_type)
        hint = QLabel(T("请先选择服务器类型；集群登录节点不宜直接运行计算。"))
        hint.setWordWrap(True)
        form.addRow(hint)
        form.addRow(T("服务器端程序"), self.program)
        form.addRow(T("服务器目录"), self.path)
        form.addRow(self.recent)
        layout.addLayout(form)
        bar = QHBoxLayout()
        self.connect_button = button("连接 / 刷新", self.connect_server)
        bar.addWidget(self.connect_button)
        bar.addWidget(button("上一级", self.parent_directory))
        self.package, self.bundle_error = None, ""
        try:
            self.package = install.bundle()
        except (OSError, ValueError) as error:
            self.bundle_error = str(error)
        self.install_button = button("安装 / 更新服务器版…", self.install_server)
        self.install_button.setEnabled(self.package is not None)
        self.install_button.setToolTip(self.bundle_error)
        bar.addWidget(self.install_button)
        bar.addWidget(button("从服务器移除…", lambda: self.install_server(remove=True)))
        layout.addLayout(bar)
        self.files = QListWidget()
        self.files.itemDoubleClicked.connect(self.enter)
        layout.addWidget(self.files, 1)
        self.status = QLabel(T("密钥或 ssh-agent 优先；密码和验证码仅在 OpenSSH 输入框中输入，不保存。"))
        self.status.setWordWrap(True)
        if self.bundle_error:
            self.status.setText(self.status.text() + "\n" + self.bundle_error)
        layout.addWidget(self.status)
        bar = QHBoxLayout()
        self.create_button = button("在此新建计算…", self.create_calculation)
        self.create_button.setEnabled(False)
        bar.addWidget(self.create_button)
        bar.addStretch()
        self.use = button("使用此目录", self.accept)
        self.use.setEnabled(False)
        bar.addWidget(self.use)
        bar.addWidget(button("取消", self.reject))
        layout.addLayout(bar)
        self.host.currentTextChanged.connect(self.profile)
        self.profile(self.host.currentText())

    def select_recent(self, index):
        item = self.recent.itemData(index)
        if item:
            host, path = item
            self.host.setCurrentText(host)
            self.path.setText(path)
            self.connect_server()

    def install_server(self, remove=False):
        if not remove and self.package is None:
            self.status.setText(self.bundle_error)
            return
        from install_panel import InstallDialog
        if self.source and self.source.client.host != self.host.currentText().strip():
            self.source.close()
            self.source = None
        if self.source is None:
            try:
                self.source = Source(ssh.Client(self.host.currentText().strip(), self.program.text().strip()), self)
                self.source.notice.connect(self.status.setText)
            except (OSError, ValueError) as error:
                self.status.setText(str(error))
                return
        source = self.source
        source.close()
        self.source, self.directory = None, None
        self.use.setEnabled(False)
        self.create_button.setEnabled(False)
        dialog = InstallDialog(source.client, self, remove)
        saved = core.load_config().get("remote_hosts", {}).get(source.client.host, {})
        dialog.root.setText(saved.get("install_root", dialog.root.text()))
        if dialog.exec() == QDialog.Accepted:
            self.program.setText(dialog.installed_path or "")
            profiles = core.load_config().get("remote_hosts", {})
            profile = profiles.setdefault(source.client.host, {})
            profile["program"] = dialog.installed_path or ""
            profile["install_root"] = dialog.root.text().strip()
            core.save_config(remote_hosts=profiles)
            if dialog.installed_path:
                self.connect_server()

    def profile(self, host):
        saved = core.load_config().get("remote_hosts", {}).get(host, {})
        self.server_type.setCurrentIndex(self.server_type.findData(saved.get("server_type", "")))
        self.program.setText(saved.get("program", ""))
        self.path.setText(saved.get("directory", "~"))

    def connect_server(self):
        host, program = self.host.currentText().strip(), self.program.text().strip()
        if self.source is not None and (self.source.client.host != host or self.source.client.program != program or
                                        self.source.client.closed.is_set()):
            self.source.close()
            self.source = None
        if self.source is None:
            try:
                self.source = Source(ssh.Client(host, program), self)
            except (OSError, ValueError) as e:
                self.status.setText(str(e))
                return
            self.source.notice.connect(self.status.setText)
        self.use.setEnabled(False)
        self.create_button.setEnabled(False)
        self.connect_button.setEnabled(False)
        self.status.setText(T("正在从 {h} 读取…", h=host))
        self.source.submit("list_dir", dict(directory=self.path.text()), self.loaded)

    def loaded(self, value, error):
        self.connect_button.setEnabled(True)
        if error:
            self.status.setText(str(error))
            if not isinstance(error, ssh.ServerUnavailable) or self.package is None:
                return
            self.status.setText(str(error) + "\n" + T("可安装捆绑服务器版，或在此设置已有程序的完整路径。"))
            box = QMessageBox(self)
            box.setWindowTitle(T("服务器端不可用"))
            box.setText(str(error))
            upload = box.addButton(T("安装 / 更新服务器版…"), QMessageBox.ActionRole)
            existing = box.addButton(T("使用已有的路径"), QMessageBox.ActionRole)
            cancel = box.addButton(T("取消"), QMessageBox.RejectRole)
            box.setDefaultButton(cancel)
            box.exec()
            if box.clickedButton() == upload:
                self.install_server()
            elif box.clickedButton() == existing:
                self.program.setFocus()
            return
        self.directory = value["directory"]
        self.parent_path = value["parent"]
        self.entries = value["files"]
        self.path.setText(self.directory)
        self.files.clear()
        for row in self.entries:
            item = QListWidgetItem(("▸ " if row["directory"] else "  ") + row["name"])
            item.setData(Qt.UserRole, row)
            item.setToolTip(f"{row['type']} · {row['size']} bytes")
            self.files.addItem(item)
        self.use.setEnabled(True)
        self.create_button.setEnabled(True)
        version = self.source.server.get("version", core.VERSION)
        self.status.setText(T("服务器 {h}:{p}", h=self.source.client.host, p=self.directory))
        if (self.package is not None and install.older_version(version, core.VERSION)
                and getattr(self, "offered_version", None) != version):
            self.offered_version = version
            if QMessageBox.question(self, T("服务器版本较旧"),
                    T("服务器 {v}，桌面 {d}；协议兼容，可继续使用。现在安装捆绑版本吗？", v=version, d=core.VERSION),
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
                self.install_server()

    def enter(self, item):
        row = item.data(Qt.UserRole)
        if row["directory"]:
            self.path.setText(str(PurePosixPath(self.directory) / row["name"]))
            self.connect_server()

    def parent_directory(self):
        if self.directory:
            self.path.setText(self.parent_path)
            self.connect_server()

    def valid_connection(self):
        if not self.source or not self.directory:
            return False
        if self.server_type.currentData() not in ("workstation", "slurm"):
            self.status.setText(T("请选择服务器类型"))
            return False
        if self.source.client.host != self.host.currentText().strip():
            self.status.setText(T("主机设置已更改，请重新连接"))
            return False
        if self.server_type.currentData() == "slurm" and "slurm" not in self.source.server.get("server_types", []):
            self.status.setText(T("服务器端尚不支持 Slurm，请先更新服务器端程序。"))
            return False
        return True

    def create_calculation(self):
        if not self.valid_connection():
            return
        from remote_import_panel import create
        self.source.server_type = self.server_type.currentData()
        def opened(directory, run):
            self.directory, self.run_after_load = directory, run
            self.accept()
        create(self, self.source, self.directory, opened)

    def accept(self):
        if not self.valid_connection():
            return
        profiles = core.load_config().get("remote_hosts", {})
        profiles.setdefault(self.source.client.host, {}).update(
            program=self.source.server.get("executable", "") or self.program.text().strip(), directory=self.directory,
            server_type=self.server_type.currentData())
        self.source.server_type = self.server_type.currentData()
        core.save_config(remote_hosts=profiles)
        super().accept()

    def reject(self):
        if getattr(self, "import_dialog", None) is not None:
            self.import_dialog.shutdown()
        if self.source:
            self.source.close()
        super().reject()


class SparseCoefficients:
    def __init__(self, count, parts):
        self.count, self.values = count, {p["csf"] - 1: p["coefficient"] for p in parts}

    def __len__(self):
        return self.count

    def __getitem__(self, i):
        if not 0 <= i < self.count:
            raise IndexError(i)
        return self.values.get(i, 0)


class RemoteLevels(LevelPanel):
    LOCAL_TRANSITIONS = False

    def __init__(self, source, directory):
        self.remote_source, self.remote_directory = source, directory
        self.remote_generation, self.remote_closed, self.remote_rows = 0, False, []
        super().__init__(directory, nist_comparison=False)

    def build_view(self, layout, workdir, nist_comparison=False):
        super().build_view(layout, workdir, nist_comparison)
        self.tr_run.hide()
        self.tr_all.hide()
        self.tr_np.hide()
        self.tr_stop.hide()
        self.more = button("读取更多组成", self.more_composition)
        layout.addWidget(self.more)

    def rebuild(self):
        files = [self.files.item(i).clone() for i in range(self.files.count())]
        tab, unit, zero = self.comp_tabs.currentIndex(), self.unit.currentIndex(), self.zero.currentIndex()
        weight, links = self.min_w.value(), self.show_links.isChecked()
        self.build_view(renew_layout(self), self.remote_directory)
        for control, value in ((self.unit, unit), (self.zero, zero), (self.comp_tabs, tab)):
            control.blockSignals(True)
            control.setCurrentIndex(value)
            control.blockSignals(False)
        self.show_links.blockSignals(True)
        self.show_links.setChecked(links)
        self.show_links.blockSignals(False)
        self.min_w.blockSignals(True)
        self.min_w.setValue(weight)
        self.min_w.blockSignals(False)
        for item in files:
            self.files.addItem(item)
        self.render_levels()

    def open_summary(self):
        from summary_panel import SummaryDialog

        def load(per_block, sections, callback):
            ident = self.remote_source.submit("summary", dict(directory=self.remote_directory,
                                              per_block=per_block, sections=sections), callback)
            return lambda: self.remote_source.cancel(ident)
        dialog = SummaryDialog(self, self.remote_directory, None, [], loader=load)
        dialog.save_dir = str(Path.home())
        dialog.setWindowTitle(T("结果摘要（给 agent）") + " · " +
                              T("服务器 {h}:{p}", h=self.remote_source.client.host, p=self.remote_directory))
        if dialog.exec() == QDialog.Accepted:
            self.status(T("已保存结果摘要：{p}", p=dialog.saved_path) if getattr(dialog, "saved_path", None)
                        else T("已复制结果摘要"))

    def scan(self, files=None):
        if self.remote_closed:
            return
        self.remote_generation += 1
        generation = self.remote_generation
        self.status(T("正在从 {h} 读取…", h=self.remote_source.client.host))

        def partial(value):
            if not self.remote_closed and generation == self.remote_generation:
                self.apply_remote(value)

        def complete(value, error):
            if not error and generation == self.remote_generation and not self.remote_closed:
                self.apply_remote(value["data"])
                if value["data"].get("truncated"):
                    self.status(T("远程结果已截断，请选择更少的文件"))
        args = dict(directory=self.remote_directory, kind="levels")
        if isinstance(files, list):
            args["files"] = files
        self.remote_source.submit("view", args, complete,
                                  progress=partial, cached=True)

    def apply_remote(self, data):
        checked = set(self.checked_paths())
        groups, compositions, csfs, labels, origins = {}, {}, {}, {}, {}
        hamiltonians = {}
        partial = {str(PurePosixPath(self.remote_directory) / name): True for name in data.get("partial_files", [])}
        for name in data.get("files", []):
            path = str(PurePosixPath(self.remote_directory) / name)
            groups[path], csfs[path], labels[path], origins[path] = {}, {}, {}, (None, None)
        for row in data.get("levels", []):
            path = str(PurePosixPath(self.remote_directory) / row["file"])
            partial[path] = partial.get(path, False) or row.get("partial", False)
            if "zero_group" in row:
                hamiltonians[path] = dict(zero_group=row["zero_group"], zero_warning=row.get("zero_warning", ""))
            if path not in groups:
                groups[path], csfs[path], labels[path] = {}, {}, {}
            origins[path] = row.get("csf_file"), row.get("lsj_file")
            self.remote_core = getattr(self, "remote_core", {})
            self.remote_core[row.get("csf_file")] = row.get("core_orbitals", [])
            b = groups[path].get(row["block"])
            if b is None:
                j = row["J"]
                twoj = int(j[:-2]) if j.endswith("/2") else 2 * int(j)
                b = mix.Block(row["block"], row["ncf"], twoj + 1, row["parity"])
                groups[path][b.nb] = b
            parts = row["components"]
            lv = mix.Level(b, row["index"], row["energy_hartree"], SparseCoefficients(b.ncf, parts))
            lv._leading = parts[0]["csf"] if parts else 1
            lv._leading_coeff = parts[0]["coefficient"] if parts else 0
            lv._leading_csf = ((parts[0].get("configuration") or "", parts[0].get("coupling") or "")
                               if parts else ("", ""))
            lv._top = [(p["csf"], p["coefficient"]) for p in parts]
            b.levels.append(lv)
            csfs[path].setdefault(b.nb - 1, {}).update({
                p["csf"] - 1: (p.get("configuration") or "", p.get("coupling") or "") for p in parts})
            labels[path][(b.nb, lv.pos)] = row.get("lsj")
            compositions[(path, b.nb, lv.pos)] = [(p["csf"], p["coefficient"], p.get("configuration") or "",
                                                   p.get("coupling") or "") for p in parts]
        self.read_data = {}
        errors = {str(PurePosixPath(self.remote_directory) / row["file"]): row["error"]
                  for row in data.get("errors", [])}
        for path in errors:
            groups.setdefault(path, {})
        for path, bs in groups.items():
            if path in errors:
                self.read_data[path] = ValueError(errors[path])
                continue
            blocks = mix.Blocks(bs.values())
            blocks.partial = partial.get(path, False)
            fallback = dict(zero_group="DC", zero_warning="") if path.endswith(".m") else mix.unknown_hamiltonian()
            blocks.hamiltonian = hamiltonians.get(path, fallback)
            self.read_data[path] = (blocks, csfs[path] if origins[path][0] else None,
                                    origins[path][0], labels[path], origins[path][1])
        self.compositions = compositions
        self.remote_rows = data.get("transitions", [])
        if not checked:
            checked = {p for p in groups if p.endswith(".cm")} or set(groups)
        self.files.blockSignals(True)
        self.files.clear()
        for path in groups:
            item = QListWidgetItem(PurePosixPath(path).name)
            item.setData(Qt.UserRole, path)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if path in checked else Qt.Unchecked)
            self.files.addItem(item)
        self.files.blockSignals(False)
        self.render_levels()

    def redraw(self, *_):
        missing = [PurePosixPath(p).name for p in self.checked_paths()
                   if p in self.read_data and not isinstance(self.read_data[p], Exception) and not self.read_data[p][0]]
        if missing:
            self.scan([PurePosixPath(p).name for p in self.checked_paths()])
        self.render_levels()

    def cached(self, path, reader):
        return getattr(self, "remote_core", {}).get(path, [])

    @staticmethod
    def leading(lv, csfs, lsj):
        if lsj and lsj[1]:
            return LevelPanel.leading(lv, csfs, lsj)
        return lv._leading_csf[0] or T("组成尚在读取"), 100 * lv._leading_coeff ** 2

    def load_marked(self):
        self.marked = [(a, b) for a, b in self.marked if a in self.levels and b in self.levels]
        self.update_arrows()

    def save_marked(self):
        pass  # 远程图上的标记仅在当前窗口保留，不写本机或服务器路径。

    def export_grasp(self):
        self.status(T("远程模式不导出原始 GRASP 跃迁文件；可导出 CSV 或能级图。"))

    def load_transitions(self):
        self.tr_by_pair, self.tr_by_level = {}, {}
        for row in self.remote_rows:
            keys = []
            for side in ("upper", "lower"):
                file, twoj, parity, pos = row[side]
                path = str(PurePosixPath(self.remote_directory) / PurePosixPath(file).name)
                keys.append(self.key_by_id.get((path, twoj, parity, pos)))
            if None in keys:
                continue
            t = transitions.Transition(row["kind"], tuple(row["upper"]), tuple(row["lower"]),
                                       1e8 / row["wavelength_angstrom"] if row["wavelength_angstrom"] else 0,
                                       row["values"])
            a, b = keys
            self.tr_by_pair.setdefault(frozenset(keys), []).append(t)
            self.tr_by_level.setdefault(a, []).append((b, t, True))
            self.tr_by_level.setdefault(b, []).append((a, t, False))
        self.diagram.links = self.links_of(self.current)

    def show_pair(self):
        self.tr_run.setEnabled(False)
        self.tr_all.setEnabled(False)
        self.tr_title.setText(T("远程模式只查看已算跃迁；计算请运行服务器上的流程。"))
        self.tr_plan.setText("")
        self.fill_pair(self.tr_by_pair.get(frozenset(self.pair), []) if self.pair else [])

    def connect_pair(self, a, b):
        if a in self.levels and b in self.levels:
            self.select_pair(a, b)
            if self.tr_by_pair.get(frozenset(self.pair)):
                self.mark(*self.pair)

    def more_composition(self):
        key = self.current
        if not key:
            return
        path, block, pos = key

        def loaded(value, error):
            if error or self.remote_closed or key not in self.levels:
                return
            self.compositions[key] = [(p["csf"], p["coefficient"], p.get("configuration") or "",
                                       p.get("coupling") or "") for p in value["components"]]
            self.show_composition()
        self.remote_source.submit("composition", dict(directory=self.remote_directory,
                                  file=PurePosixPath(path).name, block=block, index=pos), loaded)

    def shutdown(self):
        self.remote_closed = True
        super().shutdown()


class RemoteOrbital(wfn.Orbital):
    def __init__(self, props, curve=None):
        curve = curve or dict(r=[], P=[], Q=[])
        super().__init__(props["n"], props["kappa"], props["energy"], 0, curve["r"], curve["P"], curve["Q"])
        self.props = props

    def norm(self):
        return self.props["norm"]

    def mean_r(self, k=1):
        return self.props["mean_r"]

    def r_peak(self):
        return self.props["r_peak"]

    def nodes(self, thresh=0.05):
        return self.props["nodes"]


class RemoteWaves(WfnPanel):
    def __init__(self, source, directory):
        self.remote_source, self.remote_directory = source, directory
        self.remote_generation, self.remote_closed, self.remote_data = 0, False, {}
        self.remote_loading = False
        super().__init__(directory)

    def rebuild(self):
        files = [self.files.item(i).clone() for i in range(self.files.count())]
        orbitals = {self.orbs.item(i).text(): self.orbs.item(i).checkState() for i in range(self.orbs.count())}
        plots = {key: control.isChecked() for key, control in self.show_q.items()}
        mode = self.xmode.currentIndex()
        loading, self.remote_loading = self.remote_loading, True
        self.build_view(renew_layout(self), self.remote_directory)
        for item in files:
            self.files.addItem(item)
        self.xmode.blockSignals(True)
        self.xmode.setCurrentIndex(mode)
        self.xmode.blockSignals(False)
        for key, checked in plots.items():
            self.show_q[key].setChecked(checked)
        self.files_changed()
        self.orbs.blockSignals(True)
        for i in range(self.orbs.count()):
            item = self.orbs.item(i)
            if item.text() in orbitals:
                item.setCheckState(orbitals[item.text()])
        self.orbs.blockSignals(False)
        self.redraw()
        self.remote_loading = loading

    def scan(self):
        self.fetch()

    def fetch(self, names=None):
        if self.remote_closed:
            return
        self.remote_generation += 1
        generation = self.remote_generation
        self.remote_loading = True
        self.status(T("正在从 {h} 读取…", h=self.remote_source.client.host))

        def apply(data):
            if self.remote_closed or generation != self.remote_generation:
                return
            curves = {(c["file"], c["name"]): c for c in data["curves"]}
            if names is None:
                self.remote_data = {}
            updated = {}
            for prop in data["orbitals"]:
                path = str(PurePosixPath(self.remote_directory) / prop["file"])
                updated.setdefault(path, []).append(RemoteOrbital(prop, curves.get((prop["file"], prop["name"]))))
            self.remote_data.update(updated)
            if data.get("truncated"):
                self.status(T("远程结果已截断，请选择更少的文件"))
            checked = set(self.checked_paths()) or set(self.remote_data)
            self.files.blockSignals(True)
            self.files.clear()
            for path in self.remote_data:
                self.add_item(path, path in checked)
            self.files.blockSignals(False)
            self.files_changed()

        def complete(value, error):
            if generation != self.remote_generation or self.remote_closed:
                return
            if not error:
                apply(value["data"])
            self.remote_loading = False
        args = dict(directory=self.remote_directory, kind="wfn", points=1000)
        if names is not None:
            args["names"] = names
            args["files"] = [PurePosixPath(p).name for p in self.checked_paths()]
        self.remote_source.submit("view", args, complete, progress=apply, cached=True)

    def load(self, path):
        return self.remote_data.get(path, [])

    def redraw(self, *_args, reset=False):
        if not self.remote_loading:
            wanted = [self.orbs.item(i).text() for i in range(self.orbs.count())
                      if self.orbs.item(i).checkState() == Qt.Checked]
            selected = {n for n in wanted if any(o.name == n and not o.r
                        for p in self.checked_paths() for o in self.remote_data.get(p, []))}
            if selected:
                self.fetch(wanted)
        super().redraw(reset=reset)

    def add_file(self):
        self.status(T("远程文件请在服务器目录中选择"))

    def fill_table(self, selected):
        super().fill_table(selected)
        original = {(label, o.name): o.props["points"] for label, o, *_ in selected}
        for i in range(self.table.rowCount()):
            key = self.table.item(i, 0).text(), self.table.item(i, 1).text()
            self.table.item(i, 8).setData(Qt.DisplayRole, original[key])

    def shutdown(self):
        self.remote_closed = True
        super().shutdown()


class ServerRunDialog(QDialog):
    """公共工作站运行确认：检查在服务器执行，编辑不触碰本机运行偏好。"""

    def __init__(self, parent, source, directory, plan, range_args, *, run=True):
        super().__init__(parent)
        self.source, self.directory, self.plan = source, directory, plan
        self.range_args, self.run = range_args, run
        self.modern = "run_settings_v1" in getattr(source, "server", {}).get("capabilities", [])
        self.closed, self.generation, self.checked_values = False, 0, None
        self.setWindowTitle(T("服务器运行设置"))
        self.resize(940, 400)
        layout = QVBoxLayout(self)
        label = QLabel(T("服务器 {h}:{p}", h=source.client.host, p=directory))
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(label)
        values = dict(bindir="", np=1, mpirun="mpirun", mpitmp="tmp_mpi", nice=True)
        values.update(plan.get("run_settings", {}))
        if self.modern:
            saved = core.load_config().get("remote_hosts", {}).get(source.client.host, {}).get("run", {})
            values.update(saved)
        self.settings = RunSettings(values, remote=True)
        self.settings.setEnabled(self.modern)
        layout.addWidget(self.settings)
        self.report = QPlainTextEdit()
        self.report.setReadOnly(True)
        layout.addWidget(self.report, 1)
        bar = QHBoxLayout()
        self.check_button = button("检查", self.check)
        self.check_button.setEnabled(self.modern)
        self.ok = button("确认运行" if run else "保存", self.confirm)
        self.cancel = button("取消", self.reject)
        for widget in (self.check_button, self.ok, self.cancel):
            widget.setAutoDefault(False)
            bar.addWidget(widget)
        layout.addLayout(bar)
        self.cancel.setDefault(True)
        self.finished.connect(self.finish)
        for edit in (self.settings.bindir_edit, self.settings.mpirun_edit, self.settings.mpitmp_edit):
            edit.textChanged.connect(self.changed)
        self.settings.np_spin.valueChanged.connect(self.changed)
        self.settings.nice_check.toggled.connect(self.changed)
        if self.modern:
            QTimer.singleShot(0, self.check)
        else:
            self.show_report(plan)
            self.report.appendPlainText(T("服务器端版本较旧，设置只读；请用“安装 / 更新服务器版…”更新后再修改。"))

    def finish(self, _):
        self.closed = True
        self.generation += 1

    def changed(self, *_):
        self.generation += 1
        self.checked_values = None
        self.ok.setEnabled(True)
        self.cancel.setDefault(True)
        self.report.setPlainText(T("设置已改变，请检查；确认前会自动检查。"))

    def show_report(self, plan):
        missing = plan.get("missing_programs", [])
        lines = [T("服务器缺少程序：{names}", names=", ".join(missing) or "—")]
        checked = plan.get("checked")
        if checked is not None:
            lines.append(summary.check_text(checked) if checked["issues"] else T("输入预检通过。"))
        self.report.setPlainText("\n\n".join(lines))
        self.ok.setDefault(not missing)
        self.cancel.setDefault(bool(missing))

    def check(self, *, accept=False):
        if self.closed or not self.modern:
            return
        self.generation += 1
        generation, values = self.generation, self.settings.values()
        self.ok.setEnabled(False)
        self.report.setPlainText(T("正在检查服务器运行设置…"))
        def received(plan, error):
            if self.closed or generation != self.generation:
                return
            self.ok.setEnabled(True)
            if error or plan["revision"] != self.plan["revision"]:
                self.checked_values = None
                self.report.setPlainText(str(error) if error else T("工作目录记录已被其他程序修改，请重新载入"))
                self.cancel.setDefault(True)
                return
            self.plan = plan
            self.checked_values = values
            self.show_report(plan)
            if accept:
                # 检查得到警告后须再次明确确认，不让异步回答直接启动计算。
                if plan.get("missing_programs") or plan.get("checked", {}).get("issues"):
                    return
                self.confirm()
        args = dict(directory=self.directory, run_settings=values, **self.range_args)
        self.source.submit("run_plan", args, received)

    def confirm(self):
        if self.modern and self.checked_values != self.settings.values():
            self.check(accept=True)
            return
        if self.modern:
            profiles = core.load_config().get("remote_hosts", {})
            profiles.setdefault(self.source.client.host, {})["run"] = self.settings.values()
            core.save_config(remote_hosts=profiles)
        self.accept()


class RemoteCalculation(QWidget):
    editRequested = Signal(str)
    created = Signal(str, bool)
    filesChanged = Signal()
    draftChanged = Signal()

    def __init__(self, source, directory, run_after_load=False):
        super().__init__()
        self.source, self.directory, self.jobs = source, directory, []
        self.revision = None
        self.run_after_load = run_after_load
        self.server_type = getattr(source, "server_type", "workstation")
        self.submitting = False
        self.cleaning = False
        self.can_run = False
        self.slurm_state = None
        self.runtime_state = None
        self.has_runtime_state = False
        self.watch_error = None
        self.editor = None             # 服务器端可以写入且已有流程时：RemoteEditor
        self.build_view()
        self.source.push.connect(self.push)
        self.load()

    def writable(self):
        return ("workdir_write_v1" in getattr(self.source, "server", {}).get("capabilities", [])
                and getattr(self.source, "key", None) is not None)

    def build_view(self):
        if self.editor is not None:
            self.build_edit_view()
            return
        layout = renew_layout(self)
        hint = T("远程流程只读；可取出到本机编辑，再确认上传。")
        if "workdir_write_v1" not in getattr(self.source, "server", {}).get("capabilities", []):
            hint += " " + T("服务器端版本较旧，请用“安装 / 更新服务器版…”更新。")
        self.readonly_hint = QLabel(hint)
        layout.addWidget(self.readonly_hint)
        layout.addWidget(QLabel(T("服务器类型：{kind}", kind=T("Slurm 集群") if self.server_type == "slurm" else
                                 T("工作站"))))
        bar = self.run_bar()
        bar.addWidget(button("刷新", self.load))
        self.edit_button = button("在本机编辑…", self.edit_local)
        self.edit_button.setEnabled("workdir_write_v1" in getattr(self.source, "server", {}).get("capabilities", []))
        if not self.edit_button.isEnabled():
            self.edit_button.setToolTip(T("服务器端版本较旧，请用“安装 / 更新服务器版…”更新。"))
        bar.addWidget(self.edit_button)
        self.clean_button = button("清空工作目录…", self.preview_clean)
        self.update_clean_button()
        bar.addWidget(self.clean_button)
        self.state = QLabel()
        self.state.setWordWrap(True)
        bar.addWidget(self.state, 1)
        layout.addLayout(bar)
        self.create_button = button("在此新建计算…", self.create_calculation)
        self.create_button.hide()
        layout.addWidget(self.create_button)
        self.split = split = QSplitter()
        self.steps = QListWidget()
        self.steps.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.steps.itemSelectionChanged.connect(self.update_run_buttons)
        self.steps.currentRowChanged.connect(self.show_job)
        split.addWidget(self.steps)
        self.details = QTabWidget()
        self.parameters = QPlainTextEdit()
        self.parameters.setReadOnly(True)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setMaximumBlockCount(core.MAX_OUT_LINES)
        self.details.addTab(self.parameters, T("参数"))
        self.details.addTab(self.preview, T("输入预览"))
        self.details.addTab(self.output, T("程序输出"))
        split.addWidget(self.details)
        layout.addWidget(split, 1)
        self.clear_record = button("我已核对，清除此作业记录…", self.clear_slurm_record)
        self.clear_record.setVisible(False)
        layout.addWidget(self.clear_record)
        self.update_run_buttons()

    def run_bar(self):
        bar = QHBoxLayout()
        self.run_button = button("▶ 运行全部", lambda: self.prepare_run(1))
        if self.server_type == "slurm":
            self.run_button.setText(T("提交 Slurm 作业"))
        bar.addWidget(self.run_button)
        self.run_selected = button("▶ 从选中开始", lambda: self.prepare_selection("from"))
        bar.addWidget(self.run_selected)
        self.run_to = button("▶ 运行到选中", lambda: self.prepare_selection("to"))
        self.run_range = button("▶ 运行选中的步骤", lambda: self.prepare_selection("selected"))
        bar.addWidget(self.run_to)
        bar.addWidget(self.run_range)
        self.stop_button = button("■ 停止", self.stop)
        self.stop_button.setEnabled(False)
        bar.addWidget(self.stop_button)
        return bar

    def build_edit_view(self):
        """可以写入的服务器：与本机相同的程序页和流程编辑；修改先存为本机草稿。"""
        editor = self.editor
        layout = renew_layout(self)
        self.readonly_hint = QLabel(T("修改先保存为本机草稿，点“保存到服务器…”后写入服务器。"))
        layout.addWidget(self.readonly_hint)
        layout.addWidget(QLabel(T("服务器类型：{kind}", kind=T("Slurm 集群") if self.server_type == "slurm" else
                                 T("工作站"))))
        bar = self.run_bar()
        self.save_button = button("保存到服务器…", lambda: editor.save_to_server())
        bar.addWidget(self.save_button)
        draft = QToolButton()
        draft.setText(T("草稿"))
        draft.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(draft)
        self.export_action = menu.addAction(T("导出草稿为配置…"))
        self.export_action.triggered.connect(lambda: editor.export_draft())
        menu.addAction(T("重新读取服务器流程 (放弃草稿)")).triggered.connect(lambda: editor.reload())
        draft.setMenu(menu)
        bar.addWidget(draft)
        self.edit_button = button("在本机编辑…", self.edit_local)
        bar.addWidget(self.edit_button)
        self.clean_button = button("清空工作目录…", self.preview_clean)
        self.update_clean_button()
        bar.addWidget(self.clean_button)
        self.state = QLabel()
        self.state.setWordWrap(True)
        bar.addWidget(self.state, 1)
        layout.addLayout(bar)
        self.create_button = button("在此新建计算…", self.create_calculation)
        self.create_button.hide()
        layout.addWidget(self.create_button)
        forms, steps = editor.build()
        self.steps = editor.plist
        self.preview = editor.prev
        self.details = QTabWidget()
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setMaximumBlockCount(core.MAX_OUT_LINES)
        self.details.addTab(self.preview, T("输入预览"))
        self.details.addTab(self.output, T("程序输出"))
        bottom = QSplitter()
        bottom.addWidget(steps)
        bottom.addWidget(self.details)
        self.split = QSplitter(Qt.Vertical)
        self.split.addWidget(forms)
        self.split.addWidget(bottom)
        self.split.setStretchFactor(0, 3)
        self.split.setStretchFactor(1, 2)
        layout.addWidget(self.split, 1)
        self.clear_record = button("我已核对，清除此作业记录…", self.clear_slurm_record)
        self.clear_record.setVisible(False)
        layout.addWidget(self.clear_record)
        self.update_run_buttons()
        self.update_draft_buttons()

    def update_draft_buttons(self):
        if self.editor is None:
            return
        dirty = self.editor.draft is not None and not self.editor.saving
        self.save_button.setEnabled(dirty and self.editor.blocked is None)
        self.export_action.setEnabled(self.editor.draft is not None)

    def draft_changed(self):
        self.update_draft_buttons()
        self.draftChanged.emit()

    def start_editing(self, value):
        """第一次读到可写入的服务器流程时换成编辑界面，保留程序输出和运行状态。"""
        if self.editor is None:
            from remote_editor import RemoteEditor
            text, show_create = self.output.toPlainText(), not self.create_button.isHidden()
            self.editor = RemoteEditor(self, self.source.key)
            self.editor.message.connect(self.source.notice.emit)
            self.build_view()
            self.output.setPlainText(text)
            self.create_button.setVisible(show_create)
            if self.has_runtime_state:
                self.show_state(self.runtime_state)
            if self.watch_error:
                self.show_watch_error(self.watch_error)
        self.editor.start(value)

    def rebuild(self):
        if self.editor is not None:
            fields, row = self.editor.fields(), self.steps.currentRow()
            selected = [self.steps.row(item) for item in self.steps.selectedItems()]
        else:
            row, selected = self.steps.currentRow(), [self.steps.row(item) for item in self.steps.selectedItems()]
        text, tab, sizes = self.output.toPlainText(), self.details.currentIndex(), self.split.sizes()
        show_create = not self.create_button.isHidden()
        self.build_view()
        if self.editor is not None:
            self.editor.apply_fields(fields)
            self.steps.setCurrentRow(row)
        else:
            self.fill_jobs(row)
        for i in range(self.steps.count()):
            self.steps.item(i).setSelected(i in selected)
        self.details.setCurrentIndex(tab)
        self.split.setSizes(sizes)
        self.output.setPlainText(text)
        self.create_button.setVisible(show_create)
        if self.has_runtime_state:
            self.show_state(self.runtime_state)
        if self.watch_error:
            self.show_watch_error(self.watch_error)

    def fill_jobs(self, row=0):
        self.steps.clear()
        self.steps.addItems([f"{i + 1}. {job.get('title', '')}" for i, job in enumerate(self.jobs)])
        self.steps.setCurrentRow(row)
        self.update_run_buttons()

    def show_watch_error(self, error):
        self.watch_error = error
        self.can_run = False
        self.update_run_buttons()
        self.stop_button.setEnabled(False)
        self.state.setText(str(error))

    def load(self):
        self.create_button.hide()
        def loaded(value, error):
            if error:
                return
            self.jobs, self.revision = value["jobs"], value["revision"]
            self.create_button.setVisible(not self.jobs)
            if self.editor is None:
                self.fill_jobs()
            if self.jobs and self.writable():
                self.source.submit("get_pipeline", dict(directory=self.directory), edit_loaded)
            if self.run_after_load:
                self.run_after_load = False
                self.prepare_run(1)
        def edit_loaded(value, error):
            if error:
                self.source.notice.emit(str(error))
            else:
                self.start_editing(value)
        self.source.submit("pipeline", dict(directory=self.directory), loaded)

        def watched(value, error):
            if error:
                self.show_watch_error(error)
                return
            self.output.setPlainText(value["text"])
            self.show_state(value["state"])
        self.source.submit("watch", dict(directory=self.directory), watched)

    def create_calculation(self):
        from remote_import_panel import create
        create(self, self.source, self.directory, self.created.emit)

    def edit_local(self):
        from remote_edit_panel import take_out
        take_out(self.source, self.directory, self, self.editRequested.emit)

    def show_job(self, position):
        if not 0 <= position < len(self.jobs):
            return
        job = self.jobs[position]
        self.parameters.setPlainText(json.dumps(config.export_job(job), ensure_ascii=False, indent=2))
        self.preview.setPlainText(core.describe(config.make_job(config.export_job(job))))

    def push(self, event):
        if event.get("directory") != self.directory:
            return
        if event["event"] == "state":
            self.show_state(event["state"])
        elif event["event"] == "log":
            if event.get("reset"):
                self.output.clear()
            cursor = self.output.textCursor()
            cursor.movePosition(QTextCursor.End)
            cursor.insertText(event["text"])
            self.output.setTextCursor(cursor)

    def show_state(self, state):
        self.runtime_state = state
        self.has_runtime_state = True
        self.watch_error = None
        self.slurm_state = state if (state or {}).get("backend") == "slurm" else None
        self.clear_record.setVisible(bool(self.slurm_state and state.get("can_clear")))
        self.clear_record.setEnabled(not self.submitting)
        busy = self.submitting or state and (state.get("active", False) or state.get("status") == "running")
        self.can_run = not busy
        self.update_run_buttons()
        self.stop_button.setEnabled(bool(busy) and not self.submitting and (state or {}).get("can_cancel", True))
        if not state:
            self.state.setText(T("正在提交 Slurm 作业…") if self.submitting else T("没有运行记录"))
            return
        if state.get("backend") == "slurm":
            labels = dict(queued=T("Slurm 排队中"), running=T("Slurm 作业进行中"), ok=T("Slurm 作业已完成"),
                          failed=T("Slurm 作业失败"), stopped=T("Slurm 作业已取消"), unknown=T("Slurm 状态未知"))
            text = f"{labels.get(state['status'], labels['unknown'])} · {state.get('job_id') or '?'}"
            text += " · " + state.get("scheduler_state", state["status"])
            if state.get("exit_code"):
                text += " · " + T("退出码 {code}", code=state["exit_code"])
            self.state.setText(text + (" · " + state["error"] if state.get("error") else ""))
            return
        titles = state.get("titles", [])
        span = summary.title_range(self.jobs, titles)
        if span and state.get("status") in ("ok", "failed", "stopped", "lost"):
            from calc_panel import run_summary
            self.state.setText(run_summary(state, titles, span))
            return
        pos = state.get("step", 0)
        title = titles[pos] if 0 <= pos < len(titles) else ""
        self.state.setText(f"{state['status']} · {pos + 1}/{len(titles)} · {title}")

    def clear_slurm_record(self):
        state = self.slurm_state
        if not state or not state.get("can_clear") or self.submitting:
            return
        if QMessageBox.question(self, T("清除 Slurm 作业记录"),
                T("确认已人工核对任务？服务器将重新查询队列，仅在没有相关作业时归档记录；这不会取消任务，也不代表计算成功。"),
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        self.submitting = True
        self.show_state(state)
        self.source.submit("clear_slurm_record", dict(directory=self.directory,
                           revision=state["record_revision"], confirmed=True), self.started)

    def has_run_range(self):
        return "run_range_v1" in getattr(self.source, "server", {}).get("capabilities", [])

    def update_clean_button(self):
        supported = "workdir_clean_v1" in getattr(self.source, "server", {}).get("capabilities", [])
        self.clean_button.setEnabled(supported and self.can_run and bool(self.revision) and not self.cleaning)
        self.clean_button.setToolTip("" if supported else T("服务器端版本较旧，请用“安装 / 更新服务器版…”更新。"))

    def preview_clean(self, *, recovering=False):
        from calc_panel import ClearDialog, clean_result

        if self.cleaning or (not recovering and not self.clean_button.isEnabled()):
            return
        self.cleaning = True
        self.update_run_buttons()
        target = dict(directory=self.directory)

        def finished(value, error, run_after):
            self.cleaning = False
            self.update_run_buttons()
            if isinstance(error, ssh.UnknownOutcome):
                self.filesChanged.emit()
                QMessageBox.warning(self, T("清空工作目录"), T("清理结果未知。将重新读取文件清单，请核对后再操作。"))
                self.preview_clean(recovering=True)
            elif error:
                QMessageBox.warning(self, T("清空工作目录"), str(error))
            else:
                self.filesChanged.emit()
                self.run_after_load = clean_result(self, value) and run_after
                if not value["error"]:
                    self.source.notice.emit(T("清理完成"))
                self.load()

        def show(plan, error):
            if error:
                self.cleaning = False
                self.update_run_buttons()
                QMessageBox.warning(self, T("清空工作目录"), str(error))
                return
            dlg = ClearDialog(self, plan["root"], plan=plan, can_run=bool(self.jobs))
            accepted = dlg.exec() == QDialog.Accepted
            backup = dlg.backup_to_delete
            names = [backup] if backup else sorted(dlg.selected_names())
            mode = "delete" if backup else dlg.mode.currentData()
            run_after = not backup and dlg.run_after.isChecked()
            dlg.deleteLater()
            if accepted:
                self.source.submit("clean_workdir", dict(target, revision=plan["revision"], names=names, mode=mode),
                                   lambda value, err: finished(value, err, run_after))
            else:
                self.cleaning = False
                self.update_run_buttons()

        self.source.submit("preview_clean", target, show)

    def update_run_buttons(self):
        for mode, button in (("all", self.run_button), ("from", self.run_selected),
                             ("to", self.run_to), ("selected", self.run_range)):
            try:
                summary.selected_run_range(len(self.jobs), [i.row() for i in self.steps.selectedIndexes()], mode)
                hint = ""
            except ValueError as e:
                hint = str(e)
            if mode in ("to", "selected") and not self.has_run_range():
                hint = T("服务器端版本较旧，请用“安装 / 更新服务器版…”更新。")
            saving = self.editor is not None and self.editor.saving
            button.setEnabled(self.can_run and not self.cleaning and not saving and not hint)
            button.setToolTip(hint)
        self.update_clean_button()

    def save_first(self, retry):
        """服务器按自己的流程运行：有草稿时先保存到服务器，成功后再沿用运行确认。"""
        if self.editor is None or self.editor.draft is None:
            return False
        if QMessageBox.question(self, T("远程运行"), T("流程有未保存到服务器的修改，需要先保存。现在保存吗？"),
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
            self.editor.save_to_server(lambda ok: ok and retry())
        return True

    def prepare_selection(self, mode):
        if self.save_first(lambda: self.prepare_selection(mode)):
            return
        try:
            first, last = summary.selected_run_range(
                len(self.jobs), [i.row() for i in self.steps.selectedIndexes()], mode)
        except ValueError as e:
            QMessageBox.information(self, T("远程运行"), str(e))
            return
        self.prepare_run(first + 1, last if mode in ("to", "selected") else None)

    def prepare_run(self, start, end=None):
        if not self.jobs or not self.revision or start < 1 or self.submitting or self.cleaning:
            return
        if self.save_first(lambda: self.prepare_run(start, end)):
            return
        if end is not None and not self.has_run_range():
            QMessageBox.warning(self, T("远程运行"), T("服务器端版本较旧，请用“安装 / 更新服务器版…”更新。"))
            return
        try:
            first, last = summary.run_bounds(len(self.jobs), start, end)
        except ValueError as e:
            QMessageBox.warning(self, T("远程运行"), str(e))
            return
        span = (first + 1, last, len(self.jobs))
        range_args = dict(start=start, end=last) if self.has_run_range() else {}
        if self.server_type == "slurm" and "slurm" not in getattr(self.source, "server", {}).get("server_types", []):
            QMessageBox.warning(self, T("远程运行"), T("服务器端尚不支持 Slurm，请先更新服务器端程序。"))
            return

        def planned(plan, error):
            if error:
                return
            if plan["revision"] != self.revision:
                QMessageBox.warning(self, T("远程流程"), T("工作目录记录已被其他程序修改，请重新载入"))
                self.load()
                return
            custom = bool(plan["custom_commands"])
            if custom and QMessageBox.question(self, T("自定义命令"),
                    T("远程流程包含可运行任意程序的自定义命令。确认运行吗？") + "\n" +
                    json.dumps(plan["custom_commands"], ensure_ascii=False, indent=1),
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                return
            if self.server_type == "slurm":
                from calc_panel import SlurmDialog
                saved = core.load_config().get("remote_hosts", {}).get(self.source.client.host, {})
                settings = dict(plan["slurm"], **saved.get("slurm", {}))
                dialog = SlurmDialog(self, settings, submit=True, span=span)
                if dialog.exec() != QDialog.Accepted:
                    return
                settings = dialog.settings()
                if QMessageBox.question(self, T("提交 Slurm 作业"),
                        T("将在服务器目录 {p} 提交 Slurm 作业，是否确认？", p=self.directory) +
                        "\n" + summary.range_text(*span),
                        QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                    return
                profiles = core.load_config().get("remote_hosts", {})
                profiles.setdefault(self.source.client.host, {})["slurm"] = settings
                core.save_config(remote_hosts=profiles)
                self.submitting = True
                self.show_state(None)
                args = dict(directory=self.directory, revision=self.revision, start=start,
                            allow_custom=custom, server_type="slurm", slurm=settings)
                args.update(range_args)
                self.source.submit("run", args, self.started)
                return
            dialog = ServerRunDialog(self, self.source, self.directory, plan, range_args)
            if dialog.exec() != QDialog.Accepted:
                dialog.deleteLater()
                return
            plan = dialog.plan
            settings = dialog.settings.values() if dialog.modern else None
            dialog.deleteLater()
            scope = accept_logout = False
            if plan["logout_kills"]:
                box = QMessageBox(self)
                box.setWindowTitle(T("断线与计算"))
                box.setText(T("服务器退出登录会结束计算。可使用用户 scope（须开启 lingering），导出 Slurm，或明确选择照常启动。"))
                scope_button = box.addButton(T("使用用户 scope"), QMessageBox.AcceptRole) if plan["lingering"] else None
                normal = box.addButton(T("照常启动"), QMessageBox.ActionRole)
                slurm = box.addButton(T("导出 Slurm 作业脚本…"), QMessageBox.ActionRole)
                cancel = box.addButton(T("取消"), QMessageBox.RejectRole)
                box.setDefaultButton(cancel)
                box.exec()
                if box.clickedButton() == slurm:
                    self.source.submit("export_script", dict(directory=self.directory, slurm={}), self.copy_script)
                    return
                if box.clickedButton() == scope_button and scope_button:
                    scope = True
                elif box.clickedButton() == normal:
                    accept_logout = True
                else:
                    return
            args = dict(directory=self.directory, revision=self.revision, start=start, allow_custom=custom,
                        user_scope=scope, accept_logout=accept_logout)
            args.update(range_args)
            if settings is not None:
                args["run_settings"] = settings
            self.source.submit("run", args, self.started)
        args = dict(directory=self.directory, **range_args)
        if self.server_type == "slurm":
            args["server_type"] = "slurm"
        self.source.submit("run_plan", args, planned)

    def edit_run_settings(self):
        def received(plan, error):
            if error:
                QMessageBox.warning(self, T("服务器运行设置"), str(error))
                return
            dialog = ServerRunDialog(self, self.source, self.directory, plan, {}, run=False)
            dialog.exec()
            dialog.deleteLater()
        self.source.submit("run_plan", dict(directory=self.directory), received)

    def copy_script(self, value, error):
        if not error:
            from PySide6.QtGui import QGuiApplication
            QGuiApplication.clipboard().setText(value["text"])
            QMessageBox.information(self, T("Slurm"), T("已复制 Slurm 脚本，请核对服务器设置后提交。"))

    def started(self, value, error):
        self.submitting = False
        if error:
            text = str(error)
            if self.server_type != "slurm":
                text += "\n" + T("用户 scope 失败时可导出 Slurm，或重新选择照常启动；请先核对运行状态。")
            QMessageBox.warning(self, T("远程运行"), text)
        elif value:
            self.revision = value.get("revision", self.revision)
            if value.get("warnings"):
                QMessageBox.warning(self, T("远程运行"), "\n".join(value["warnings"]))
        self.load()

    def stop(self):
        self.source.submit("stop", dict(directory=self.directory), self.started)


def workspace_key(source, directory):
    """目录取服务器返回的绝对路径，连接标识也供草稿使用。"""
    identity = getattr(source, "workspace_identity", None)
    if identity is None:
        identity = getattr(source.client, "identity", None) or ssh.connection_identity(source.client, resolve=False)
        source.workspace_identity = identity
    return identity, posixpath.normpath(directory)


class RemoteViewer(QMainWindow):
    def closeEvent(self, event):
        self.centralWidget().shutdown()
        super().closeEvent(event)


class RemoteWorkspace(QWidget):
    TABS = (("calc", "计算"), ("wfn", "径向波函数"), ("levels", "能级图"))
    push = Signal(object)
    draftChanged = Signal()

    def __init__(self, source, directory, parent=None, run_after_load=False):
        super().__init__(parent)
        self.source, self.directory, self.detached = source, directory, []
        self.transfer_dialogs = []
        self.owner = parent
        self.closed = False
        self.keep_source = False
        source.setParent(self)
        self.key = workspace_key(source, directory)
        source.push.connect(self.push)
        if hasattr(source, "connectionChanged"):
            source.connectionChanged.connect(self.update_connection)
        self.status = QStatusBar(self)
        source.notice.connect(self.status.showMessage)
        self.calc = RemoteCalculation(self, directory, run_after_load)
        self.calc.created.connect(lambda path, run: QTimer.singleShot(0, lambda: self.open_created(path, run)))
        self.calc.draftChanged.connect(self.draftChanged)
        if parent and hasattr(parent, "open_path"):
            def open_local(path):
                if hasattr(parent, "workspaces"):
                    parent.workspaces.setCurrentIndex(0)
                if getattr(parent, "cur_workdir", None) == path:
                    parent.calc.open_workdir(path)
                else:
                    parent.open_path(path)
                if hasattr(parent, "show_tab"):
                    parent.show_tab("calc")
                parent.show()
                parent.raise_()
            self.calc.editRequested.connect(open_local)
        self.waves = RemoteWaves(self, directory)
        self.levels = RemoteLevels(self, directory)
        self.calc.filesChanged.connect(self.refresh_results)
        self.waves.message.connect(self.status.showMessage)
        self.levels.message.connect(self.status.showMessage)
        self.build()

    @property
    def client(self):
        return self.source.client

    @property
    def server(self):
        return getattr(self.source, "server", {})

    @property
    def notice(self):
        return self.source.notice

    @property
    def server_type(self):
        return getattr(self.source, "server_type", "workstation")

    def submit(self, method, args, callback, **options):
        """让回答随工作区存活，重建后由面板更新当前控件。"""
        def deliver(target):
            def received(*values):
                if not self.closed:
                    target(*values)
            return received
        if options.get("progress"):
            options["progress"] = deliver(options["progress"])
        return self.source.submit(method, args, deliver(callback), **options)

    def cancel(self, request):
        self.source.cancel(request)

    def statusBar(self):
        return self.status

    def build(self, index=0):
        layout = renew_layout(self)
        self.label = T("服务器 {h}:{p}", h=self.client.host, p=self.directory)
        self.setWindowTitle(self.label)
        bar = QHBoxLayout()
        name = QLabel(self.label)
        name.setTextInteractionFlags(Qt.TextSelectableByMouse)
        bar.addWidget(name, 1)
        self.connection = QLabel()
        bar.addWidget(self.connection)
        self.reconnect_button = button("重新连接", self.source.reconnect)
        bar.addWidget(self.reconnect_button)
        self.update_connection()
        operations = QToolButton()
        operations.setText(T("服务器操作"))
        operations.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(operations)
        if self.server_type == "workstation":
            menu.addAction(T("服务器运行设置…")).triggered.connect(lambda: self.calc.edit_run_settings())
        menu.addAction(T("下载到本机…")).triggered.connect(lambda: self.open_transfer())
        menu.addAction(T("从本机上传…")).triggered.connect(lambda: self.open_transfer(upload=True))
        menu.addAction(T("上传本机目录…")).triggered.connect(lambda: self.open_transfer(upload=True, folder=True))
        edit = menu.addAction(T("在本机编辑…"))
        edit.setEnabled("workdir_write_v1" in self.server.get("capabilities", []))
        edit.triggered.connect(lambda: self.calc.edit_local())
        menu.addAction(T("在此新建计算…")).triggered.connect(lambda: self.calc.create_calculation())
        menu.addAction(T("重新连接")).triggered.connect(self.source.reconnect)
        operations.setMenu(menu)
        bar.addWidget(operations)
        layout.addLayout(bar)
        self.tabs = QTabWidget()
        for widget, (_, title) in zip((self.calc, self.waves, self.levels), self.TABS):
            self.tabs.addTab(widget, T(title))
        detach = QToolButton()
        detach.setText(T("在新窗口中打开"))
        detach.clicked.connect(lambda: self.open_view(self.TABS[self.tabs.currentIndex()][0]))
        self.tabs.setCornerWidget(detach, Qt.TopRightCorner)
        self.tabs.currentChanged.connect(lambda i: detach.setVisible(i > 0))
        self.tabs.setCurrentIndex(index)
        detach.setVisible(index > 0)
        layout.addWidget(self.tabs, 1)
        layout.addWidget(self.status)

    def update_connection(self):
        connected = getattr(self.client, "connected", True)
        self.connection.setText(f"{self.server_label()} · {T('已连接') if connected else T('已断开')}")
        self.reconnect_button.setVisible(not connected)
        editor = self.calc.editor
        if connected and editor is not None and editor.draft and editor.draft.get("pending") and not editor.saving:
            editor.reconcile()

    def has_draft(self):
        return self.calc.editor is not None and self.calc.editor.draft is not None

    def resolve_draft(self, choice, done):
        """关闭时处理草稿：save 保存到服务器，discard 放弃，keep 保留。done(可以关闭)。"""
        editor = self.calc.editor
        if not self.has_draft() or choice == "keep" or editor.saving:
            # 等待回答的写入保留待核对状态，下次打开时核对。
            done(True)
        elif choice == "discard":
            editor.discard()
            done(True)
        else:
            editor.save_to_server(done)

    def server_label(self):
        return T("Slurm 集群") if self.server_type == "slurm" else T("工作站")

    def show_tab(self, kind):
        for i, (name, _) in enumerate(self.TABS):
            if kind == name:
                self.tabs.setCurrentIndex(i)
                return

    def rebuild(self):
        index = self.tabs.currentIndex()
        for panel in (self.calc, self.waves, self.levels):
            panel.setParent(None)
            panel.rebuild()
        self.status.setParent(None)
        self.build(index)
        for window, panel in self.detached:
            window.setWindowTitle(self.label)
            panel.rebuild()
        for dialog in self.transfer_dialogs:
            dialog.rebuild()

    def refresh_results(self):
        self.waves.scan()
        self.levels.scan()
        for _, panel in self.detached:
            panel.scan()

    def open_transfer(self, upload=False, folder=False):
        from PySide6.QtWidgets import QFileDialog
        from transfer_panel import TransferDialog
        local, names = None, None
        if upload:
            if folder:
                chosen = QFileDialog.getExistingDirectory(self, T("上传本机目录…"), str(Path.home()))
                paths = [Path(chosen)] if chosen else []
            else:
                chosen, _ = QFileDialog.getOpenFileNames(self, T("从本机上传…"), str(Path.home()))
                paths = [Path(value) for value in chosen]
            if not paths:
                return
            local = os.path.commonpath([str(path.parent) for path in paths])
            names = [path.relative_to(local).as_posix() for path in paths]
        dialog = TransferDialog(self, self.directory, local, names, self)
        self.transfer_dialogs.append(dialog)
        dialog.finished.connect(lambda result: self.transfer_dialogs.remove(dialog))
        dialog.finished.connect(dialog.deleteLater)
        dialog.show()

    def open_created(self, directory, run):
        if self.closed:
            return
        parent = self.owner
        if self.calc.jobs:
            if workspace_key(self.source, directory) in parent.remote_workspaces:
                parent.open_remote_workspace(self.source, directory, refresh=True, run_after_load=run)
            else:
                try:
                    source = self.source.fork()
                except (OSError, ValueError) as error:
                    self.status.showMessage(str(error))
                    return
                parent.open_remote_workspace(source, directory, run_after_load=run)
        elif self.may_close():
            parent.open_remote_workspace(self.source, directory, run_after_load=run, replace=self)

    def open_view(self, kind):
        if kind not in ("wfn", "levels"):
            return
        win = RemoteViewer(self)
        win.setAttribute(Qt.WA_DeleteOnClose)
        win.setWindowTitle(self.label)
        panel = RemoteWaves(self, self.directory) if kind == "wfn" else RemoteLevels(self, self.directory)
        entry = (win, panel)
        self.detached.append(entry)
        win.destroyed.connect(lambda: self.forget_view(entry))
        win.setCentralWidget(panel)
        win.resize(1250, 800)
        win.show()
        return win

    def forget_view(self, entry):
        if entry in self.detached:
            self.detached.remove(entry)

    def may_close(self):
        creation = getattr(self.calc, "import_dialog", None)
        if not any(dialog.busy for dialog in self.transfer_dialogs) and not (creation and creation.busy):
            return True
        return QMessageBox.question(self, T("取消传输"),
                                    T("传输仍在进行，关闭工作区将取消传输。是否取消并关闭？"),
                                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes

    def shutdown(self):
        if self.closed:
            return
        self.closed = True
        self.source.push.disconnect(self.push)
        self.source.notice.disconnect(self.status.showMessage)
        if hasattr(self.source, "connectionChanged"):
            self.source.connectionChanged.disconnect(self.update_connection)
        if getattr(self.calc, "import_dialog", None) is not None:
            self.calc.import_dialog.shutdown()
        for dialog in list(self.transfer_dialogs):
            dialog.shutdown()
        for win, _ in self.detached[:]:
            win.close()
        self.detached.clear()
        self.waves.shutdown()
        self.levels.shutdown()
        if not self.keep_source:
            self.source.close()


def connect_window(parent):
    dialog = ConnectDialog(parent)
    if dialog.exec() != QDialog.Accepted:
        return None
    return parent.open_remote_workspace(dialog.source, dialog.directory, run_after_load=dialog.run_after_load)
