"""rcsfgenerate 的离线壳层模型助手。"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFormLayout, QHBoxLayout, QHeaderView,
                               QLabel, QLineEdit, QListWidget, QPlainTextEdit, QSpinBox, QSplitter, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from grasp_core import T
import grasp_shells as shells
from gui_common import button, fixed_font


def occupation_text(occupations):
    return " ".join(f"{shells.label(shell)}^{count}" for shell, count in sorted(occupations.items()))


class ShellModelDialog(QDialog):
    def __init__(self, parent, step, original, element="Li"):
        super().__init__(parent)
        self.step, self.original, self.result_fields = step, original, None
        self.references = []
        self.source_shell = None
        self.updating = False
        self.setWindowTitle(T("从壳层模型构造"))
        self.resize(1030, 750)
        layout = QVBoxLayout(self)

        species = QHBoxLayout()
        species.addWidget(QLabel(T("元素 (符号或 Z)")))
        self.element = QLineEdit(str(element))
        species.addWidget(self.element, 1)
        self.quantity = QComboBox()
        self.quantity.addItem(T("离化度"), "charge")
        self.quantity.addItem(T("电子数"), "electrons")
        species.addWidget(self.quantity)
        self.value = QSpinBox()
        self.value.setRange(0, len(shells.ELEMENTS))
        species.addWidget(self.value)
        self.load_button = button("载入基态", self.load_ground)
        species.addWidget(self.load_button)
        layout.addLayout(species)
        self.quantity.currentIndexChanged.connect(self.switch_quantity)
        self.element.returnPressed.connect(self.load_ground)
        self.origin = QLabel()
        self.origin.setWordWrap(True)
        layout.addWidget(self.origin)

        split = QSplitter()
        left = QWidget()
        display = QVBoxLayout(left)
        options = QHBoxLayout()
        options.addWidget(QLabel(T("显示至主量子数 n")))
        self.max_n = QSpinBox()
        self.max_n.setRange(1, shells.MAX_N)
        options.addWidget(self.max_n)
        self.relativistic = QCheckBox(T("显示相对论子壳层"))
        options.addWidget(self.relativistic)
        options.addStretch()
        display.addLayout(options)
        self.instructions = QLabel()
        self.instructions.setWordWrap(True)
        display.addWidget(self.instructions)
        self.shells = QTableWidget(0, 4)
        self.shells.setHorizontalHeaderLabels([T("壳层"), T("占据数"), T("容量"), T("激发标记")])
        self.shells.horizontalHeaderItem(3).setToolTip(T("i 表示不激发，* 表示可激发，数值表示最少占据数。"))
        self.shells.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.shells.verticalHeader().hide()
        self.shells.cellClicked.connect(self.select_shell)
        display.addWidget(self.shells, 1)
        self.max_n.valueChanged.connect(self.draw_shells)
        self.relativistic.toggled.connect(self.draw_shells)
        split.addWidget(left)

        right = QWidget()
        controls = QVBoxLayout(right)
        controls.addWidget(QLabel(T("参考组态")))
        self.reference_list = QListWidget()
        self.reference_list.currentRowChanged.connect(self.change_reference)
        controls.addWidget(self.reference_list, 1)
        bar = QHBoxLayout()
        self.copy_ref = button("复制参考", self.copy_reference)
        self.remove_ref = button("删除参考", self.remove_reference)
        bar.addWidget(self.copy_ref)
        bar.addWidget(self.remove_ref)
        bar.addStretch()
        controls.addLayout(bar)
        self.parity_note = QLabel(T("检测到混合宇称，将按宇称写成独立列表。"))
        self.parity_note.setWordWrap(True)
        controls.addWidget(self.parity_note)

        form = QFormLayout()
        core_row = QHBoxLayout()
        self.core = QComboBox()
        for code, (name, _) in enumerate(shells.CORES):
            self.core.addItem(name or T("无芯"), str(code))
        core_row.addWidget(self.core, 1)
        self.suggest_core_button = button("建议闭壳芯", self.suggest_core)
        core_row.addWidget(self.suggest_core_button)
        form.addRow(T("闭壳芯"), core_row)
        active_row = QHBoxLayout()
        self.active = QLineEdit()
        self.active.setPlaceholderText("5s,4p,3d")
        active_row.addWidget(self.active, 1)
        active_row.addWidget(button("按参考组态设置", self.suggest_active))
        form.addRow(T("活跃轨道集"), active_row)
        bounds = QHBoxLayout()
        self.jmin, self.jmax = QSpinBox(), QSpinBox()
        for field in (self.jmin, self.jmax):
            field.setRange(0, 999)
            field.valueChanged.connect(self.update_preview)
            bounds.addWidget(field)
        form.addRow(T("2J 范围 (最小值、最大值)"), bounds)
        self.excitations = QSpinBox()
        self.excitations.setRange(-118, 118)
        form.addRow(T("激发数"), self.excitations)
        self.mode = QComboBox()
        self.mode.addItem(T("替换现有列表"), "replace")
        if any(item["configs"].strip() for item in original["lists"]):
            self.mode.addItem(T("追加到现有列表"), "append")
        form.addRow(T("填回方式"), self.mode)
        controls.addLayout(form)
        self.mode.currentIndexChanged.connect(self.change_mode)
        self.core.currentIndexChanged.connect(self.core_changed)
        self.active.textChanged.connect(self.update_preview)
        self.excitations.valueChanged.connect(self.update_preview)
        split.addWidget(right)
        split.setSizes([570, 420])
        layout.addWidget(split, 4)
        layout.addWidget(QLabel(T("输入预览")))
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setFont(fixed_font())
        layout.addWidget(self.preview, 2)
        self.error = QLabel()
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        buttons = QHBoxLayout()
        buttons.addStretch()
        self.apply_button = button("填回程序页", self.accept)
        buttons.addWidget(self.apply_button)
        buttons.addWidget(button("取消", self.reject))
        layout.addLayout(buttons)
        try:
            shells.element(element)
        except ValueError:
            self.element.setText("Li")
        self.load_ground()

    def switch_quantity(self):
        if hasattr(self, "z"):
            count = self.electrons if self.quantity.currentData() == "electrons" else self.z - self.electrons
            self.value.setValue(count)

    def load_ground(self):
        try:
            z = shells.element(self.element.text())
            value = self.value.value()
            charge = z - value if self.quantity.currentData() == "electrons" else value
            ground = shells.ground(z, charge)
        except ValueError as error:
            self.error.setText(str(error))
            return
        self.updating = True
        self.z, self.electrons = z, z - charge
        symbol = shells.ELEMENTS[z - 1]
        self.element.setText(symbol)
        occ = dict(ground.occupations)
        self.references = [(occ, {})]
        self.origin.setText(T("{symbol} · {electrons} 个电子 · 基态 {config}",
                              symbol=symbol, electrons=self.electrons, config=occupation_text(occ) or "—") + "\n" +
                            self.ground_source(ground))
        self.max_n.setValue(min(shells.MAX_N, 1 + max((n for n, _ in occ), default=1)))
        self.jmin.setValue(self.electrons % 2)
        maximum = sum(sum(sorted((2 * m + s for m in range(-l, l + 1) for s in (-1, 1)), reverse=True)[:count])
                      for (_, l), count in occ.items())
        self.jmax.setValue(maximum)
        self.suggest_core()
        self.suggest_active()
        self.updating = False
        self.refresh_references(0)

    def ground_source(self, ground):
        metadata, _ = shells.snapshot()
        self.origin.setToolTip(metadata["citation"])
        if not ground.occupations:
            return T("全电离离子没有束缚电子，不能生成 CSF。")
        if ground.estimated:
            return T("推算组态，按 n+l、n 顺序填充。电离能和基态项无数据。")
        return T("NIST ASD · 下载于 {date} · 基态项 {term} · 电离能 {energy}",
                 date=metadata["retrieved"], term=shells.format_term(ground.term) or "—",
                 energy=shells.format_ionization(ground.ionization_ev, ground.uncertainty_ev))

    def refresh_references(self, position):
        self.reference_list.blockSignals(True)
        self.reference_list.clear()
        self.reference_list.addItems([f"{shells.parity(occ)}   {occupation_text(occ) or '—'}"
                                      for occ, _ in self.references])
        self.reference_list.setCurrentRow(position)
        self.reference_list.blockSignals(False)
        self.remove_ref.setEnabled(len(self.references) > 1)
        self.parity_note.setVisible(len({shells.parity(occ) for occ, _ in self.references}) > 1)
        self.change_reference()

    def change_reference(self):
        self.source_shell = None
        self.draw_shells()
        self.update_preview()

    def copy_reference(self):
        index = self.reference_list.currentRow()
        occ, marks = self.references[index]
        self.references.insert(index + 1, (dict(occ), dict(marks)))
        self.refresh_references(index + 1)

    def remove_reference(self):
        index = self.reference_list.currentRow()
        if len(self.references) > 1:
            del self.references[index]
            self.refresh_references(min(index, len(self.references) - 1))

    def draw_shells(self, *_):
        index = self.reference_list.currentRow()
        if self.updating or not 0 <= index < len(self.references):
            return
        occ, marks = self.references[index]
        relative = self.relativistic.isChecked()
        self.instructions.setText(T("相对子壳层按较小 j 先填充，只作参考。") if relative else
                                  T("先点有电子的来源壳层，再点有空位的目标壳层，一次移动一个电子。"))
        self.shells.clearContents()
        data = []
        for shell in shells.orbitals(self.max_n.value()):
            count = occ.get(shell, 0)
            view = (shells.relativistic(shell, count) if relative else
                    [(shells.label(shell), count, shells.capacity(shell))])
            data.extend((shell, name, used, limit) for name, used, limit in view)
        self.shells.setRowCount(len(data))
        self.shells.setColumnHidden(3, relative)
        core = shells.closed_core(self.core.currentData())
        for row, (shell, name, count, limit) in enumerate(data):
            for column, text in enumerate((name, str(count), str(limit))):
                item = QTableWidgetItem(text)
                item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                item.setData(Qt.UserRole, shell)
                self.shells.setItem(row, column, item)
            if not relative and count and shell not in core:
                marker = QComboBox()
                options = ["i", "*"] + [str(n) for n in range(count + 1)]
                current = marks.get(shell, "i")
                if current not in options:
                    options.append(current)         # 移走电子后，让用户重新选合法的最小占据。
                marker.addItems(options)
                marker.setCurrentText(current)
                marker.currentTextChanged.connect(lambda value, key=shell: self.set_marker(key, value))
                self.shells.setCellWidget(row, 3, marker)

    def select_shell(self, row, column):
        if self.relativistic.isChecked() or column == 3:
            return
        index = self.reference_list.currentRow()
        occ, marks = self.references[index]
        shell = tuple(self.shells.item(row, 0).data(Qt.UserRole))
        if self.source_shell is None:
            if occ.get(shell):
                self.source_shell = shell
                self.instructions.setText(T("已选择 {shell}，请选择目标壳层。", shell=shells.label(shell)))
            return
        try:
            changed = shells.transfer(occ, self.source_shell, shell)
        except ValueError as error:
            self.instructions.setText(str(error))
            self.source_shell = None
            return
        self.references[index] = changed, {key: value for key, value in marks.items() if key in changed}
        self.refresh_references(index)

    def set_marker(self, shell, marker):
        self.references[self.reference_list.currentRow()][1][shell] = marker
        self.update_preview()

    def suggest_core(self):
        code = self.original["core"] if self.mode.currentData() == "append" else shells.core_suggestion(
            [occ for occ, _ in self.references])
        self.core.setCurrentIndex(self.core.findData(code))

    def suggest_active(self):
        core = shells.closed_core(self.core.currentData())
        text = shells.default_active([occ for occ, _ in self.references], core)
        self.active.setText(text)

    def change_mode(self):
        append = self.mode.currentData() == "append"
        self.core.setEnabled(not append)
        self.suggest_core_button.setEnabled(not append)
        if append:
            self.suggest_core()
        self.update_preview()

    def core_changed(self):
        self.draw_shells()
        self.update_preview()

    def update_preview(self, *_):
        if self.updating or not self.references:
            return
        try:
            changes = shells.build_fields(self.references, self.electrons, self.core.currentData(), self.active.text(),
                                          self.jmin.value(), self.jmax.value(), self.excitations.value())
            if self.mode.currentData() == "append":
                changes["lists"] = list(self.original["lists"]) + changes["lists"]
            self.preview.setPlainText(self.step.stdin(dict(self.original, **changes)))
        except ValueError as error:
            self.result_fields = None
            self.preview.clear()
            self.error.setText(str(error))
        else:
            self.result_fields = changes
            self.error.setText(T("追加时沿用当前程序页的闭壳芯。") if self.mode.currentData() == "append" else "")
        self.apply_button.setEnabled(self.result_fields is not None)

    def accept(self):
        if self.result_fields is not None:
            super().accept()
