"""自定义轨道顺序、各执行入口与真实 GRASP 对照。"""

import json
import os
from pathlib import Path
import subprocess

import pytest

import grasp_bg as bg
import grasp_config as config
import grasp_core as core
import grasp_summary as summary
from conftest import DEMOS
from test_bg import messages, run_cfg


@pytest.fixture
def ordered_job():
    step = core.RcsfGenerate()
    return step.job(dict(step.defaults(), order="u", orbital_order=" 2p,2s\n1s ", saveas=""))


def test_config_and_preview(tmp_path, ordered_job):
    doc = config.export([dict(ordered_job, group="odd")], dict(title="u"))
    restored = config.make_job(config.loads(json.dumps(doc))[0]["jobs"][0])
    assert restored["pre"] == [("write", "clist.ref", "2p\n2s\n1s\n")]
    assert restored["stdin"].splitlines()[0] == "u"
    assert not summary.check_files(str(tmp_path), [restored])["issues"]
    preview = core.describe(restored)
    assert preview.index("2p\n2s\n1s\n") < preview.index("rcsfgenerate <<'EOF'\nu\n")
    core.set_lang("en")
    assert core.describe(restored) == preview
    invalid = dict(doc, jobs=[dict(command=dict(prog="true", pre=[list(restored["pre"][0])]))])
    with pytest.raises(config.ConfigError):
        config.check(invalid)


@pytest.mark.parametrize("order", ["*", "r", "s"])
def test_other_order_preserves_stdin_and_actions(order):
    step = core.RcsfGenerate()
    vals = dict(step.defaults(), order=order, orbital_order="invalid")
    step.validate(vals)
    job = step.job(vals)
    assert job["pre"] == []
    assert job["stdin"] == f"{order}\n0\n1s(2,i)2s(2,i)2p(1,i)\n1s(2,i)2p(3,i)\n\n2s,2p\n1,3\n0\nn\n"


@pytest.mark.parametrize("value,message", [
    ("", "至少指定一个"), ("2p-", "写法无效"), ("1s 2p", "写法无效"), ("11j", "写法无效"),
    ("0s", "写法无效"), ("16s", "n ≤ 15"), ("2d", "l < n"), (" 2p,\n2p ", "重复"),
])
def test_invalid_order(value, message):
    step = core.RcsfGenerate()
    vals = dict(step.defaults(), order="u", orbital_order=value)
    with pytest.raises(ValueError, match=message):
        step.validate(vals)
    with pytest.raises(ValueError, match=message):
        step.job(vals)


def test_two_digit_orbitals():
    step = core.RcsfGenerate()
    assert step.pre(dict(order="u", orbital_order="10m,11n,15s")) == [("write", "clist.ref", "10m\n11n\n15s\n")]


@pytest.mark.parametrize("mode", ["runner", "background", "shell", "slurm"])
def test_write_before_program(tmp_path, ordered_job, mode):
    stub = tmp_path / "rcsfgenerate"
    stub.write_text('#!/bin/sh\n[ "$(head -1 clist.ref)" = 2p ] && cat clist.ref > rcsf.out\n')
    stub.chmod(0o755)
    (tmp_path / "clist.ref").write_text("old\n")
    if mode == "runner":
        core.Runner(lambda *_: None).run_job(ordered_job, run_cfg(tmp_path))
    elif mode == "background":
        proc = bg.launch(str(tmp_path), [ordered_job], run_cfg(tmp_path))
        with bg.connect(str(tmp_path), timeout=10) as sock:
            assert messages(sock)[-1] == {"done": "ok"}
        assert proc.wait(10) == 0
    else:
        if mode == "shell":
            script = core.shell_script([ordered_job], str(tmp_path), str(tmp_path))
        else:
            script = core.slurm_script([ordered_job], dict(bindir=str(tmp_path), modules=""))
        env = dict(os.environ, SLURM_SUBMIT_DIR=str(tmp_path))
        result = subprocess.run(["bash", "-c", script], cwd=tmp_path, env=env,
                                capture_output=True, text=True, timeout=10)
        assert result.returncode == 0, result.stderr
    assert (tmp_path / "rcsf.out").read_text() == "2p\n2s\n1s\n"
    assert not list(tmp_path.glob(".grasp-write*"))


@pytest.mark.parametrize("name", ["", ".", "..", "a..b", "../bad", "a/b", "a\\b", "bad\0name"])
def test_write_rejects_non_basename(tmp_path, name):
    action = ("write", name, "test\n")
    with pytest.raises(ValueError, match="文件名无效"):
        core.action_sh(action)
    with pytest.raises(ValueError, match="文件名无效"):
        core.Runner(lambda *_: None).do_action(action, run_cfg(tmp_path))


@pytest.mark.parametrize("content", ["", "$HOME\nGRASP_WRITE\n`echo no`\n\n", "missing newline"])
def test_write_shell_preserves_text(tmp_path, content):
    action = ("write", "quotes' and spaces", content)
    subprocess.run(["sh", "-c", core.action_sh(action)], cwd=tmp_path, check=True, timeout=10)
    assert (tmp_path / action[1]).read_text() == content


def test_write_failure_does_not_start_program(tmp_path, ordered_job):
    (tmp_path / "original").write_text("old\n")
    (tmp_path / "clist.ref").symlink_to("original")
    job = dict(ordered_job, prog="touch", args=["ran"], stdin="")
    with pytest.raises(ValueError, match="不是普通文件"):
        core.Runner(lambda *_: None).run_job(job, run_cfg(tmp_path))
    script = core.shell_script([job], str(tmp_path), str(tmp_path))
    result = subprocess.run(["sh", "-c", script], cwd=tmp_path, capture_output=True, timeout=10)
    assert result.returncode != 0
    assert (tmp_path / "original").read_text() == "old\n"
    assert (tmp_path / "clist.ref").is_symlink()
    assert not (tmp_path / "ran").exists()


@pytest.mark.grasp
def test_li_order_matches_handwritten_grasp(grasp_bindir, tmp_path):
    cfg, _ = config.load(str(Path(DEMOS) / "example1.json"))
    raw = cfg["jobs"][1]
    form = config.make_job(dict(raw, vals=dict(raw["vals"], order="u", orbital_order="2p,2s,1s")))
    outputs = {}
    hand = "u\n0\n1s(2,i)2s(1,i)\n\n2s,2p\n1,1\n0\ny\n1s(2,i)2p(1,i)\n\n2s,2p\n1,3\n0\nn\n"
    for mode in ("form", "hand", "default"):
        wd = tmp_path / mode
        wd.mkdir()
        if mode == "form":
            core.Runner(lambda *_: None).run_job(form, dict(run_cfg(wd), bindir=grasp_bindir))
        else:
            if mode == "hand":
                (wd / "clist.ref").write_text("2p\n2s\n1s\n")
            result = subprocess.run([str(Path(grasp_bindir) / "rcsfgenerate")], cwd=wd,
                                    input=hand if mode == "hand" else "*\n" + hand[2:],
                                    capture_output=True, text=True, env=core.child_env(), timeout=60)
            assert result.returncode == 0, result.stderr
        outputs[mode] = (wd / "rcsf.out").read_bytes()
    assert outputs["form"] == outputs["hand"]
    def header(mode):
        lines = outputs[mode].splitlines()
        return lines[1].split() + lines[3].split()

    assert header("default") == [b"1s", b"2s", b"2p-", b"2p"]
    assert header("form") == [b"2p-", b"2p", b"2s", b"1s"]
