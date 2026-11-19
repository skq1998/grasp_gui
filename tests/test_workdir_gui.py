"""工作目录操作的真实对话框路径；追加运行使用后台进程和替身命令。"""

import copy
import os

import pytest

pytestmark = pytest.mark.gui
QtWidgets = pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtCore import Qt             # noqa: E402

import calc_panel as panel                # noqa: E402
import grasp_config as config             # noqa: E402
import grasp_core as core                 # noqa: E402
import grasp_session as sessions          # noqa: E402
import grasp_workdir as work              # noqa: E402
from conftest import DEMOS                 # noqa: E402
from test_gui import no_dialogs, spin, window  # noqa: E402, F401
from test_large_files import make_files  # noqa: E402
from test_mcp import wavefunction  # noqa: E402
from test_workdir import cfg, job          # noqa: E402


@pytest.mark.parametrize("tab", ["levels", "wfn"])
@pytest.mark.parametrize("switch_directory", [False, True])
def test_viewing_results_leaves_directory_untouched(qapp, tmp_path, monkeypatch, no_dialogs, tab, switch_directory):
    monkeypatch.setattr(core, "CONFIG_FILE", str(tmp_path / "preferences.json"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    root = tmp_path / "results"
    make_files(root, ncf=2, nev=2)
    wavefunction(root / "state.w")
    original = {p.name: p.read_bytes() for p in root.iterdir()}
    w = window(root, tab)
    if tab == "levels":
        spin(qapp, lambda: not w.panel(tab).loading)
        assert w.panel(tab).level_table.rowCount() == 2
    else:
        assert w.panel(tab).checked_paths() == [str(root / "state.w")]
    if switch_directory:
        target = tmp_path / "other"
        target.mkdir()
        w.change_workdir(str(target))
        assert w.cur_workdir == str(target)
    assert w.close() and not no_dialogs
    assert {p.name for p in root.iterdir()} == original.keys()
    assert all((root / name).read_bytes() == value for name, value in original.items())


def test_inherited_parameters_navigation_and_run_preferences_do_not_create_record(qapp, tmp_path, no_dialogs):
    root = tmp_path / "old"
    config.install(cfg([job("rnucleus", z="6")]), root)
    target = tmp_path / "new"
    target.mkdir()
    w = window(root)
    w.change_workdir(str(target))
    assert next(f for f in w.calc.forms if f.step.key == "rnucleus").values()["z"] == "6"
    w.calc.switch_group("even")
    w.calc.nav.setCurrentWidget(w.calc.group_forms()[0])
    w.calc.show_preview()
    w.calc.switch_group("odd")
    w.run.mpitmp_edit.setText("changed-temp")
    w.run.save()
    assert w.close() and not no_dialogs
    assert not (target / core.SESSION_FILE).exists()
    assert core.load_config()["mpitmp"] == "changed-temp"


@pytest.mark.parametrize("edit", ["form", "group", "add", "clear"])
def test_calculation_actions_create_first_record(qapp, tmp_path, no_dialogs, edit):
    w = window(tmp_path)
    form = next(f for f in w.calc.forms if f.step.key == "rnucleus")
    w.calc.nav.setCurrentWidget(form)
    if edit == "form":
        form.set_values({"z": "6"})
        assert not (tmp_path / core.SESSION_FILE).exists()
    elif edit == "group":
        w.calc.switch_group("even")
        assert not (tmp_path / core.SESSION_FILE).exists()
        form = next(f for f in w.calc.group_forms() if f.step.key == "rmcdhf")
        form.set_values({"nscf": "222"})
        w.calc.switch_group("odd")
        assert core.load_session(tmp_path)["groups"]["even"]["rmcdhf"]["nscf"] == "222"
    elif edit == "add":
        w.calc.add_to_pipeline()
        assert core.load_session(tmp_path)["pipeline"][0]["step"] == "rnucleus"
    else:
        w.calc.clear_pipeline()
        assert core.load_session(tmp_path)["pipeline"] == []
    assert w.close() and not no_dialogs
    if edit == "form":
        assert core.load_session(tmp_path)["steps"]["rnucleus"]["z"] == "6"


def test_single_step_run_creates_first_record(qapp, tmp_path, no_dialogs):
    w = window(tmp_path)
    form = next(f for f in w.calc.forms if f.step.key == "custom")
    form.set_values({"prog": "sh", "args": "-c 'echo calculated > result.txt'"})
    w.calc.nav.setCurrentWidget(form)
    w.calc.run_current()
    assert w.calc.busy and (tmp_path / core.SESSION_FILE).is_file()
    spin(qapp, lambda: not w.calc.busy)
    assert (tmp_path / "result.txt").read_text() == "calculated\n"
    assert w.close() and not no_dialogs


def test_existing_record_saves_without_changing_contents(qapp, tmp_path, no_dialogs):
    config.install(cfg([job()]), tmp_path)
    assert window(tmp_path).close()  # 先由界面补齐完整记录。
    path = tmp_path / core.SESSION_FILE
    original = path.read_bytes()
    os.utime(path, ns=(1, 1))
    assert window(tmp_path, "levels").close() and not no_dialogs
    assert path.read_bytes() == original and path.stat().st_mtime_ns != 1


def test_record_save_keeps_slurm_bindir(qapp, tmp_path, no_dialogs):
    config.install(cfg([job()]), tmp_path)
    w = window(tmp_path)
    w.calc.slurm["bindir"] = "~/cluster/grasp"
    assert w.close() and not no_dialogs
    data, _ = sessions.read(tmp_path)
    assert data["slurm"]["bindir"] == "~/cluster/grasp"
    assert "bindir" not in data


def test_external_first_record_still_prompts_before_close(qapp, tmp_path, no_dialogs, monkeypatch):
    w = window(tmp_path, "levels")
    config.install(cfg([job()]), tmp_path)
    shown = []

    def load(dialog):
        shown.append(True)
        dialog.choose("load")
        return dialog.result()

    monkeypatch.setattr(panel.SessionChangedDialog, "exec", load)
    assert w.close() and not no_dialogs
    assert shown == [True] and len(core.load_session(tmp_path)["pipeline"]) == 1


@pytest.mark.parametrize("lang", ["zh", "en"])
def test_update_preview_and_conflicts(qapp, tmp_path, lang):
    core.set_lang(lang)
    config.install(cfg([job()], [dict(name="input", text="old\n")]), str(tmp_path))
    revised = cfg([job(), job("rsave")], [dict(name="input", text="new\n")])
    plan = work.prepare_update(tmp_path, revised)
    d = panel.UpdateDialog(None, plan)
    assert d.overwrite() == set()
    assert "old" in d.diff.toPlainText() and "new" in d.diff.toPlainText()
    d.copy_summary()
    assert qapp.clipboard().text() == plan["text"]
    assert d.added.isEnabled()
    d.files.item(0).setCheckState(Qt.Checked)
    assert d.overwrite() == {"input"} and not d.added.isEnabled()
    d.accept_update(True)
    assert d.result() != QtWidgets.QDialog.Accepted
    d.accept_update(False)
    assert d.result() == QtWidgets.QDialog.Accepted and not d.run_added
    assert (tmp_path / "input").read_text() == "old\n"


def test_import_mode_defaults_to_new_directory(qapp, tmp_path):
    d = panel.ConfigDialog(None, [dict(cfg=cfg([job()]), warnings=[], path=None)], "", str(tmp_path))
    assert not d.update.isChecked()
    new_target = d.dir.text()
    d.update.setChecked(True)
    assert d.target() == str(tmp_path) and d.dir.isReadOnly() and not d.browse.isEnabled()
    d.update.setChecked(False)
    assert d.dir.text() == new_target and not d.dir.isReadOnly() and d.browse.isEnabled()


@pytest.mark.parametrize("source", ["file", "clipboard"])
def test_import_update_runs_only_appended_steps(qapp, tmp_path, no_dialogs, monkeypatch, source):
    original, _ = config.load(os.path.join(DEMOS, "example1.json"))
    original.setdefault("files", []).append(dict(name="extra.in", text="preserve input"))
    root = tmp_path / "calc"
    config.install(original, str(root))
    old_record = (root / core.SESSION_FILE).read_bytes()
    result = root / "original.cm"
    result.write_bytes(b"original result\x00do not touch")
    revised = copy.deepcopy(original)
    revised["jobs"] += [job("custom", prog="sh", args=f"-c 'echo {i} >> appended.txt'") for i in (1, 2)]
    path = tmp_path / "update.json"
    config.save(revised, str(path))
    seen = []

    def choose_update(d):
        assert d.update.isChecked() and d.target() == str(root)
        if source == "clipboard":
            qapp.clipboard().setText("```json\n" + config.dumps(revised) + "```")
            assert d.open_clipboard() and d.target() == str(root)
        d.accept()
        return d.result()

    def append_only(d):
        assert d.plan["append_from"] == len(original["jobs"])
        assert core.T("前面 {n} 步不变，新增 {m} 步", n=len(original["jobs"]), m=2) in d.summary.toPlainText()
        seen.append(d.plan)
        d.accept_update(True)
        return d.result()

    monkeypatch.setattr(panel.ConfigDialog, "exec", choose_update)
    monkeypatch.setattr(panel.UpdateDialog, "exec", append_only)
    confirmations = []
    monkeypatch.setattr(QtWidgets.QMessageBox, "question",
                        lambda *a, **k: confirmations.append(a[2]) or QtWidgets.QMessageBox.Yes)
    w = window(root)
    w.calc.import_config(False, str(path) if source == "file" else None, update=True)
    assert len(seen) == 1 and len(confirmations) == 1 and w.calc.busy
    spin(qapp, lambda: not w.calc.busy)
    assert (root / "appended.txt").read_text().splitlines() == ["1", "2"]
    assert result.read_bytes() == b"original result\x00do not touch"
    assert work.backups(root)[0].read_bytes() == old_record
    assert len(w.calc.pipeline) == len(revised["jobs"])
    assert not no_dialogs
    assert w.close()
    restarted = window(root)

    def clear_results(d):
        assert not d.plan["unknown_inputs"]
        assert d.selected_names() >= {"original.cm", "appended.txt"}
        assert {x["name"] for x in d.plan["kept"]} >= {"extra.in", config.INPUTS_FILE, core.SESSION_FILE}
        d.accept()
        return d.result()

    monkeypatch.setattr(panel.ClearDialog, "exec", clear_results)
    restarted.calc.clear_workdir()
    assert not result.exists() and not (root / "appended.txt").exists()
    assert (root / "extra.in").read_text() == "preserve input"
    assert config.load_inputs(root) == {"extra.in"}
    assert (root / core.SESSION_FILE).is_file()
    assert restarted.close() and not no_dialogs


def test_update_cancel_restore_and_running_guard(qapp, tmp_path, no_dialogs, monkeypatch):
    config.install(cfg([job()]), str(tmp_path))
    initial = (tmp_path / core.SESSION_FILE).read_bytes()
    w = window(tmp_path)
    monkeypatch.setattr(panel.UpdateDialog, "exec", lambda d: QtWidgets.QDialog.Rejected)
    w.calc.update_workdir(cfg([job(z="4")]))
    assert (tmp_path / core.SESSION_FILE).read_bytes() == initial
    monkeypatch.setattr(panel.UpdateDialog, "exec", lambda d: QtWidgets.QDialog.Accepted)
    w.calc.update_workdir(cfg([job(z="4")]))
    assert w.calc.pipeline[0]["vals"]["z"] == "4"
    monkeypatch.setattr(QtWidgets.QMessageBox, "question", lambda *a, **k: QtWidgets.QMessageBox.Yes)
    w.calc.restore_workdir()
    assert w.calc.pipeline[0]["vals"]["z"] == job()["vals"]["z"]
    monkeypatch.setattr(work.bg, "status", lambda p: dict(status="running"))
    w.calc.update_workdir(cfg([job(z="9")]))
    assert len(no_dialogs) == 1 and "运行" in no_dialogs[0][1][1]
    no_dialogs.clear()
    monkeypatch.setattr(work.bg, "status", lambda p: None)
    assert w.close()


def test_clear_selection_and_all_delete_survives_close(qapp, tmp_path, no_dialogs, monkeypatch):
    config.install(cfg([job()], [dict(name="input", text="keep")]), str(tmp_path))
    (tmp_path / "result.cm").write_bytes(b"result")
    (tmp_path / "other.cm").write_bytes(b"keep result")
    (tmp_path / ".grasp_gui").mkdir()
    (tmp_path / ".grasp_gui" / "cache").write_text("cache")
    w = window(tmp_path)

    def delete_results(d):
        assert not d.all_files.isChecked()
        assert {d.kept.item(i).text() for i in range(d.kept.count())} == {
            "input", core.SESSION_FILE, config.INPUTS_FILE, "grasp_run"}
        for i in range(d.files.count()):
            if d.files.item(i).data(Qt.UserRole)["name"] == "other.cm":
                d.files.item(i).setCheckState(Qt.Unchecked)
        d.accept()
        return d.result()

    monkeypatch.setattr(panel.ClearDialog, "exec", delete_results)
    w.calc.clear_workdir()
    assert not (tmp_path / "result.cm").exists() and not (tmp_path / ".grasp_gui").exists()
    assert (tmp_path / "input").read_text() == "keep" and (tmp_path / "other.cm").exists()

    def delete_all(d):
        d.all_files.setChecked(True)
        assert not d.ok.isEnabled()
        d.confirmation.setText(tmp_path.name)
        assert d.ok.isEnabled()
        d.accept()
        return d.result()

    monkeypatch.setattr(panel.ClearDialog, "exec", delete_all)
    w.calc.clear_workdir()
    assert w.calc.pipeline == [] and list(tmp_path.iterdir()) == []
    assert w.close() and list(tmp_path.iterdir()) == []
    assert not no_dialogs


def test_persistent_inputs_survive_update_and_restart(qapp, tmp_path, no_dialogs, monkeypatch):
    config.install(cfg([job()], [dict(name="old.in", text="must retain")]), str(tmp_path))
    w = window(tmp_path)
    monkeypatch.setattr(panel.UpdateDialog, "exec", lambda d: QtWidgets.QDialog.Accepted)
    w.calc.update_workdir(cfg([job(z="4")], [dict(name="new.in", text="new")]))
    assert w.close()
    restarted = window(tmp_path)
    d = panel.ClearDialog(restarted.calc, str(tmp_path))
    assert not d.plan["unknown_inputs"]
    assert {x["name"] for x in d.plan["kept"]} >= {"old.in", "new.in", config.INPUTS_FILE}
    assert restarted.close() and not no_dialogs


def test_untracked_clear_needs_separate_confirmation(qapp, tmp_path, no_dialogs, monkeypatch):
    (tmp_path / "valuable").write_text("keep")
    w = window(tmp_path)
    # 刚打开空白目录时窗口不自动保存记录，因此仍需单独确认。
    (tmp_path / core.SESSION_FILE).unlink(missing_ok=True)
    called = []
    monkeypatch.setattr(panel.ClearDialog, "exec", lambda d: called.append(True) or QtWidgets.QDialog.Rejected)
    w.calc.clear_workdir()
    assert not called and any("没有工作目录记录" in x[1][1] for x in no_dialogs)
    monkeypatch.setattr(QtWidgets.QMessageBox, "question", lambda *a, **k: QtWidgets.QMessageBox.Yes)
    w.calc.clear_workdir()
    assert called == [True] and (tmp_path / "valuable").read_text() == "keep"
    assert w.close()


def test_partial_clear_does_not_recreate_removed_record(qapp, tmp_path, no_dialogs, monkeypatch):
    config.install(cfg([job()]), str(tmp_path))
    (tmp_path / "result").write_text("result")
    w = window(tmp_path)

    def choose(d):
        d.all_files.setChecked(True)
        d.confirmation.setText(tmp_path.name)
        d.accept()
        return d.result()

    def fail_after_record(plan, *args):
        (tmp_path / core.SESSION_FILE).unlink()
        raise OSError("simulated deletion failure")

    monkeypatch.setattr(panel.ClearDialog, "exec", choose)
    monkeypatch.setattr(work, "clear", fail_after_record)
    w.calc.clear_workdir()
    assert len(no_dialogs) == 1 and "部分项目" in no_dialogs[0][1][1]
    assert w.calc.pipeline == []
    assert w.close() and not (tmp_path / core.SESSION_FILE).exists()
    assert (tmp_path / "result").read_text() == "result"



def test_legacy_directory_all_and_none(qapp, tmp_path):
    config.install(cfg([job()]), str(tmp_path))
    (tmp_path / config.INPUTS_FILE).unlink()
    for i in range(40):
        (tmp_path / f"result-{i}.cm").write_bytes(b"result")
    d = panel.ClearDialog(None, str(tmp_path))
    assert d.plan["unknown_inputs"] and not d.selected_names() and not d.ok.isEnabled()
    d.select_all.click()
    assert len(d.selected_names()) == 40 and d.ok.isEnabled()
    assert core.SESSION_FILE not in d.selected_names()
    d.select_none.click()
    assert not d.selected_names() and not d.ok.isEnabled()


@pytest.mark.parametrize("size,text", [(0, "0.0 B"), (512, "512.0 B"), (1024, "1.0 KB"),
                                      (int(2.5 * 1024 ** 2), "2.5 MB"), (int(6.2 * 1024 ** 3), "6.2 GB")])
def test_clear_readable_size(qapp, tmp_path, size, text):
    config.install(cfg([job()]), str(tmp_path))
    # 稀疏文件只用于核对显示，不写入或读取数 GB 的内容。
    with (tmp_path / "result.cm").open("wb") as f:
        f.truncate(size)
    d = panel.ClearDialog(None, str(tmp_path))
    assert text in d.files.item(0).text()
    assert text in d.total.text()
