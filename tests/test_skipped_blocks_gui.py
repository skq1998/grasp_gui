"""缺块文件的完整组成显示，以及文件尚未写完的本机 / 远程提示。"""

from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.gui
pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtCore import QEvent, QObject  # noqa: E402

import grasp_core as core  # noqa: E402
import grasp_index as index  # noqa: E402
import grasp_mix as mix  # noqa: E402
import grasp_remote as remote  # noqa: E402
from test_gui import no_dialogs, spin, window  # noqa: E402, F401
from test_skipped_blocks import EXPECTED, files  # noqa: E402, F401


def isolate(root, name):
    for candidate in root.iterdir():
        if mix.is_mix_file(str(candidate)) and candidate.name != name:
            candidate.unlink()


@pytest.mark.parametrize("language", ["zh", "en"])
@pytest.mark.parametrize("name", EXPECTED)
def test_skipped_files_show_all_levels_with_configuration(qapp, files, monkeypatch, no_dialogs, language, name):
    monkeypatch.setattr(core, "CONFIG_FILE", str(files / "preferences.json"))
    core.set_lang(language)
    isolate(files, name)
    w = window(files, "levels")
    try:
        panel = w.panel("levels")
        spin(qapp, lambda: not panel.loading)
        assert panel.level_table.rowCount() == 2
        assert all(panel.level_table.item(i, 6).text() for i in range(2))
        assert not panel.partial_files
        assert not no_dialogs
    finally:
        assert w.close()


@pytest.mark.parametrize("language", ["zh", "en"])
def test_incomplete_file_has_tooltip_and_status_that_clear_after_completion(qapp, files, monkeypatch, language):
    monkeypatch.setattr(core, "CONFIG_FILE", str(files / "preferences.json"))
    core.set_lang(language)
    path = files / "mid.cm"
    original = path.read_bytes()
    with path.open("rb") as stream:
        end = list(index.record_index(stream))[5]["end"]
    path.write_bytes(original[:end])
    isolate(files, path.name)
    w = window(files, "levels")
    try:
        panel = w.panel("levels")
        spin(qapp, lambda: not panel.loading)
        assert panel.level_table.rowCount() == 1
        assert core.T("文件尚未写完，仅显示完整块中的能级。") in panel.files.item(0).toolTip()
        assert core.T("文件尚未写完：{files}", files=path.name) in w.statusBar().currentMessage()
        changed = []
        panel.files.itemChanged.connect(lambda *args: changed.append(True))
        panel.render_levels()
        panel.render_levels()
        assert len(changed) <= 2  # 重绘不能在路径和警告两个提示之间来回切换，递归触发 itemChanged。
        path.write_bytes(original)
        panel.scan()
        spin(qapp, lambda: not panel.loading)
        assert panel.level_table.rowCount() == 2 and not panel.partial_files
        assert core.T("文件尚未写完，仅显示完整块中的能级。") not in panel.files.item(0).toolTip()
        assert core.T("文件尚未写完：{files}", files=path.name) not in w.statusBar().currentMessage()
    finally:
        assert w.close()


def test_remote_view_preserves_partial_metadata_and_displays_warning(qapp, files, monkeypatch):
    from remote_panel import RemoteLevels
    monkeypatch.setattr(core, "CONFIG_FILE", str(files / "preferences.json"))
    path = files / "mid.cm"
    with path.open("rb") as stream:
        end = list(index.record_index(stream))[5]["end"]
    path.write_bytes(path.read_bytes()[:end])
    class Source(QObject):
        client = SimpleNamespace(host="fixture")
        def submit(self, *args, **kwargs):
            pass
    source = Source()
    panel = RemoteLevels(source, "/server/calc")
    source.setParent(panel)
    try:
        result = remote.Operations().view(str(files), "levels")["data"]
        panel.apply_remote(result)
        assert "mid.cm" in panel.partial_files
        item = next(panel.files.item(i) for i in range(panel.files.count()) if panel.files.item(i).text() == path.name)
        assert core.T("文件尚未写完，仅显示完整块中的能级。") in item.toolTip()
    finally:
        panel.shutdown()
        panel.deleteLater()
        qapp.sendPostedEvents(None, QEvent.DeferredDelete)


def test_multiple_files_do_not_reenter_render_when_tooltips_change(qapp, files, monkeypatch):
    monkeypatch.setattr(core, "CONFIG_FILE", str(files / "preferences.json"))
    path = files / "mid.cm"
    with path.open("rb") as stream:
        end = list(index.record_index(stream))[5]["end"]
    path.write_bytes(path.read_bytes()[:end])
    w = window(files, "levels")
    w.show()
    try:
        panel = w.panel("levels")
        spin(qapp, lambda: not panel.loading)
        assert panel.level_table.rowCount() == 3
        assert panel.partial_files == ["mid.cm"]
        panel.render_levels()
        assert panel.partial_files == ["mid.cm"]
    finally:
        assert w.close()
