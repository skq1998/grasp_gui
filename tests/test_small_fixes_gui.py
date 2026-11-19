"""审核修复的本机提示与不丢失其他文件的显示行为。"""

import pytest

pytestmark = pytest.mark.gui
pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtCore import QEvent, QObject, Qt  # noqa: E402
from types import SimpleNamespace  # noqa: E402

import grasp_config as config  # noqa: E402
import grasp_core as core  # noqa: E402
import grasp_workdir as work  # noqa: E402
import grasp_mix as mix  # noqa: E402
import grasp_remote as remote  # noqa: E402
from test_gui import no_dialogs, spin, window  # noqa: E402, F401
from test_small_fixes import mixed_warning_files, nonfinite_files  # noqa: E402
from test_summary import job  # noqa: E402


@pytest.mark.parametrize("language", ["zh", "en"])
def test_clear_refused_before_deletion_only_reports_the_reason(qapp, tmp_path, monkeypatch, no_dialogs, language):
    monkeypatch.setattr(core, "CONFIG_FILE", str(tmp_path / "preferences.json"))
    core.set_lang(language)
    root = tmp_path / "calc"
    config.install(config.export([job("rnucleus")], {}), str(root))
    (root / "result.cm").write_bytes(b"untouched result")
    w = window(root)
    try:
        original = {p.name: p.read_bytes() for p in root.iterdir() if p.is_file()}
        monkeypatch.setattr(work.bg, "status", lambda *args: dict(status="running"))
        w.calc.clear_workdir()
        assert len(no_dialogs) == 1
        assert no_dialogs[0][1][1] == core.T("已有任务在运行，请等待或先停止。")
        assert {p.name: p.read_bytes() for p in root.iterdir() if p.is_file()} == original
    finally:
        assert w.close()


@pytest.mark.parametrize("language", ["zh", "en"])
def test_local_levels_reject_nonfinite_file_and_display_healthy_file(qapp, tmp_path, monkeypatch, no_dialogs, language):
    monkeypatch.setattr(core, "CONFIG_FILE", str(tmp_path / "preferences.json"))
    core.set_lang(language)
    healthy, bad = nonfinite_files(tmp_path / "calc")
    data = mix.load_result_files([str(healthy), str(bad)], 5)
    assert isinstance(data[str(bad)], ValueError)
    w = window(healthy.parent, "levels")
    try:
        panel = w.panel("levels")
        spin(qapp, lambda: not panel.loading)
        assert panel.level_table.rowCount() == 2
        item = next(panel.files.item(i) for i in range(panel.files.count()) if panel.files.item(i).text() == bad.name)
        assert core.T("能量不是有限的数，计算可能已经发散") in item.toolTip()
        assert bad.name in w.statusBar().currentMessage()
        assert not no_dialogs
    finally:
        assert w.close()


@pytest.mark.parametrize("language", ["zh", "en"])
def test_local_level_warnings_coexist_and_clear_when_errors_are_deselected(qapp, tmp_path, monkeypatch, language):
    monkeypatch.setattr(core, "CONFIG_FILE", str(tmp_path / "preferences.json"))
    core.set_lang(language)
    healthy, bad, partial = mixed_warning_files(tmp_path / "calc")
    w = window(healthy.parent, "levels")
    try:
        panel = w.panel("levels")
        spin(qapp, lambda: not panel.loading)
        assert panel.level_table.rowCount() == 3
        text = w.statusBar().currentMessage()
        assert core.T("能量不是有限的数，计算可能已经发散") in text
        assert core.T("文件尚未写完：{files}", files=partial.name) in text
        assert core.T("无法确定哈密顿量，零点可能不可比") in text
        assert text == core.T("；").join(panel.level_warnings) and len(panel.level_warnings) == 3
        if language == "en":
            assert "；" not in text
        for i in range(panel.files.count()):
            item = panel.files.item(i)
            if item.text() != bad.name:
                item.setCheckState(Qt.Unchecked)
        spin(qapp, lambda: not panel.loading)
        assert len(panel.level_warnings) == 1 and not panel.partial_files
        assert bad.name in w.statusBar().currentMessage()
        item = next(panel.files.item(i) for i in range(panel.files.count()) if panel.files.item(i).text() == bad.name)
        item.setCheckState(Qt.Unchecked)
        spin(qapp, lambda: not panel.loading)
        assert panel.level_warnings == [] and w.statusBar().currentMessage() == ""
    finally:
        assert w.close()


def test_remote_levels_show_nonfinite_file_error_and_keep_other_levels(qapp, tmp_path, monkeypatch):
    from remote_panel import RemoteLevels
    monkeypatch.setattr(core, "CONFIG_FILE", str(tmp_path / "preferences.json"))
    healthy, bad, partial = mixed_warning_files(tmp_path)
    class Source(QObject):
        client = SimpleNamespace(host="server")
        def submit(self, *args, **kwargs):
            pass
    source = Source()
    panel = RemoteLevels(source, "/server/calc")
    source.setParent(panel)
    try:
        data = remote.Operations().view(str(tmp_path), "levels")["data"]
        panel.apply_remote(data)
        assert panel.level_table.rowCount() == 3 and panel.partial_files == [partial.name]
        item = next(panel.files.item(i) for i in range(panel.files.count()) if panel.files.item(i).text() == bad.name)
        assert core.T("能量不是有限的数，计算可能已经发散") in item.toolTip()
        panel.redraw()
        assert panel.level_table.rowCount() == 3
    finally:
        panel.shutdown()
        panel.deleteLater()
        qapp.sendPostedEvents(None, QEvent.DeferredDelete)
