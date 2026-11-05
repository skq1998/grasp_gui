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
