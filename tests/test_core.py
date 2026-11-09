"""与界面无关的规则：翻译完整、核心模块只用标准库、代码能在 Python 3.10 上运行、各程序页的默认参数能生成任务。"""

import ast
import glob
import os
import re
import subprocess
import sys

import pytest

import grasp_config as config  # noqa: E402
import grasp_core as core
from conftest import ROOT

SOURCES = sorted(glob.glob(os.path.join(ROOT, "*.py")))
CJK = re.compile(r"[一-鿿]")
# 不能导入 Qt 的模块：命令行工具和后台运行进程在没有图形库的服务器上也要能用
STDLIB_ONLY = ["grasp_core", "grasp_config", "grasp_bg", "grasp_wfn", "grasp_mix", "grasp_trans",
               "grasp_index", "grasp_workdir", "grasp_session", "grasp_service", "grasp_mcp", "grasp_render",
               "grasp_remote", "grasp_ssh", "grasp_askpass", "grasp_summary", "grasp_server", "grasp_transfer",
               "grasp_install", "grasp_slurm", "grasp_remote_edit"]


def t_literals():
    """所有源文件中 T("...") 的中文文字：[(文件, 行, 文字)]。"""
    out = []
    for path in SOURCES:
        with open(path, encoding="utf-8") as f:
            tree = ast.parse(f.read())
        for n in ast.walk(tree):
            if (isinstance(n, ast.Call) and getattr(n.func, "id", None) == "T" and n.args
                    and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str)
                    and CJK.search(n.args[0].value)):
                out.append((os.path.basename(path), n.lineno, n.args[0].value))
    return out


def form_texts():
    """程序页上显示的文字：页名、分组名、字段名、说明、选项。"""
    out = [g for g, _ in core.STEP_GROUPS]
    for s in core.STEPS:
        out.append(s.name)
        for f in s.fields:
            out += [getattr(f, "text", ""), getattr(f, "label", ""), getattr(f, "hint", "")]
            out += [o[1] for o in getattr(f, "options", [])]
    for _, label, hint in core.CsfLists.LIST_FIELDS:
        out += [label, hint]
    return [t for t in out if t and CJK.search(t)]


def test_every_t_text_has_english():
    missing = [f"{p}:{line}  {s!r}" for p, line, s in t_literals() if s not in core.EN]
    assert not missing, "add to grasp_core.EN:\n" + "\n".join(missing)


def test_every_form_text_has_english():
    missing = sorted({t for t in form_texts() if t not in core.EN})
    assert not missing, "add to grasp_core.EN:\n" + "\n".join(missing)


def test_english_placeholders_match():
    """英文翻译的 {占位符} 与中文一致，否则切换到英文后 format 出错。"""
    names = re.compile(r"\{(\w*)\}")
    bad = [k for k, v in core.EN.items() if sorted(names.findall(k)) != sorted(names.findall(v))]
    assert not bad, "\n".join(bad)


@pytest.mark.parametrize("module", STDLIB_ONLY)
def test_module_does_not_import_qt(module):
    code = ("import sys; sys.modules['PySide6'] = None; sys.modules['PySide2'] = None\n"
            f"import {module}")
    r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


@pytest.mark.parametrize("path", SOURCES, ids=os.path.basename)
def test_python_310_syntax(path):
    """旧系统版用 Python 3.10 打包：不能用 3.11 以后的语法。"""
    with open(path, encoding="utf-8") as f:
        ast.parse(f.read(), filename=path, feature_version=(3, 10))


@pytest.mark.parametrize("group", core.GROUPS)
@pytest.mark.parametrize("step", core.STEPS, ids=lambda s: s.key)
def test_defaults_make_a_job(step, group):
    vals = core.group_defaults(group).get(step.key, step.defaults())
    job = step.job(vals)
    assert job["prog"] and isinstance(job["stdin"], str)
    assert job["step"] == step.key


def test_step_keys_unique():
    keys = [s.key for s in core.STEPS]
    assert len(keys) == len(set(keys))


def test_normalize_old_choice_values():
    """旧版本把下拉框的显示文字一起保存 (如 "5 标准 (EOL)")：只取代码。"""
    choice = next(f for s in core.STEPS for f in s.fields if isinstance(f, core.Choice))
    code = choice.options[-1][0]
    assert core.normalize(choice, f"{code} {choice.options[-1][1]}") == code
    assert core.normalize(choice, "no such code") == choice.default


def test_rename_group():
    assert core.rename_group({"a": ["odd2.c", "outodd_rci"]}, "odd", "even") == {"a": ["even2.c", "outeven_rci"]}


@pytest.mark.parametrize("step,key,limit", [
    ("jj2lsj", "state", 23), ("rhfs", "state", 23), ("ris4", "state", 23), ("rhfszeeman95", "state", 23),
    ("rbiotransform", "init", 23), ("rbiotransform", "final", 23), ("rtransition", "init", 23),
    ("rtransition", "final", 23), ("rmixextract", "state", 64), ("rci", "state", 127)])
def test_state_name_limits_from_grasp_sources(step, key, limit):
    """GRASP 读入态名的字符变量有长度上限 (docs/CONFIG_FORMAT.md 的“名称长度”)；超限的配置在检查时报错。"""
    s = config.STEPS[step]
    v = s.defaults()
    v[key] = "a" * limit
    s.validate(v)
    v[key] = "a" * (limit + 1)
    with pytest.raises(ValueError):
        s.validate(v)
    v[key] = "odd 2"
    with pytest.raises(ValueError):
        s.validate(v)


def test_long_jj2lsj_state_rejected_on_check():
    """agent 写出 24 个字符的态名时，导入检查就报错，不等 jj2lsj 打开错误的文件。"""
    vals = dict(config.STEPS["jj2lsj"].defaults(), state="rn2_odd_cv6_low_corr_qed")
    doc = dict(format=config.FORMAT, version=2, jobs=[dict(step="jj2lsj", vals=vals)])
    with pytest.raises(config.ConfigError, match="23"):
        config.check(doc)


@pytest.mark.parametrize("step,key,limit", [("rcsfzerofirst", "zero", 256), ("rtabtrans2", "tfile", 80),
                                            ("rlevels", "files", 80)])
def test_file_name_limits_from_grasp_sources(step, key, limit):
    s = config.STEPS[step]
    v = s.defaults()
    v[key] = "a" * limit
    s.validate(v)
    v[key] = "a" * (limit + 1)
    with pytest.raises(ValueError):
        s.validate(v)
