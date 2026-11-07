"""剪贴板导入沿用外部配置的确认与预览；复制和文件输出一致。"""

import os
import struct

import pytest

pytestmark = pytest.mark.gui
QtWidgets = pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication

import grasp_config as config
import grasp_core as core
from calc_panel import ConfigDialog, ExportDialog
from conftest import DEMOS


@pytest.fixture
def dialogs(monkeypatch):
    shown = []
    for method in ("information", "warning"):
        monkeypatch.setattr(QtWidgets.QMessageBox, method,
                            staticmethod(lambda *args, **kwargs: shown.append(args[2])))
    return shown


def test_clipboard_import_and_preview(qapp, tmp_path, dialogs):
    cfg, _ = config.load(os.path.join(DEMOS, "example1.json"))
    QGuiApplication.clipboard().setText("```json\n" + config.dumps(cfg) + "```")
    dlg = ConfigDialog(None, [], "")
    assert not dlg.ok.isEnabled()
    assert dlg.open_clipboard()
    assert dlg.ok.isEnabled()
    assert "剪贴板" in dlg.info.toPlainText()
    assert dlg.entry()["cfg"] == cfg
    assert "rnucleus" in dlg.info.toPlainText()
    target = tmp_path / "imported"
    dlg.dir.setText(str(target))
    dlg.accept()
    assert dlg.result() == QtWidgets.QDialog.Accepted
    config.install(dlg.entry()["cfg"], dlg.target())
    file_target = tmp_path / "from-file"
    config.install(config.load(os.path.join(DEMOS, "example1.json"))[0], str(file_target))
    assert core.load_session(target) == core.load_session(file_target)
    assert dialogs == []
    dlg.close()


def test_custom_commands_require_confirmation(qapp, monkeypatch):
    cfg = dict(format=config.FORMAT, version=2, jobs=[dict(step="custom", vals=core.Custom.defaults())])
    QGuiApplication.clipboard().setText(config.dumps(cfg))
    asked = []
    monkeypatch.setattr(QtWidgets.QMessageBox, "question",
                        lambda *args: asked.append(args[2]) or QtWidgets.QMessageBox.No)
    dlg = ConfigDialog(None, [], "")
    assert dlg.open_clipboard()
    assert "自定义命令" in dlg.info.toPlainText()
    dlg.accept()
    assert asked and dlg.result() == QtWidgets.QDialog.Rejected
    monkeypatch.setattr(QtWidgets.QMessageBox, "question", lambda *args: QtWidgets.QMessageBox.Yes)
    dlg.accept()
    assert dlg.result() == QtWidgets.QDialog.Accepted
    dlg.close()


def test_clipboard_error_preserves_selection(qapp, dialogs):
    cfg, _ = config.load(os.path.join(DEMOS, "example1.json"))
    dlg = ConfigDialog(None, [dict(cfg=cfg, warnings=[], path=None)], "")
    QGuiApplication.clipboard().setText("copied the wrong message")
    assert not dlg.open_clipboard()
    assert "copied the wrong message" in dialogs[-1]
    assert dlg.entry()["cfg"] == cfg
    dlg.close()


def test_copy_with_files_and_reference_matches_save(qapp, tmp_path, dialogs):
    attachment = tmp_path / "input.txt"
    attachment.write_text("附带输入\n", encoding="utf-8")
    mix = tmp_path / "state.cm"

    def record(body):
        return struct.pack("<i", len(body)) + body + struct.pack("<i", len(body))

    mix.write_bytes(b"".join(record(x) for x in [b"G92MIX", struct.pack("<6i", 3, 1, 1, 1, 1, 1),
                                               struct.pack("<5i", 1, 1, 1, 2, 1), struct.pack("<i", 1),
                                               struct.pack("<2d", -7.4, 0), struct.pack("<d", 1)]))
    step = core.RNucleus()
    dlg = ExportDialog(None, str(tmp_path), [step.job(step.defaults())])
    dlg.title.setText("Roundtrip")
    item = QtWidgets.QListWidgetItem("input.txt")
    item.setData(Qt.UserRole, str(attachment))
    dlg.files.addItem(item)
    dlg.accept(True)
    assert dlg.result() == QtWidgets.QDialog.Accepted
    assert dlg.to_clipboard
    text = QGuiApplication.clipboard().text()
    destination = tmp_path / "saved.json"
    config.save(dlg.cfg, destination)
    assert text.encode("utf-8") == destination.read_bytes()
    copied, warnings = config.loads(text)
    assert not warnings
    assert copied["files"] == [dict(name="input.txt", text="附带输入\n")]
    assert copied["reference"] == [dict(file="state.cm", energies=[-7.4])]
    imported = ConfigDialog(None, [], "")
    assert imported.open_clipboard()
    assert config.session(imported.entry()["cfg"]) == config.session(dlg.cfg)
    assert dialogs == []
    imported.close()
    dlg.close()


def test_too_large_copy_keeps_clipboard(qapp, tmp_path, monkeypatch, dialogs):
    step = core.RNucleus()
    dlg = ExportDialog(None, str(tmp_path), [step.job(step.defaults())])
    QGuiApplication.clipboard().setText("keep this")
    monkeypatch.setattr(config, "MAX_CLIPBOARD", 10)
    dlg.accept(True)
    assert dlg.result() == QtWidgets.QDialog.Rejected
    assert QGuiApplication.clipboard().text() == "keep this"
    assert "保存为文件" in dialogs[-1]
    dlg.accept()
    assert dlg.result() == QtWidgets.QDialog.Accepted
    assert not dlg.to_clipboard
    dlg.close()


def test_export_menu_copy_and_save_use_same_config(qapp, tmp_path, monkeypatch, dialogs):
    import grasp_gui

    w = grasp_gui.MainWindow(str(tmp_path), "calc")
    step = core.RNucleus()
    w.calc.pipeline = [step.job(step.defaults())]
    destination = tmp_path / "saved.json"

    def accept(self):
        self.title.setText("Same config")
        self.accept(self.to_clipboard)
        return self.result()

    monkeypatch.setattr(ExportDialog, "exec", accept)
    writes = []
    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName",
                        lambda *args: writes.append(1) or (str(destination), ""))
    messages = []
    w.calc.message.connect(messages.append)
    w.calc.export_config(True)
    text = QGuiApplication.clipboard().text()
    assert writes == []
    assert "剪贴板" in messages[-1]
    w.calc.export_config()
    assert destination.read_bytes() == text.encode("utf-8")
    assert writes == [1]
    assert dialogs == []
    w.close()


def test_import_menu_accepts_clipboard_without_file_picker(qapp, tmp_path, monkeypatch, dialogs):
    import grasp_gui

    cfg, _ = config.load(os.path.join(DEMOS, "example1.json"))
    QGuiApplication.clipboard().setText(config.dumps(cfg))
    target = tmp_path / "clipboard-import"

    def accept(self):
        assert self.open_clipboard()
        self.dir.setText(str(target))
        self.accept()
        return self.result()

    monkeypatch.setattr(ConfigDialog, "exec", accept)
    monkeypatch.setattr(QtWidgets.QFileDialog, "getOpenFileName",
                        lambda *args: pytest.fail("clipboard import must not open a file picker"))
    w = grasp_gui.MainWindow(str(tmp_path), "calc")
    w.calc.import_config(False)
    assert w.calc.cur_workdir == str(target)
    assert len(w.calc.pipeline) == len(cfg["jobs"])
    assert dialogs == []
    w.close()



@pytest.mark.parametrize("identifier,title,stem", [
    ("li-example", "Lithium calculation", "li-example"),
    (None, "Lithium calculation", "Lithium_calculation"),
    ("../escape", "Ignored", "escape"),
    (None, "../", "clipboard"),
])
def test_clipboard_directory_uses_config_name(qapp, tmp_path, monkeypatch, identifier, title, stem):
    import calc_panel
    monkeypatch.setattr(calc_panel, "CALC_ROOT", str(tmp_path))
    cfg = dict(format=config.FORMAT, version=2, title=title,
               jobs=[dict(step="rnucleus", vals=core.RNucleus.defaults())])
    if identifier is not None:
        cfg["id"] = identifier
    QGuiApplication.clipboard().setText(config.dumps(cfg))
    d = ConfigDialog(None, [], "")
    assert d.open_clipboard()
    assert d.target() == str(tmp_path / stem)
    (tmp_path / stem).mkdir()
    (tmp_path / stem / "occupied").write_text("keep")
    assert d.open_clipboard()
    assert d.target() == str(tmp_path / (stem + "_2"))
