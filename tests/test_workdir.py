"""配置更新保留结果、按内容比较步骤，并能备份恢复。"""

from pathlib import Path

import pytest

import grasp_config as config
import grasp_core as core
import grasp_workdir as work


def cfg(jobs, files=()):
    checked, warnings = config.check(dict(format=config.FORMAT, version=config.VERSION, jobs=jobs, files=list(files)))
    assert not warnings
    return checked


def job(step="rnucleus", **vals):
    return dict(step=step, vals=dict(config.STEPS[step].defaults(), **vals))


def session(jobs):
    return config.session(cfg(jobs))


def test_sequence_differences():
    a, b, c = job(), job("rsave"), job("rangular")
    old = session([a, b, c])
    appended = work.compare(old, session([a, b, c, job("rci")]))
    assert appended["append_from"] == 3 and appended["first"] == 3
    assert "前面 3 步不变，新增 1 步" in appended["text"]
    inserted = work.compare(old, session([a, job("ris4"), b, c]))
    assert inserted["first"] == 1 and inserted["append_from"] is None
    assert "新增：第 2 步 ris4" in inserted["text"]
    assert "修改：第 3" not in inserted["text"]
    deleted = work.compare(old, session([a, c]))
    assert "删除：原第 2 步 rsave" in deleted["text"]
    moved = work.compare(old, session([c, a, b]))
    assert "移动：" in moved["text"]
    replaced = work.compare(old, session([a, job("ris4"), c]))
    assert "删除：原第 2 步" in replaced["text"] and "新增：第 2 步" in replaced["text"]


def test_parameter_group_and_old_defaults():
    a = job("rmcdhf", vary="5*")
    b = job("rmcdhf", vary="5s,5p*")
    changes = work.compare(session([a]), session([b]))
    assert "5*" in changes["text"] and "5s,5p*" in changes["text"]
    b["group"] = "even"
    assert "态组" in work.compare(session([a]), session([b]))["text"]
    old = session([a])
    old["pipeline"][0]["vals"].pop("modify_nsic")
    assert work.compare(old, session([a]))["first"] is None
    old["steps"]["rnucleus"] = dict(core.RNucleus.defaults(), z="99")
    assert "程序页参数的其他改动" in work.compare(old, session([a]))["text"]


def test_update_files_backup_and_restore(tmp_path):
    original = cfg([job()], [dict(name="same.in", text="same\n"), dict(name="keep.in", text="original\n")])
    config.install(original, str(tmp_path))
    result = tmp_path / "state.cm"
    result.write_bytes(b"result\x00unchanged")
    initial = (tmp_path / core.SESSION_FILE).read_bytes()
    revised = cfg([job(z="4"), job("rangular")], [dict(name="same.in", text="same\n"),
                  dict(name="keep.in", text="revised\n"), dict(name="new.in", text="new\n")])
    plan = work.prepare_update(tmp_path, revised)
    assert [x["kind"] for x in plan["files"]] == ["same", "conflict", "new"]
    work.apply_update(plan)
    assert result.read_bytes() == b"result\x00unchanged"
    assert (tmp_path / "keep.in").read_text() == "original\n"
    assert (tmp_path / "new.in").read_text() == "new\n"
    assert work.backups(tmp_path)[0].read_bytes() == initial
    work.restore(tmp_path)
    assert (tmp_path / core.SESSION_FILE).read_bytes() == initial
    plan = work.prepare_update(tmp_path, revised)
    work.apply_update(plan, {"keep.in"})
    assert (tmp_path / "keep.in").read_text() == "revised\n"
    assert result.read_bytes() == b"result\x00unchanged"
    for z in range(6, 12):
        work.apply_update(work.prepare_update(tmp_path, cfg([job(z=str(z))])))
    assert len(work.backups(tmp_path)) == work.BACKUPS


def test_update_rejects_running_and_changed_files(tmp_path, monkeypatch):
    config.install(cfg([job()]), str(tmp_path))
    revised = cfg([job(z="4")], [dict(name="input", text="new")])
    plan = work.prepare_update(tmp_path, revised)
    (tmp_path / "input").write_text("unreviewed file")
    with pytest.raises(ValueError, match="已改变"):
        work.apply_update(plan)
    monkeypatch.setattr(work.bg, "status", lambda p: dict(status="running"))
    with pytest.raises(ValueError, match="运行"):
        work.prepare_update(tmp_path, revised)
    with pytest.raises(ValueError, match="运行"):
        work.restore(tmp_path)


def test_update_does_not_follow_input_links(tmp_path):
    root = tmp_path / "calc"
    outside = tmp_path / "outside"
    outside.write_text("keep outside")
    config.install(cfg([job()]), str(root))
    (root / "input").symlink_to(outside)
    revised = cfg([job(z="4")], [dict(name="input", text="new")])
    plan = work.prepare_update(root, revised)
    work.apply_update(plan)
    assert (root / "input").is_symlink()
    work.apply_update(work.prepare_update(root, revised), {"input"})
    assert not (root / "input").is_symlink()
    assert outside.read_text() == "keep outside"


def test_clear_modes_inputs_and_links(tmp_path):
    root, outside = tmp_path / "calc", tmp_path / "outside"
    outside.mkdir()
    (outside / "valuable").write_text("keep")
    config.install(cfg([job()], [dict(name="input", text="keep input")]), str(root))
    (root / "state.cm").write_bytes(b"result")
    (root / "link").symlink_to(outside, target_is_directory=True)
    (root / ".grasp_gui").mkdir()
    (root / ".grasp_gui" / "cache").write_bytes(b"cache")
    (root / ".grasp_gui_run.log").write_text("output")
    plan = work.prepare_clear(root)
    assert {x["name"] for x in plan["kept"]} == {"input", core.SESSION_FILE, config.INPUTS_FILE}
    work.clear(plan, {x["name"] for x in plan["entries"] if x["name"] != "state.cm"})
    assert (root / "state.cm").read_bytes() == b"result"
    assert not (root / "link").exists()
    assert (outside / "valuable").read_text() == "keep"
    assert not (root / ".grasp_gui").exists()
    all_plan = work.prepare_clear(root, all_files=True)
    with pytest.raises(ValueError, match="目录名"):
        work.clear(all_plan, {x["name"] for x in all_plan["entries"]})
    work.clear(all_plan, {x["name"] for x in all_plan["entries"]}, "calc")
    assert list(root.iterdir()) == []
    assert (outside / "valuable").read_text() == "keep"


def test_clear_rejects_unsafe_or_changed(tmp_path, monkeypatch):
    for forbidden in ("/", str(Path.home()), "/home", "/tmp", "/etc/ssh"):
        with pytest.raises(ValueError, match="拒绝"):
            work.prepare_clear(forbidden, all_files=True, allow_untracked=True)
    root = tmp_path / "calc"
    root.mkdir()
    with pytest.raises(ValueError, match="记录"):
        work.prepare_clear(root)
    config.install(cfg([job()]), str(root))
    result = root / "result"
    result.write_text("result")
    plan = work.prepare_clear(root)
    result.unlink()
    result.symlink_to(tmp_path)
    with pytest.raises(ValueError, match="已改变"):
        work.clear(plan, {"result"})
    monkeypatch.setattr(work.bg, "status", lambda p: dict(status="running"))
    with pytest.raises(ValueError, match="运行"):
        work.prepare_clear(root)
    with pytest.raises(ValueError, match="运行"):
        work.clear(plan, {"result"})


def test_unknown_inputs_are_not_guessed(tmp_path):
    config.install(cfg([job()]), str(tmp_path))
    (tmp_path / "input.txt").write_text("must preserve")
    (tmp_path / config.INPUTS_FILE).unlink()
    plan = work.prepare_clear(tmp_path)
    assert plan["unknown_inputs"]
    assert not next(x for x in plan["entries"] if x["name"] == "input.txt")["checked"]


def test_failed_update_rolls_back_inputs(tmp_path, monkeypatch):
    config.install(cfg([job()], [dict(name="input", text="old")]), str(tmp_path))
    original = (tmp_path / core.SESSION_FILE).read_bytes()
    plan = work.prepare_update(tmp_path, cfg([job(z="4")], [dict(name="input", text="new")]))
    atomic = work._atomic

    def fail_record(path, raw, mode=None):
        if Path(path).name == core.SESSION_FILE:
            raise OSError("simulated write error")
        return atomic(path, raw, mode)

    monkeypatch.setattr(work, "_atomic", fail_record)
    with pytest.raises(OSError):
        work.apply_update(plan, {"input"})
    assert (tmp_path / "input").read_text() == "old"
    assert (tmp_path / core.SESSION_FILE).read_bytes() == original



def test_update_checks_record_and_duplicate_inputs(tmp_path):
    config.install(cfg([job()]), str(tmp_path))
    revised = cfg([job(z="4")])
    plan = work.prepare_update(tmp_path, revised)
    core.save_session(str(tmp_path), session([job(z="6")]))
    with pytest.raises(ValueError, match="记录已改变"):
        work.apply_update(plan)
    revised["files"] = [dict(name="input", text="a"), dict(name="input", text="b")]
    with pytest.raises(ValueError, match="重复"):
        work.prepare_update(tmp_path, revised)


def test_update_preserves_mode_and_unknown_old_inputs(tmp_path):
    config.install(cfg([job()], [dict(name="input", text="old")]), str(tmp_path))
    (tmp_path / "input").chmod(0o640)
    plan = work.prepare_update(tmp_path, cfg([job(z="4")], [dict(name="input", text="new")]))
    work.apply_update(plan, {"input"})
    assert (tmp_path / "input").stat().st_mode & 0o777 == 0o640
    clear = work.prepare_clear(tmp_path)
    assert not clear["unknown_inputs"]
    assert {x["name"] for x in clear["kept"]} >= {"input", core.SESSION_FILE, work.backups(tmp_path)[0].name}



def test_clear_refuses_directory_swapped_for_link(tmp_path, monkeypatch):
    root, outside = tmp_path / "calc", tmp_path / "outside"
    outside.mkdir()
    (outside / "valuable").write_text("keep")
    config.install(cfg([job()]), str(root))
    (root / "results").mkdir()
    (root / "results" / "output").write_text("result")
    plan = work.prepare_clear(root)
    open_file = work.os.open
    swapped = False

    def swap(name, flags, *args, **kwargs):
        nonlocal swapped
        if name == "results" and not swapped:
            swapped = True
            (root / "results").rename(root / "moved")
            (root / "results").symlink_to(outside, target_is_directory=True)
        return open_file(name, flags, *args, **kwargs)

    monkeypatch.setattr(work.os, "open", swap)
    with pytest.raises(OSError):
        work.clear(plan, {"results"})
    assert (outside / "valuable").read_text() == "keep"
    assert (root / "moved" / "output").read_text() == "result"



def test_input_manifest_update_restore_and_stale_preview(tmp_path):
    config.install(cfg([job()], [dict(name="old.in", text="keep")]), str(tmp_path))
    assert config.load_inputs(tmp_path) == {"old.in"}
    revised = cfg([job(z="4")], [dict(name="new.in", text="new")])
    work.apply_update(work.prepare_update(tmp_path, revised))
    assert config.load_inputs(tmp_path) == {"old.in", "new.in"}
    saved_manifest = (tmp_path / config.INPUTS_FILE).read_bytes()
    work.restore(tmp_path)
    assert (tmp_path / config.INPUTS_FILE).read_bytes() == saved_manifest
    assert (tmp_path / "old.in").read_text() == "keep" and (tmp_path / "new.in").read_text() == "new"
    before_update = work.prepare_update(tmp_path, revised)
    before_clear = work.prepare_clear(tmp_path)
    config.save_inputs(tmp_path, {"old.in", "new.in", "other.in"})
    with pytest.raises(ValueError, match="清单已改变"):
        work.apply_update(before_update)
    with pytest.raises(ValueError, match="清单已改变"):
        work.clear(before_clear, set())


def test_manifest_failure_rolls_back_inputs_and_record(tmp_path, monkeypatch):
    config.install(cfg([job()], [dict(name="input", text="old")]), str(tmp_path))
    record = (tmp_path / core.SESSION_FILE).read_bytes()
    manifest = (tmp_path / config.INPUTS_FILE).read_bytes()
    plan = work.prepare_update(tmp_path, cfg([job(z="4")], [dict(name="input", text="new")]))
    atomic = work._atomic

    def fail_manifest(path, raw, mode=None):
        if Path(path).name == config.INPUTS_FILE:
            raise OSError("simulated manifest write failure")
        return atomic(path, raw, mode)

    monkeypatch.setattr(work, "_atomic", fail_manifest)
    with pytest.raises(OSError):
        work.apply_update(plan, {"input"})
    assert (tmp_path / "input").read_text() == "old"
    assert (tmp_path / core.SESSION_FILE).read_bytes() == record
    assert (tmp_path / config.INPUTS_FILE).read_bytes() == manifest


def test_restore_twice_returns_to_current_record(tmp_path):
    config.install(cfg([job()]), str(tmp_path))
    first = (tmp_path / core.SESSION_FILE).read_bytes()
    work.apply_update(work.prepare_update(tmp_path, cfg([job(z="4")])))
    second = (tmp_path / core.SESSION_FILE).read_bytes()
    work.restore(tmp_path)
    assert (tmp_path / core.SESSION_FILE).read_bytes() == first
    work.restore(tmp_path)
    assert (tmp_path / core.SESSION_FILE).read_bytes() == second
