"""逐层收敛面板，复用结果读取、状态判据及文本报告。"""

import os
from pathlib import Path
import queue

from PySide6.QtCore import QItemSelectionModel, QTimer, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog,
                               QFormLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMessageBox,
                               QSplitter, QTableWidgetItem, QVBoxLayout, QWidget)

from convergence_plot import ConvergencePlot
import grasp_converge as converge
import grasp_core as core
from grasp_index import ReadWorker
from grasp_mix import HARTREE_CM
from grasp_summary import result_order
from gui_common import Panel, button
from level_panel import UNITS, table
from wfn_panel import fmt

T = core.T
STATUS_COLORS = {"converged": "#73b791", "converging": "#91b9e8", "unconverged": "#e2ab64",
                 "changed": "#d5a2d7", "unmatched": "#c1c1c1"}


def calculate(directory, files, jobs, layers, threshold, reference, *, cancel, progress):
    if layers is None:
        layers = converge.read_layers(directory, files, jobs=jobs, cancel=cancel)
    return layers, converge.analyze(layers, threshold=threshold, reference=reference)


def level_name(row):
    return row["j"] + ("+" if row["parity"] > 0 else "−"), row["lsj"] or row["primary"][0] or "—"


class ConvergencePanel(Panel):
    KIND, TITLE = "convergence", "收敛"

    def __init__(self, workdir):
        super().__init__()
        self.worker = ReadWorker()
        self.token = 0
        self.loading = False
        self.root = None
        self.jobs = []
        self.layers = self.result = None
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(50)
        self.threshold_cm = converge.DEFAULT_THRESHOLD

        layout = QVBoxLayout(self)
        top = self.dir_bar(workdir)
        self.copy_button = button("复制为文本", self.copy_report)
        self.csv_button = button("导出 CSV…", self.export_csv)
        top.addWidget(self.copy_button)
        top.addWidget(self.csv_button)
        top.addStretch()
        layout.addLayout(top)

        split = QSplitter()
        controls = QWidget()
        sidebar = QVBoxLayout(controls)
        fields = QFormLayout()
        self.group = QComboBox()
        self.group.currentIndexChanged.connect(self.group_changed)
        fields.addRow(T("态组"), self.group)
        self.reference = QComboBox()
        self.reference.currentIndexChanged.connect(self.request)
        fields.addRow(T("参考能级"), self.reference)
        self.threshold = QDoubleSpinBox()
        self.threshold.setKeyboardTracking(False)
        self.threshold.valueChanged.connect(self.threshold_changed)
        fields.addRow(T("收敛阈值"), self.threshold)
        sidebar.addLayout(fields)
        sidebar.addWidget(QLabel(T("轨道层")))
        self.files = QListWidget()
        self.files.itemChanged.connect(self.files_checked)
        sidebar.addWidget(self.files, 1)
        order = QHBoxLayout()
        order.addWidget(button("上移", lambda: self.move_layer(-1)))
        order.addWidget(button("下移", lambda: self.move_layer(1)))
        sidebar.addLayout(order)
        sidebar.addWidget(button("选择层文件…", self.pick_files))
        self.only_unconverged = QCheckBox(T("只显示未收敛的态"))
        self.only_unconverged.toggled.connect(self.render)
        sidebar.addWidget(self.only_unconverged)
        self.selection_only = QCheckBox(T("只绘制选中的态"))
        self.selection_only.toggled.connect(self.update_plot)
        sidebar.addWidget(self.selection_only)
        split.addWidget(controls)

        display = QWidget()
        view = QVBoxLayout(display)
        self.view = QComboBox()
        self.view.addItems(["", ""])
        self.view.currentIndexChanged.connect(self.render)
        view.addWidget(self.view)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        view.addWidget(self.summary)
        self.results = table([])
        self.results.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.results.horizontalHeader().setResizeContentsPrecision(80)
        self.results.itemSelectionChanged.connect(self.update_plot)
        view.addWidget(self.results, 2)
        self.plot = ConvergencePlot()
        view.addWidget(self.plot, 1)
        split.addWidget(display)
        split.setStretchFactor(1, 1)
        split.setSizes([290, 870])
        layout.addWidget(split, 1)
        self.set_unit(core.load_config().get("level_unit", 0))
        self.scan()

    def set_unit(self, value):
        position = min(max(int(value), 0), len(UNITS) - 1)
        _, self.unit, factor, self.energy_format = UNITS[position]
        self.units_per_cm = factor / HARTREE_CM
        precision = (8, 4, 10)[position]
        self.threshold.blockSignals(True)
        self.threshold.setDecimals(precision)
        self.threshold.setRange(10 ** -precision, 1e9)
        self.threshold.setSuffix(" " + self.unit)
        self.threshold.setValue(self.threshold_cm * self.units_per_cm)
        self.threshold.blockSignals(False)
        self.view.setItemText(0, T("激发能与增量 ({unit})", unit=self.unit))
        self.view.setItemText(1, T("精细结构分裂及增量 ({unit})", unit=self.unit))
        self.render()

    def scan(self):
        directory = os.path.abspath(self.dir.text())
        same = directory == self.root
        selected = self.group.currentData() if same else None
        self.root = directory
        self.jobs = (core.load_session(directory) or {}).get("pipeline", [])
        groups = dict.fromkeys(row["group"] for row in result_order(self.jobs)
                               if row["file"].endswith((".m", ".cm")) and Path(directory, row["file"]).is_file())
        self.group.blockSignals(True)
        self.group.clear()
        for group in groups or (None,):
            self.group.addItem(group or T("不分组"), group)
        if selected in groups:
            self.group.setCurrentIndex(self.group.findData(selected))
        self.group.blockSignals(False)
        self.populate_layers(keep=same)

    def group_changed(self):
        self.populate_layers(keep=False)

    def populate_layers(self, *, keep):
        existing = {self.files.item(i).data(Qt.UserRole): self.files.item(i).checkState()
                    for i in range(self.files.count())} if keep else {}
        ordered = converge.ordered_files(self.root, self.jobs, self.group.currentData())
        paths = [str(Path(self.root, name)) for name in ordered]
        self.files.blockSignals(True)
        self.files.clear()
        for path in dict.fromkeys([*existing, *paths]):
            if Path(path).is_file():
                self.add_layer(path, existing.get(path, Qt.Checked))
        self.files.blockSignals(False)
        self.files_checked()

    def add_layer(self, path, checked=Qt.Checked):
        item = QListWidgetItem(Path(path).name)
        item.setData(Qt.UserRole, path)
        item.setToolTip(path)
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        item.setCheckState(checked)
        self.files.addItem(item)

    def add_extra_file(self, path):
        if Path(path).suffix in (".m", ".cm") and Path(path).is_file():
            self.add_layer(path)

    def pick_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, T("选择层文件…"), self.root, "*.m *.cm")
        if not paths:
            return
        if any(Path(path).resolve().parent != Path(self.root).resolve() for path in paths):
            QMessageBox.warning(self, T("收敛"), T("请选择工作目录中的混合系数文件。"))
            return
        existing = {self.files.item(i).data(Qt.UserRole): self.files.item(i) for i in range(self.files.count())}
        self.files.blockSignals(True)
        for path in paths:
            if path in existing:
                existing[path].setCheckState(Qt.Checked)
            else:
                self.add_extra_file(path)
        self.files.blockSignals(False)
        self.files_checked()

    def move_layer(self, step):
        row = self.files.currentRow()
        if row < 0 or not 0 <= row + step < self.files.count():
            return
        self.files.blockSignals(True)
        self.files.insertItem(row + step, self.files.takeItem(row))
        self.files.setCurrentRow(row + step)
        self.files.blockSignals(False)
        self.files_checked()

    def files_checked(self):
        self.layers = None
        self.reference.blockSignals(True)
        self.reference.clear()
        self.reference.addItem(T("各层最低能级"), None)
        self.reference.blockSignals(False)
        self.request()

    def threshold_changed(self):
        self.threshold_cm = self.threshold.value() / self.units_per_cm
        self.request()

    def request(self):
        files = [os.path.relpath(path, self.root) for path in self.checked_paths()]
        self.token += 1
        self.worker.cancel.set()
        self.result = None
        self.loading = bool(files)
        self.summary.setText(T("正在读取收敛数据…") if files else T("请选择 1–50 个不同的轨道层文件。"))
        self.render()
        if files:
            self.worker.submit(self.token, calculate, self.root, files, self.jobs, self.layers, self.threshold_cm,
                               self.reference.currentData())

    def poll(self):
        try:
            while True:
                kind, token, data = self.worker.events.get_nowait()
                if token == self.token:
                    self.receive(kind, data)
        except queue.Empty:
            pass

    def receive(self, kind, data):
        if kind == "progress":
            return
        self.loading = False
        if kind == "error":
            self.summary.setText(str(data))
            return
        self.layers, self.result = data
        reference = self.result["reference"]
        self.reference.blockSignals(True)
        self.reference.clear()
        self.reference.addItem(T("各层最低能级"), None)
        for row in self.result["rows"]:
            self.reference.addItem(" · ".join(level_name(row)), row["id"])
        self.reference.setCurrentIndex(max(0, self.reference.findData(reference)))
        self.reference.blockSignals(False)
        self.summary.setText(T("受数据或行数上限影响，部分结果未列出。") if self.result["truncated"] else "")
        self.render()

    def displayed_result(self):
        if self.result is None or not self.only_unconverged.isChecked():
            return self.result
        return dict(self.result, rows=[row for row in self.result["rows"] if row["status"] != "converged"])

    def render(self):
        result = self.displayed_result()
        splitting = self.view.currentIndex() == 1
        selected = self.selected_ids()
        self.copy_button.setEnabled(result is not None)
        self.csv_button.setEnabled(result is not None)
        self.results.blockSignals(True)
        self.results.clear()
        self.results.setRowCount(0)
        self.plot.setVisible(not splitting)
        if result is not None:
            self.render_table(result, splitting)
            selection = self.results.selectionModel()
            for number, row in enumerate(result["rows"] if not splitting else []):
                if row["id"] in selected:
                    index = self.results.model().index(number, 0)
                    selection.select(index, QItemSelectionModel.Select | QItemSelectionModel.Rows)
        self.results.blockSignals(False)
        self.update_plot()

    def render_table(self, result, splitting):
        layers = result["layers"]
        columns = [T("组态"), T("谱项"), "J^π"] if splitting else ["J^π", T("主要成分")]
        start = len(columns)
        columns += [f'{layer["file"]}\n{self.unit}' for layer in layers]
        if not splitting:
            columns += [T("总能量增量") + "\n" + self.unit, "r", T("剩余量") + "\n" + self.unit,
                        T("纯度 (%)"), T("状态")]
        self.results.setColumnCount(len(columns))
        self.results.setHorizontalHeaderLabels(columns)
        for i, layer in enumerate(layers, start):
            self.results.horizontalHeaderItem(i).setToolTip(self.log_tooltip(layer["scf"]))
        rows = result["splittings"] if splitting else result["rows"]
        self.results.setRowCount(len(rows))
        for i, row in enumerate(rows):
            if splitting:
                self.split_row(i, row)
            else:
                self.level_row(i, row)
        self.results.resizeColumnsToContents()

    def energy(self, value):
        return "—" if value is None else self.energy_format.format(value * self.units_per_cm)

    def level_row(self, number, row):
        points = row["points"]
        last = points[-1]
        cells = list(level_name(row))
        cells.extend(f'{self.energy(p["excitation_cm"])} / {self.energy(p["delta_cm"])}' for p in points)
        status = T(converge.STATUSES[row["status"]])
        if row["ambiguous"]:
            status += " · " + T("对应不唯一")
        cells.extend((self.energy(last["total_delta_cm"]), self.number(last["ratio"]),
                      self.energy(last["remainder_cm"]), self.number(last["purity_percent"]), status))
        for column, text in enumerate(cells):
            item = QTableWidgetItem(text)
            if column == 0:
                item.setData(Qt.UserRole, row["id"])
            if 2 <= column < 2 + len(points):
                p = points[column - 2]
                primary = " · ".join(p["primary"]) if p["primary"] else "—"
                item.setToolTip(f'{primary}\n{T("纯度 (%)")} {self.number(p["purity_percent"])}')
            if column == len(cells) - 1:
                item.setBackground(QColor(STATUS_COLORS[row["status"]]))
                item.setForeground(QColor("#1b2026"))
                if last["reason"]:
                    item.setToolTip(T(converge.REASONS[last["reason"]]))
            self.results.setItem(number, column, item)

    def split_row(self, number, row):
        parity = "+" if row["parity"] > 0 else "−"
        config = " ".join(f"{orbital}^{occ}" for orbital, occ in row["configuration"])
        cells = [config, row["term"], f'{row["lower_j"]}{parity} → {row["higher_j"]}{parity}']
        cells.extend(f'{self.energy(value)} / {self.energy(point["delta_cm"])}'
                     for value, point in zip(row["splitting_cm"], row["points"]))
        for column, text in enumerate(cells):
            self.results.setItem(number, column, QTableWidgetItem(text))

    @staticmethod
    def number(value):
        return "—" if value is None else fmt(value)

    @staticmethod
    def log_tooltip(scf):
        if scf.get("missing"):
            return T("无日志")
        if "error" in scf:
            return str(scf["error"])
        state = {True: "converged", False: "unconverged", None: "unknown"}[scf.get("converged")]
        details = [f'{scf.get("file", "")} · {T(converge.SCF_STATUS[state])}',
                   f'{T("总迭代次数")} {scf.get("iterations", "—")}']
        for sample in scf.get("last_iterations", []):
            parts = [f'{T("迭代序号")} {sample["iteration"]}']
            for key, title in (("delta_weighted_energy_hartree", "加权 SCF 能量增量 (Hartree)"),
                               ("delta_optimized_energy_hartree", "单能级 SCF 能量增量 (Hartree)")):
                if sample.get(key) is not None:
                    parts.append(f'{T(title)} {fmt(sample[key])}')
            details.append(" · ".join(parts))
        return "\n".join(details)

    def update_plot(self):
        selected = self.selected_ids() if self.selection_only.isChecked() else None
        self.plot.set_result(self.displayed_result(), selected=selected, unit=self.unit, units_per_cm=self.units_per_cm)

    def selected_ids(self):
        return {self.results.item(index.row(), 0).data(Qt.UserRole)
                for index in self.results.selectionModel().selectedRows()}

    def copy_report(self):
        result = self.displayed_result()
        if result is not None:
            QApplication.clipboard().setText(converge.markdown(result))

    def export_csv(self):
        result = self.displayed_result()
        if result is None:
            return
        default = str(Path(self.root, "convergence.csv"))
        path, _ = QFileDialog.getSaveFileName(self, T("导出 CSV…"), default, "CSV (*.csv)")
        if path:
            try:
                Path(path).write_bytes(converge.csv_text(result).encode("utf-8-sig"))
            except OSError as error:
                QMessageBox.warning(self, T("收敛"), str(error))

    def shutdown(self):
        self.timer.stop()
        self.worker.close()
