"""SSH 连接/目录浏览、只读远程流程，以及现有查看器的数据源适配。"""

import json
import os
from pathlib import Path, PurePosixPath
import queue

from PySide6.QtCore import Qt, QTimer, Signal, QObject
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMainWindow, QMessageBox, QPlainTextEdit, QPushButton, QSplitter,
                               QTabWidget, QVBoxLayout, QWidget)

import grasp_config as config
import grasp_install as install
import grasp_core as core
import grasp_mix as mix
import grasp_ssh as ssh
import grasp_trans as transitions
import grasp_wfn as wfn
from grasp_core import T
from gui_common import button
from level_panel import LevelPanel
from wfn_panel import WfnPanel


class Source(QObject):
    notice = Signal(str)
    push = Signal(object)

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
                elif kind in ("connected", "latency"):
                    if kind == "connected":
                        self.server = event
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
        if old.watched:
            self.submit("watch", dict(directory=old.watched), lambda value, error: None)


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
    def __init__(self, source, directory):
        self.remote_source, self.remote_directory = source, directory
        self.remote_generation, self.remote_closed, self.remote_rows = 0, False, []
        super().__init__(directory)
        self.tr_run.hide()
        self.tr_all.hide()
        self.tr_np.hide()
        self.tr_stop.hide()
        self.more = button("读取更多组成", self.more_composition)
        self.layout().addWidget(self.more)

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
        for name in data.get("files", []):
            path = str(PurePosixPath(self.remote_directory) / name)
            groups[path], csfs[path], labels[path], origins[path] = {}, {}, {}, (None, None)
        for row in data.get("levels", []):
            path = str(PurePosixPath(self.remote_directory) / row["file"])
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
        for path, bs in groups.items():
            blocks = mix.Blocks(bs.values())
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
                   if p in self.read_data and not self.read_data[p][0]]
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


class RemoteCalculation(QWidget):
    editRequested = Signal(str)
    created = Signal(str, bool)

    def __init__(self, source, directory, run_after_load=False):
        super().__init__()
        self.source, self.directory, self.jobs = source, directory, []
        self.revision = None
        self.run_after_load = run_after_load
        self.server_type = getattr(source, "server_type", "workstation")
        self.submitting = False
        self.slurm_state = None
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(T("远程流程只读；可取出到本机编辑，再确认上传。")))
        layout.addWidget(QLabel(T("服务器类型：{kind}", kind=T("Slurm 集群") if self.server_type == "slurm" else
                                 T("工作站"))))
        bar = QHBoxLayout()
        self.run_button = button("▶ 运行全部", lambda: self.prepare_run(1))
        if self.server_type == "slurm":
            self.run_button.setText(T("提交 Slurm 作业"))
        bar.addWidget(self.run_button)
        self.run_selected = button("▶ 从选中开始", lambda: self.prepare_run(self.steps.currentRow() + 1))
        bar.addWidget(self.run_selected)
        self.stop_button = button("■ 停止", self.stop)
        bar.addWidget(self.stop_button)
        bar.addWidget(button("刷新", self.load))
        self.edit_button = button("在本机编辑…", self.edit_local)
        self.edit_button.setEnabled("workdir_write_v1" in getattr(source, "server", {}).get("capabilities", []))
        if not self.edit_button.isEnabled():
            self.edit_button.setToolTip(T("服务器端版本较旧，请用“安装 / 更新服务器版…”更新。"))
        bar.addWidget(self.edit_button)
        self.state = QLabel()
        self.state.setWordWrap(True)
        bar.addWidget(self.state, 1)
        layout.addLayout(bar)
        self.create_button = button("在此新建计算…", self.create_calculation)
        self.create_button.hide()
        layout.addWidget(self.create_button)
        split = QSplitter()
        self.steps = QListWidget()
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
        self.source.push.connect(self.push)
        self.load()

    def load(self):
        self.create_button.hide()
        def loaded(value, error):
            if error:
                return
            self.jobs, self.revision = value["jobs"], value["revision"]
            self.create_button.setVisible(not self.jobs)
            self.steps.clear()
            self.steps.addItems([f"{i + 1}. {j.get('title', '')}" for i, j in enumerate(self.jobs)])
            self.steps.setCurrentRow(0)
            if self.run_after_load:
                self.run_after_load = False
                self.prepare_run(1)
        self.source.submit("pipeline", dict(directory=self.directory), loaded)

        def watched(value, error):
            if error:
                self.run_button.setEnabled(False)
                self.run_selected.setEnabled(False)
                self.stop_button.setEnabled(False)
                self.state.setText(str(error))
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
        self.slurm_state = state if (state or {}).get("backend") == "slurm" else None
        self.clear_record.setVisible(bool(self.slurm_state and state.get("can_clear")))
        self.clear_record.setEnabled(not self.submitting)
        busy = self.submitting or state and (state.get("active", False) or state.get("status") == "running")
        self.run_button.setEnabled(not busy)
        self.run_selected.setEnabled(not busy)
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

    def prepare_run(self, start):
        if not self.jobs or not self.revision or start < 1 or self.submitting:
            return
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
                dialog = SlurmDialog(self, settings, submit=True)
                if dialog.exec() != QDialog.Accepted:
                    return
                settings = dialog.settings()
                if QMessageBox.question(self, T("提交 Slurm 作业"),
                        T("将在服务器目录 {p} 提交 Slurm 作业，是否确认？", p=self.directory),
                        QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                    return
                profiles = core.load_config().get("remote_hosts", {})
                profiles.setdefault(self.source.client.host, {})["slurm"] = settings
                core.save_config(remote_hosts=profiles)
                self.submitting = True
                self.show_state(None)
                self.source.submit("run", dict(directory=self.directory, revision=self.revision, start=start,
                                               allow_custom=custom, server_type="slurm", slurm=settings), self.started)
                return
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
            self.source.submit("run", args, self.started)
        args = dict(directory=self.directory)
        if self.server_type == "slurm":
            args["server_type"] = "slurm"
        self.source.submit("run_plan", args, planned)

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
        self.load()

    def stop(self):
        self.source.submit("stop", dict(directory=self.directory), self.started)


class RemoteWindow(QMainWindow):
    def __init__(self, source, directory, parent=None, run_after_load=False):
        super().__init__(parent)
        self.source, self.directory, self.detached = source, directory, []
        self.transfer_dialogs = []
        source.setParent(self)
        self.label = T("服务器 {h}:{p}", h=source.client.host, p=directory)
        self.setWindowTitle(self.label)
        self.resize(1350, 900)
        central = QWidget()
        layout = QVBoxLayout(central)
        label = QLabel(self.label)
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(label)
        self.tabs = QTabWidget()
        self.calc = RemoteCalculation(source, directory, run_after_load)
        self.keep_source = False
        self.calc.created.connect(lambda path, run: QTimer.singleShot(0, lambda: self.open_created(path, run)))
        if parent and hasattr(parent, "open_path"):
            def open_local(path):
                if getattr(parent, "cur_workdir", None) == path:
                    parent.calc.open_workdir(path)
                else:
                    parent.open_path(path)
                if hasattr(parent, "show_tab"):
                    parent.show_tab("calc")
                parent.show()
                parent.raise_()
            self.calc.editRequested.connect(open_local)
        self.waves = RemoteWaves(source, directory)
        self.levels = RemoteLevels(source, directory)
        for widget, title in ((self.calc, "计算"), (self.waves, "径向波函数"), (self.levels, "能级图")):
            self.tabs.addTab(widget, T(title))
        layout.addWidget(self.tabs)
        self.setCentralWidget(central)
        source.notice.connect(self.statusBar().showMessage)
        self.waves.message.connect(self.statusBar().showMessage)
        self.levels.message.connect(self.statusBar().showMessage)
        files = self.menuBar().addMenu(T("文件"))
        files.addAction(T("下载到本机…")).triggered.connect(lambda: self.open_transfer())
        files.addAction(T("从本机上传…")).triggered.connect(lambda: self.open_transfer(upload=True))
        files.addAction(T("上传本机目录…")).triggered.connect(lambda: self.open_transfer(upload=True, folder=True))
        menu = self.menuBar().addMenu(T("窗口"))
        menu.addAction(T("重新连接")).triggered.connect(source.reconnect)
        menu.addAction(T("在新窗口中打开径向波函数")).triggered.connect(lambda: self.open_view("wfn"))
        menu.addAction(T("在新窗口中打开能级图")).triggered.connect(lambda: self.open_view("levels"))

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
        dialog = TransferDialog(self.source, self.directory, local, names, self)
        self.transfer_dialogs.append(dialog)
        dialog.finished.connect(lambda result: self.transfer_dialogs.remove(dialog))
        dialog.finished.connect(dialog.deleteLater)
        dialog.show()

    def open_created(self, directory, run):
        parent = self.parentWidget()
        win = RemoteWindow(self.source, directory, parent, run_after_load=run)
        self.created_window = win
        if parent and hasattr(parent, "remote_windows"):
            parent.remote_windows.append(win)
        self.keep_source = True
        win.show()
        self.close()

    def open_view(self, kind):
        win = QMainWindow(self)
        win.setWindowTitle(self.label)
        panel = RemoteWaves(self.source, self.directory) if kind == "wfn" else RemoteLevels(self.source, self.directory)
        win.setCentralWidget(panel)
        win.resize(1250, 800)
        win.show()
        self.detached.append((win, panel))

    def closeEvent(self, event):
        if getattr(self.calc, "import_dialog", None) is not None:
            self.calc.import_dialog.shutdown()
        for dialog in list(self.transfer_dialogs):
            dialog.shutdown()
        for win, panel in self.detached:
            panel.shutdown()
            win.close()
        self.waves.shutdown()
        self.levels.shutdown()
        if not self.keep_source:
            self.source.close()
        event.accept()


def connect_window(parent):
    dialog = ConnectDialog(parent)
    if dialog.exec() != QDialog.Accepted:
        return None
    win = RemoteWindow(dialog.source, dialog.directory, parent, run_after_load=dialog.run_after_load)
    win.show()
    return win
