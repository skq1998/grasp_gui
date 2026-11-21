"""桌面能级页的 NIST 对照，联网只由获取按钮触发。"""

import csv
import queue
import threading

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
                               QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout)

from grasp_core import T
import grasp_nist as nist
from gui_common import button
from level_panel import LevelDiagram, PARITY_COLORS

FIELDS = ("文件", "J", "宇称", "主要成分", "LSJ 标记", "计算能量 (cm⁻¹)", "NIST 组态",
          "NIST 谱项", "NIST 能量 (cm⁻¹)", "差值 (cm⁻¹)", "相对误差", "对应状态")
STATUS = {"automatic": "唯一匹配", "manual": "手动匹配", "ambiguous": "待核对", "unmatched": "无匹配"}


def level_label(row):
    configuration = " ".join(f"{shell}^{occ}" for shell, occ in row.get("full") or ())
    return row.get("lsj_label") or configuration or f"#{row['pos']}"


def calc_label(row):
    parity = "+" if row["parity"] > 0 else "−"
    return f"{row['file']}  {row['J']}{parity}  {level_label(row)}"


def nist_label(level):
    value = f"{level.reported} cm⁻¹"
    return " · ".join(text for text in (level.configuration, level.term, "J=" + level.j, value) if text)


def number(value):
    return "—" if value is None else f"{value:.12g}"


class NistDialog(QDialog):
    def __init__(self, parent, calculated, ion, workdir):
        super().__init__(parent)
        self.calculated, self.workdir = calculated, workdir
        self.pairs = {}
        self.pairing_error = None
        self.current = None
        self.rows = []
        self.events = queue.Queue()
        self.setWindowTitle(T("NIST ASD 能级对照"))
        self.resize(1130, 740)
        layout = QVBoxLayout(self)
        tools = QHBoxLayout()
        tools.addWidget(QLabel(T("NIST 光谱")))
        self.ion = QLineEdit(ion)
        self.ion.setPlaceholderText("Li I / Fe XV")
        self.ion.editingFinished.connect(self.show_cache)
        tools.addWidget(self.ion, 1)
        self.fetch_button = button("从 NIST 获取", self.request_levels)
        tools.addWidget(self.fetch_button)
        self.refresh = QCheckBox(T("刷新缓存"))
        tools.addWidget(self.refresh)
        self.import_button = button("导入 NIST 文件…", self.import_levels)
        tools.addWidget(self.import_button)
        layout.addLayout(tools)
        self.info = QLabel()
        self.info.setWordWrap(True)
        self.info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.info)
        zero = QHBoxLayout()
        zero.addWidget(QLabel(T("计算能量零点")))
        self.zero = QComboBox()
        self.zero.addItem(T("统一取最低能级"), None)
        for row in self.calculated:
            self.zero.addItem(calc_label(row), row["id"])
        self.zero.currentIndexChanged.connect(self.update_comparison)
        zero.addWidget(self.zero, 1)
        layout.addLayout(zero)
        self.tabs = QTabWidget()
        self.diagram = LevelDiagram()
        self.tabs.addTab(self.diagram, T("能级图"))
        self.table = QTableWidget(0, len(FIELDS))
        self.table.setHorizontalHeaderLabels([T(name) for name in FIELDS])
        self.table.verticalHeader().hide()
        self.table.resizeColumnsToContents()
        for column in (1, 2):
            self.table.setColumnWidth(column, max(48, self.table.columnWidth(column)))
        for column in (0, 3, 4, 5, 6, 8, 9, 10, 11):
            self.table.setColumnWidth(column, max(116, self.table.columnWidth(column)))
        self.table.currentCellChanged.connect(self.pair_options)
        self.tabs.addTab(self.table, T("能级对照表"))
        layout.addWidget(self.tabs, 1)
        pairing = QHBoxLayout()
        self.choices = QComboBox()
        pairing.addWidget(QLabel(T("所选行的实验能级")))
        pairing.addWidget(self.choices, 1)
        self.apply_pair = button("应用配对", self.set_pair)
        pairing.addWidget(self.apply_pair)
        layout.addLayout(pairing)
        bar = QHBoxLayout()
        bar.addStretch()
        self.export_button = button("导出对照 CSV…", self.export)
        bar.addWidget(self.export_button)
        bar.addWidget(button("关闭", self.reject))
        layout.addLayout(bar)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(80)
        self.show_cache()

    def show_cache(self):
        try:
            value = nist.load_cache(self.ion.text())
        except ValueError:
            value = None
        self.show_data(value)

    def show_data(self, value):
        self.current = value
        self.pairs = {}
        self.pairing_error = None
        if value:
            metadata, _ = value
            self.ion.setText(metadata["ion"])
            text = T("NIST ASD · {date} 导入", date=metadata["date"]) if metadata["imported"] else T(
                "NIST ASD · 下载于 {date}", date=metadata["date"])
            if metadata.get("warning"):
                text += "\n" + metadata["warning"]
            self.info.setText(text + "\n" + nist.CITATION)
            try:
                self.pairs = nist.load_matches(self.workdir).get(metadata["ion"], {})
            except (OSError, ValueError) as error:
                self.pairing_error = str(error)
                self.info.setText(self.info.text() + "\n" + self.pairing_error)
        else:
            self.info.setText(T("输入光谱后，可从 NIST 获取或导入 CSV / 制表符文件。"))
        self.update_comparison()

    def request_levels(self):
        try:
            ion = nist.normalize_spectrum(self.ion.text())
        except ValueError as error:
            self.info.setText(str(error))
            return
        if not self.refresh.isChecked():
            cached = nist.load_cache(ion)
            if cached:
                self.show_data(cached)
                return
        self.fetch_button.setEnabled(False)
        self.import_button.setEnabled(False)
        self.ion.setEnabled(False)
        self.info.setText(T("正在从 NIST 获取 {ion}…", ion=ion))

        def worker():
            try:
                value = nist.save_cache(ion, nist.fetch(ion))
            except (OSError, ValueError) as error:
                self.events.put((None, str(error)))
            else:
                self.events.put((value, None))
        threading.Thread(target=worker, daemon=True).start()

    def poll(self):
        try:
            value, error = self.events.get_nowait()
        except queue.Empty:
            return
        self.fetch_button.setEnabled(True)
        self.import_button.setEnabled(True)
        self.ion.setEnabled(True)
        if error:
            self.info.setText(T("无法从 NIST 获取数据：{e}。请检查代理设置或改用导入。", e=error))
        else:
            self.show_data(value)

    def import_levels(self):
        try:
            ion = nist.normalize_spectrum(self.ion.text())
        except ValueError as error:
            self.info.setText(str(error))
            return
        path, _ = QFileDialog.getOpenFileName(self, T("导入 NIST 文件…"), "", "CSV / TSV (*.csv *.tsv *.txt)")
        if path:
            try:
                raw, _ = nist.read_data(path)
                self.show_data(nist.save_cache(ion, raw, imported=True))
            except (OSError, ValueError) as error:
                self.info.setText(str(error))

    def update_comparison(self):
        exp = self.current[1] if self.current else []
        self.rows = nist.compare(self.calculated, exp, manual=nist.match_overrides(self.calculated, self.pairs),
                                 reference=self.zero.currentData()) if self.current else []
        self.export_button.setEnabled(bool(self.rows))
        position = self.table.currentRow()
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.rows))
        for i, row in enumerate(self.rows):
            for j, value in enumerate(self.fields(row)):
                cell = QTableWidgetItem(value)
                cell.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
                if j in (3, 4):
                    cell.setToolTip(value)
                if j == 8 and row["experimental"]:
                    measured = row["experimental"]
                    cell.setToolTip(nist_label(measured))
                self.table.setItem(i, j, cell)
        if self.rows:
            self.table.setCurrentCell(max(0, min(position, len(self.rows) - 1)), 0)
        self.table.blockSignals(False)
        self.pair_options()
        self.draw()

    @staticmethod
    def fields(row):
        calculated, lvl = row["calculated"], row["experimental"]
        fraction = row["relative_error"]
        return (calculated["file"], calculated["J"], "+" if calculated["parity"] > 0 else "−",
                calculated.get("jj") or level_label(calculated), calculated.get("lsj_label") or "—",
                number(row["calculated_cm"]),
                lvl.configuration if lvl else "—", lvl.term if lvl else "—",
                number(row["experimental_cm"]), number(row["delta_cm"]),
                f"{fraction:.5%}" if fraction is not None else "—", T(STATUS[row["status"]]))

    def pair_options(self, *_):
        self.choices.clear()
        position = self.table.currentRow()
        valid = 0 <= position < len(self.rows)
        self.apply_pair.setEnabled(valid and not self.pairing_error)
        if not valid:
            return
        row = self.rows[position]
        self.choices.addItem(T("自动匹配"), None)
        for level in row["candidates"]:
            self.choices.addItem(nist_label(level), level.key)
        current = self.pairs.get(row["calculated"]["id"])
        if current:
            self.choices.setCurrentIndex(max(0, self.choices.findData(current["experimental"])))

    def set_pair(self):
        row = self.rows[self.table.currentRow()]["calculated"]
        try:
            self.pairs = nist.save_match(self.workdir, self.current[0]["ion"], row, self.choices.currentData())
        except (OSError, ValueError) as error:
            self.info.setText(str(error))
            return
        self.update_comparison()

    def draw(self):
        computed, experimental = [], []
        for row in self.rows:
            calc = row["calculated"]
            computed.append(dict(key=("calc", calc["id"]), y=row["calculated_cm"],
                                 color=PARITY_COLORS[calc["parity"]], info=calc_label(calc), label=""))
        if computed and self.current:
            levels = self.current[1]
            base = min((level.energy for level in levels if level.energy is not None), default=0)
            experimental = [dict(key=("nist", lv.key), y=lv.energy - base, color=QColor("#147b61"),
                                 info=nist_label(lv), label="") for lv in levels if lv.energy is not None]
        self.diagram.set_columns([dict(file=T("计算能级"), title="", levels=computed),
                                  dict(file="NIST ASD", title="", levels=experimental)], "cm⁻¹")
        if computed and experimental:
            # 默认范围聚焦当前计算态和已配对的实验态。
            values = [v["y"] for v in computed]
            values.extend(row["experimental_cm"] for row in self.rows if row["experimental_cm"] is not None)
            lo, hi = min(values), max(values)
            pad = 0.06 * (hi - lo) or 1
            self.diagram.yr = (lo - pad, hi + pad)
            self.diagram.update()

    def export(self):
        path, _ = QFileDialog.getSaveFileName(self, T("导出对照 CSV…"), "nist-levels.csv", "CSV (*.csv)")
        if not path:
            return
        if not path.lower().endswith(".csv"):
            path += ".csv"

        def cell(text):
            # CSV 中保留普通负数；数据或文件名以公式开头时按字面输出。
            if len(text) > 1 and text[0] in "=+-@" and nist._number(text.rstrip("%")) is None:
                return "'" + text
            return text

        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as stream:
                writer = csv.writer(stream)
                writer.writerow([T(label) for label in FIELDS])
                writer.writerows([cell(text) for text in self.fields(row)] for row in self.rows)
        except OSError as error:
            self.info.setText(str(error))
