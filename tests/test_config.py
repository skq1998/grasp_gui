"""共享配置与命令行工具：算例能导入，生成的脚本与保存的标准结果一致，导出 → 导入不变。"""

import glob
import json
import os
import re

import pytest

import grasp_config as config
import grasp_core as core
from conftest import DEMOS, GOLDEN, ROOT

DEMO_FILES = sorted(glob.glob(os.path.join(DEMOS, "*.json")))
DEMO_NAMES = [os.path.splitext(os.path.basename(p))[0] for p in DEMO_FILES]
BINDIR = "/opt/grasp/bin"            # 标准结果中的程序目录 (只是文字)
MPI = "mpirun -np 4"


def script(cfg, mpi=""):
    return core.shell_script(config.session(cfg)["pipeline"], BINDIR, ".", mpi) + "\n"


@pytest.mark.parametrize("name", DEMO_NAMES)
def test_demo_checks_without_warnings(name):
    cfg, warnings = config.load(os.path.join(DEMOS, name + ".json"))
    assert warnings == []


@pytest.mark.parametrize("mpi", ["", MPI], ids=["serial", "mpi"])
@pytest.mark.parametrize("name", DEMO_NAMES)
def test_demo_script_matches_golden(name, mpi):
    """生成的程序输入不能在无意中改变。有意修改输入规则时，用 UPDATE_GOLDEN=1 重新生成标准结果，
    并在提交说明中写明改变的原因和核对方法 (见 AGENTS.md)。"""
    cfg, _ = config.load(os.path.join(DEMOS, name + ".json"))
    path = os.path.join(GOLDEN, f"{name}{'.mpi' if mpi else ''}.sh")
    out = script(cfg, mpi)
    if os.environ.get("UPDATE_GOLDEN"):
        os.makedirs(GOLDEN, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(out)
    with open(path, encoding="utf-8") as f:
        assert out == f.read()


@pytest.mark.parametrize("name", DEMO_NAMES)
def test_export_import_round_trip(name):
    cfg, _ = config.load(os.path.join(DEMOS, name + ".json"))
    exported = config.export(config.session(cfg)["pipeline"], dict(title="t"))
    again, warnings = config.check(json.loads(json.dumps(exported)))
    assert warnings == []
    assert script(again) == script(cfg)


def test_version_1_is_upgraded():
    cfg, _ = config.load(os.path.join(DEMOS, "example1.json"))
    old = {k: v for k, v in cfg.items() if k not in ("format", "version", "package")}
    old["demo_format"] = 1
    upgraded, warnings = config.check(old)
    assert (upgraded["format"], upgraded["version"], upgraded["package"]) == (config.FORMAT, 2, "GRASP2018")
    assert script(upgraded) == script(cfg)


def test_missing_new_fields_take_defaults_silently():
    """新版本增加的字段 (added="版本号") 缺少时不产生警告：旧配置照样导入。"""
    added = [(s.key, f.key) for s in core.STEPS for f in s.fields if getattr(f, "added", False)]
    assert added, "no fields marked added"
    for s in core.STEPS:
        for f in s.fields:
            if getattr(f, "added", False):
                assert re.fullmatch(r"\d+\.\d+\.\d+", str(f.added)), f"{s.key}.{f.key}: added must be a version string"
    for step, key in added:
        vals = config.STEPS[step].defaults()
        vals.pop(key)
        doc = dict(format=config.FORMAT, version=2, package=core.package_of([step]),
                   jobs=[dict(step=step, group="odd", vals=vals)])
        assert config.check(doc)[1] == []


@pytest.mark.parametrize("doc", [
    [], {"format": "other"}, {"format": config.FORMAT, "version": 99, "jobs": [{}]},
    {"format": config.FORMAT, "version": 2, "jobs": []},
    {"format": config.FORMAT, "version": 2, "jobs": [{"step": "nosuchstep", "vals": {}}]},
    {"format": config.FORMAT, "version": 2, "package": "OTHER", "jobs": [{"step": "rnucleus", "vals": {}}]},
    {"format": config.FORMAT, "version": 2, "jobs": [{"step": "rnucleus", "vals": {}}],
     "files": [{"name": "../x", "text": ""}]},
])
def test_bad_configs_are_rejected(doc):
    with pytest.raises(config.ConfigError):
        config.check(doc)


def test_steps_md_is_up_to_date():
    with open(os.path.join(ROOT, "docs", "STEPS.md"), encoding="utf-8") as f:
        assert f.read().rstrip("\n") == config.steps_markdown().rstrip("\n"), \
            "docs/STEPS.md is out of date: python3 grasp_config.py steps > docs/STEPS.md"


def test_cli_exit_codes(tmp_path, capsys):
    assert config.cli(["--help"]) == 0
    assert config.cli([]) == 2
    assert config.cli(["nosuchcommand"]) == 2
    assert config.cli(["check", str(tmp_path / "missing.json")]) == 1
    assert config.cli(["check", os.path.join(DEMOS, "example1.json")]) == 0
    assert config.cli(["script", os.path.join(DEMOS, "example1.json"), "--bindir", BINDIR]) == 0
    capsys.readouterr()
    assert config.cli(["steps"]) == 0
    out = capsys.readouterr().out
    assert all(f"`{s.key}`" in out for s in core.STEPS)



def test_import_persists_only_valid_input_names(tmp_path):
    cfg, _ = config.load(os.path.join(DEMOS, "example1.json"))
    cfg["files"] = [dict(name="extra.in", text="preserve this input")]
    config.install(cfg, str(tmp_path))
    assert json.loads((tmp_path / config.INPUTS_FILE).read_text()) == ["extra.in"]
    assert config.load_inputs(tmp_path) == {"extra.in"}
    assert core.load_session(tmp_path) == json.loads(json.dumps(config.session(cfg)))
    original = (tmp_path / config.INPUTS_FILE).read_bytes()
    with pytest.raises(config.ConfigError):
        config.save_inputs(tmp_path, ["../outside"])
    assert (tmp_path / config.INPUTS_FILE).read_bytes() == original


@pytest.mark.parametrize("content", ["{}", "null", "[1]", json.dumps(["../outside"]), json.dumps([".hidden"]), "not JSON"])
def test_invalid_input_manifest_is_refused(tmp_path, content):
    (tmp_path / config.INPUTS_FILE).write_text(content)
    with pytest.raises(config.ConfigError, match="输入清单"):
        config.load_inputs(tmp_path)


def test_input_manifest_missing_or_link(tmp_path):
    assert config.load_inputs(tmp_path) is None
    outside = tmp_path / "outside"
    outside.write_text(json.dumps(["input"]))
    (tmp_path / config.INPUTS_FILE).symlink_to(outside)
    with pytest.raises(OSError):
        config.load_inputs(tmp_path)
    assert json.loads(outside.read_text()) == ["input"]
