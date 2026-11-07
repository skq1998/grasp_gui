"""界面冒烟测试 (offscreen)：主窗口在中英文下能建立、切换标签页和语言、运行流程并显示输出、关闭。
对话框一律用替身代替，测试不会停在等待点击的窗口上。"""

import os
import time

import pytest

pytestmark = pytest.mark.gui
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

import grasp_bg as bg                    # noqa: E402
import grasp_config as config            # noqa: E402
import grasp_core as core                # noqa: E402
from conftest import DEMOS               # noqa: E402


@pytest.fixture
def no_dialogs(monkeypatch):
    """记录弹出的消息框；出现消息框的测试应明确检查它。"""
    shown = []
    QMessageBox = QtWidgets.QMessageBox
    for name in ("information", "warning", "critical"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, n=name, **k: shown.append((n, a[1:3]))))
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: shown.append(("question", a[1:3])) or QMessageBox.No))
    monkeypatch.setattr(QMessageBox, "exec", lambda self: shown.append(("exec", self.text())) or 0)
    return shown


def window(workdir, tab="calc"):
    import grasp_gui
    return grasp_gui.MainWindow(str(workdir), tab)


def spin(app, until, timeout=30):
    end = time.monotonic() + timeout
    while not until():
        if time.monotonic() > end:
            pytest.fail("timeout")
        app.processEvents()
        time.sleep(0.02)


@pytest.mark.parametrize("lang", ["zh", "en"])
def test_main_window_tabs(qapp, tmp_path, no_dialogs, lang):
    core.set_lang(lang)
    w = window(tmp_path)
    w.show()
    for tab in ("wfn", "levels", "calc"):
        w.show_tab(tab)
        qapp.processEvents()
    w.set_language("en" if lang == "zh" else "zh")
    qapp.processEvents()
    assert w.close()
    assert no_dialogs == []


@pytest.mark.parametrize("name", ["example1", "case1"])
def test_imported_demo_fills_pipeline(qapp, tmp_path, no_dialogs, name):
    cfg, _ = config.load(os.path.join(DEMOS, name + ".json"))
    config.install(cfg, str(tmp_path / "wd"))
    w = window(tmp_path / "wd")
    assert len(w.calc.pipeline) == len(cfg["jobs"])
    assert w.close()
    assert no_dialogs == []


def test_edit_pipeline_step(qapp, tmp_path, no_dialogs):
    """“编辑”把步骤的参数和态组载回表单，“更新所选”只替换同一程序的步骤。"""
    w = window(tmp_path)
    c = w.calc
    forms = {f.step.key: f for f in c.forms}
    c.switch_group("even")
    c.nav.setCurrentWidget(forms["rmcdhf"])
    forms["rmcdhf"].set_values({"nscf": "100", "save": "even2"})
    c.add_to_pipeline()
    c.nav.setCurrentWidget(forms["rnucleus"])
    c.add_to_pipeline()
    c.switch_group("odd")
    c.plist.setCurrentRow(0)
    c.edit_job()
    assert c.group == "even"
    assert c.nav.currentWidget() is forms["rmcdhf"]
    assert forms["rmcdhf"].values()["save"] == "even2"
    forms["rmcdhf"].set_values({"nscf": "250"})
    c.update_job()
    assert c.pipeline[0]["vals"]["nscf"] == "250"
    assert c.pipeline[0]["group"] == "even"
    assert c.pipeline[0]["stdin"].split()[-1] == "250"
    assert [j["step"] for j in c.pipeline] == ["rmcdhf", "rnucleus"]
    assert no_dialogs == []
    c.plist.setCurrentRow(1)
    c.update_job()
    assert c.pipeline[1]["step"] == "rnucleus"
    assert [n for n, _ in no_dialogs] == ["information"]
    no_dialogs.clear()
    assert w.close()


@pytest.mark.parametrize("command", ["sleep 0.5; echo hello-from-test", "echo hello-from-test"],
                         ids=["normal", "instant"])
def test_run_pipeline_shows_output(qapp, tmp_path, no_dialogs, command):
    """流程经由后台运行进程执行，输出显示在计算页；结束后关闭窗口不再询问。"""
    doc = dict(format=config.FORMAT, version=2, jobs=[
        dict(step="custom", vals=dict(prog="sh", args=f"-c '{command}'", stdin="", log=""))])
    cfg, _ = config.check(doc)
    config.install(cfg, str(tmp_path / "wd"))
    w = window(tmp_path / "wd")
    w.calc.run_pipeline()
    assert w.calc.busy
    spin(qapp, lambda: not w.calc.busy)
    spin(qapp, lambda: "hello-from-test" in w.calc.out.toPlainText())
    assert bg.status(str(tmp_path / "wd"))["status"] == "ok"
    assert w.close()
    assert no_dialogs == []


def test_close_while_running_asks(qapp, tmp_path, no_dialogs):
    """计算进行中关闭窗口：询问后台继续 / 停止 / 取消 (替身返回“取消”，窗口保持打开，计算继续)。"""
    doc = dict(format=config.FORMAT, version=2, jobs=[
        dict(step="custom", vals=dict(prog="sh", args="-c 'sleep 30'", stdin="", log=""))])
    cfg, _ = config.check(doc)
    config.install(cfg, str(tmp_path / "wd"))
    w = window(tmp_path / "wd")
    w.calc.run_pipeline()
    spin(qapp, lambda: (bg.status(str(tmp_path / "wd")) or {}).get("status") == "running")
    assert not w.close()
    assert [n for n, _ in no_dialogs] == ["exec"]
    w.calc.stop_run()
    spin(qapp, lambda: not w.calc.busy)
    assert bg.status(str(tmp_path / "wd"))["status"] == "stopped"


@pytest.mark.parametrize("lang", ["zh", "en"])
def test_csfg_partition_forms(qapp, tmp_path, no_dialogs, lang):
    """新程序页可在两个态组中编辑，新增选项不影响逐块 CI。"""
    core.set_lang(lang)
    w = window(tmp_path)
    w.calc.set_package("GRASPG")
    for group in core.GROUPS:
        w.calc.switch_group(group)
        for key in ("rcsfgzerofirst_csfg", "rangular_csfg", "rci_csfg"):
            form = w.calc.nav.by_key[key]
            w.calc.nav.setCurrentWidget(form)
            assert form.values()["zero"] == group + "2.g"
            if "zf" in form.put:
                form.put["zf"](True)
                assert "@ICCUT" in form.step.stdin(form.values())
    assert "zf" not in w.calc.nav.by_key["rci_block_csfg"].put
    assert w.close()
    assert no_dialogs == []


@pytest.mark.parametrize("lang", ["zh", "en"])
def test_nsic_form(qapp, tmp_path, no_dialogs, lang):
    from calc_panel import StepForm

    core.set_lang(lang)
    form = StepForm(core.RMcdhf(), "")
    value = form.get["nsic"].__self__
    assert not value.isEnabled()
    form.set_values(dict(nsic="3", modify_nsic=True))
    assert value.isEnabled()
    assert "\ny\n3\nn\nn\n1\n" in form.job()["stdin"]
    form.put["modify_nsic"](False)
    assert not value.isEnabled()
    form.close()

    w = window(tmp_path)
    form = next(f for f in w.calc.forms if f.step.key == "rmcdhf")
    form.set_values(dict(modify_nsic=True, nsic="-1"))
    w.calc.nav.setCurrentWidget(form)
    assert w.calc.current_job() is None
    assert no_dialogs[-1][0] == "critical"
    assert "NSIC" in no_dialogs[-1][1][1]
    w.close()


@pytest.mark.parametrize("lang", ["zh", "en"])
def test_hfszeeman_form(qapp, tmp_path, no_dialogs, lang):
    core.set_lang(lang)
    w = window(tmp_path)
    c = w.calc
    form = next(f for f in c.forms if f.step.key == "rhfszeeman95")
    c.nav.setCurrentWidget(form)
    form.set_values(dict(state="odd_result", offdiag=False, saveas="zeeman.txt", log="hfs.log"))
    c.add_to_pipeline()
    assert c.pipeline[-1]["stdin"] == "y\nodd_result\ny\nn\n"
    assert c.pipeline[-1]["post"] == [("cp", "odd_result.ch", "zeeman.txt")]
    c.switch_group("even")
    assert form.values()["state"] != "odd_result"
    c.switch_group("odd")
    assert form.values()["state"] == "odd_result"
    assert form.values()["offdiag"] is False
    form.set_values(dict(state="bad name"))
    assert c.current_job() is None
    assert no_dialogs[-1][0] == "critical"
    assert "23" in no_dialogs[-1][1][1]
    assert w.close()


@pytest.mark.parametrize("checked", [False, True])
def test_field_enabled_by(qapp, checked):
    from calc_panel import StepForm

    class DependentStep(core.Step):
        key = "dependency_test"
        # 被控制字段可以放在复选框之前；默认状态和恢复后的状态都要生效。
        fields = [core.Entry("value", "value", "0", enabled_by="enabled"),
                  core.Check("enabled", "enabled", checked)]

    form = StepForm(DependentStep(), "")
    value = form.get["value"].__self__
    assert value.isEnabled() == checked
    form.set_values(dict(enabled=not checked))
    assert value.isEnabled() != checked
    form.put["enabled"](checked)
    assert value.isEnabled() == checked
    form.close()


def test_preview_column_and_buttons_adapt(qapp, tmp_path, no_dialogs):
    """下方足够宽时输入预览单独成一栏，变窄时回到标签页；按钮高度不够时排成两列。"""
    from calc_panel import ButtonColumn

    w = window(tmp_path)
    c = w.calc
    w.resize(1900, 950)
    w.show()
    qapp.processEvents()
    assert c.preview_column and c.btabs.count() == 1 and c.pbox.isVisible()
    c.show_preview()
    w.resize(1100, 950)
    qapp.processEvents()
    assert not c.preview_column and c.btabs.count() == 2
    c.show_preview()
    assert c.btabs.currentWidget() is c.prev
    c.btabs.setCurrentIndex(0)              # 流程页可见时按钮才随高度重排
    buttons = c.findChild(ButtonColumn)
    c.split.setSizes([800, 150])
    qapp.processEvents()
    assert buttons.columns == 2
    c.split.setSizes([150, 800])
    qapp.processEvents()
    assert buttons.columns == 1
    assert w.close()
    assert no_dialogs == []


def test_progress_label_survives_status_messages(qapp, tmp_path, no_dialogs):
    """运行进度在常驻标签中：其他页的状态消息不会清掉它；本步时间和最后输出时间来自运行状态和日志。"""
    import time as _time

    doc = dict(format=config.FORMAT, version=2, jobs=[
        dict(step="custom", vals=dict(prog="sh", args="-c 'echo start; sleep 3'", stdin="", log=""))])
    cfg, _ = config.check(doc)
    config.install(cfg, str(tmp_path / "wd"))
    w = window(tmp_path / "wd")
    w.calc.run_pipeline()
    spin(qapp, lambda: "sh" in w.progress_label.text())
    w.statusBar().showMessage("other page message")
    w.show_tab("levels")
    qapp.processEvents()
    assert "sh" in w.progress_label.text()
    log = tmp_path / "wd" / bg.RUN_LOG
    old = _time.time() - 125
    os.utime(log, (old, old))
    w.calc.update_progress()
    assert "2:0" in w.progress_label.text()        # 最后输出于 2:05 前
    assert "sh" not in w.statusBar().currentMessage()   # 进度只在常驻标签中显示一处
    spin(qapp, lambda: not w.calc.busy, timeout=30)
    w.calc.update_progress()
    assert core.T("上次运行完成：共 {n} 步", n=1) in w.progress_label.text()
    assert w.close()
    assert no_dialogs == []


def test_failed_step_stays_in_status_bar(qapp, tmp_path, no_dialogs):
    """运行出错后，常驻标签和输出末尾写明停在第几步；切换页面、重新打开目录后仍然显示。"""
    jobs = [dict(step="custom", vals=dict(prog="sh", args=f"-c '{c}'", stdin="", log=""))
            for c in ("echo one", "echo two; exit 3", "echo three")]
    cfg, _ = config.check(dict(format=config.FORMAT, version=2, jobs=jobs))
    config.install(cfg, str(tmp_path / "wd"))
    w = window(tmp_path / "wd")
    w.calc.run_pipeline()
    spin(qapp, lambda: not w.calc.busy, timeout=30)
    expected = core.T("上次运行在第 {i}/{n} 步 {t} 出错", i=2, n=3, t=w.calc.pipeline[1]["title"])
    w.statusBar().showMessage("other page message")
    w.show_tab("levels")
    qapp.processEvents()
    assert expected in w.progress_label.text()
    assert expected in w.calc.out.toPlainText()
    assert w.close()
    w = window(tmp_path / "wd")                        # 重新打开：从运行状态文件恢复
    qapp.processEvents()
    w.calc.update_progress()
    assert expected in w.progress_label.text()
    assert w.close()
    assert no_dialogs == []


def test_level_diagram_wheel_scrolls_and_pinch_zooms(qapp):
    """滚轮上下平移 (范围不变)；Ctrl + 滚轮和触控板捏合以鼠标处为中心缩放。"""
    from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent
    from level_panel import PARITY_COLORS, LevelDiagram

    d = LevelDiagram()
    d.resize(600, 400)
    levels = [dict(key=("a", i), y=float(y), label="", color=PARITY_COLORS[1], info="")
              for i, y in enumerate((0, 100, 200, 300))]
    d.set_columns([dict(file="a", title="a", levels=levels)], "cm⁻¹")
    d.show()
    qapp.processEvents()
    rect, full = d.plot_rect(), d.y_range()
    pos = QPointF(rect.center())

    def wheel(angle, pixels=0, modifiers=Qt.NoModifier):
        d.wheelEvent(QWheelEvent(pos, QPointF(d.mapToGlobal(pos.toPoint())), QPoint(0, pixels), QPoint(0, angle),
                                 Qt.NoButton, modifiers, Qt.NoScrollPhase, False))

    wheel(120)                                     # 向上滚一格：看更高的能量，范围不变
    lo, hi = d.y_range()
    span = full[1] - full[0]
    assert abs((lo - full[0]) - 0.1 * span) < 1e-6 and abs((hi - lo) - span) < 1e-6
    wheel(-120)
    assert all(abs(a - b) < 1e-6 for a, b in zip(d.y_range(), full))
    wheel(0, pixels=int(rect.height() / 4))       # 触控板双指滑动：按像素平移
    lo, hi = d.y_range()
    assert abs((lo - full[0]) - span / 4) < 1e-6
    d.yr = None
    centre = d.from_py(pos.y(), rect, full)
    wheel(120, modifiers=Qt.ControlModifier)      # Ctrl + 滚轮 (Windows 的捏合)：放大
    lo, hi = d.y_range()
    assert abs((hi - lo) - 0.8 * span) < 1e-6 and lo < centre < hi

    class Pinch:
        def type(self):
            return QEvent.NativeGesture

        def gestureType(self):
            return Qt.ZoomNativeGesture

        def position(self):
            return pos

        def value(self):
            return 0.25

    d.yr = None
    assert d.event(Pinch())
    lo, hi = d.y_range()
    assert abs((hi - lo) - span / 1.25) < 1e-6 and lo < centre < hi
    d.close()


def test_level_diagram_box_zoom(qapp):
    """右键 (或 Ctrl + 左键) 拖出的框放大到其能量范围；单击右键或 Backspace 回到上一个视图，双击复原。"""
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from level_panel import PARITY_COLORS, LevelDiagram

    d = LevelDiagram()
    d.resize(600, 400)
    levels = [dict(key=("a", i), y=float(y), label="", color=PARITY_COLORS[1], info="") for i, y in enumerate((0, 100, 200, 300))]
    d.set_columns([dict(file="a", title="a", levels=levels)], "cm⁻¹")
    d.show()
    qapp.processEvents()
    rect, full = d.plot_rect(), d.y_range()
    top, bottom = d.to_py(250, rect, full), d.to_py(150, rect, full)
    x = int(rect.center().x())

    def drag(button, modifiers, y0, y1):
        QTest.mousePress(d, button, modifiers, QPoint(x, int(y0)))
        QTest.mouseMove(d, QPoint(x + 5, int(y1)))
        QTest.mouseRelease(d, button, modifiers, QPoint(x + 5, int(y1)))

    drag(Qt.RightButton, Qt.NoModifier, top, bottom)
    lo, hi = d.y_range()
    assert abs(lo - 150) < 2 and abs(hi - 250) < 2
    drag(Qt.LeftButton, Qt.ControlModifier, d.to_py(240, d.plot_rect(), d.y_range()),
         d.to_py(200, d.plot_rect(), d.y_range()))
    lo, hi = d.y_range()
    assert abs(lo - 200) < 2 and abs(hi - 240) < 2
    QTest.keyClick(d, Qt.Key_Backspace)
    lo, hi = d.y_range()
    assert abs(lo - 150) < 2 and abs(hi - 250) < 2
    QTest.mouseClick(d, Qt.RightButton, Qt.NoModifier, QPoint(x, int(top)))     # 单击右键：回到上一个视图
    assert d.y_range() == full
    drag(Qt.RightButton, Qt.NoModifier, top, bottom)
    QTest.mouseDClick(d, Qt.LeftButton, Qt.NoModifier, QPoint(x, int(top)))
    assert d.y_range() == full and d.history == []
    d.close()


def test_open_working_directory_entries(qapp, tmp_path, monkeypatch, no_dialogs):
    """文件菜单、“最近”菜单和程序输出的“工作目录”菜单都能打开任意目录，不只是最近的目录。"""
    from PySide6.QtGui import QKeySequence
    from PySide6.QtWidgets import QFileDialog, QMenu, QPushButton

    def menu(title):
        return next(m for m in w.findChildren(QMenu) if m.title() == core.T(title))

    (tmp_path / "a").mkdir()
    w = window(tmp_path / "a")
    act = next(a for a in menu("文件").actions() if a.text() == core.T("打开工作目录…"))
    assert act.shortcut() == QKeySequence(QKeySequence.Open)
    targets = []
    for name in ("b", "c", "d"):
        (tmp_path / name).mkdir()
        targets.append(str(tmp_path / name))
    chosen = iter(targets)
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: next(chosen)))
    act.trigger()
    assert w.cur_workdir == targets[0]
    recent = menu("最近的工作目录")
    recent.aboutToShow.emit()
    first = recent.actions()[0]
    assert first.text() == core.T("打开其他目录…")
    first.trigger()
    assert w.cur_workdir == targets[1]
    button = next(b for b in w.calc.findChildren(QPushButton) if b.text() == core.T("工作目录"))
    act = next(a for a in button.menu().actions() if a.text() == core.T("打开工作目录…"))
    act.trigger()
    assert w.cur_workdir == targets[2]
    assert w.close()
    assert no_dialogs == []
