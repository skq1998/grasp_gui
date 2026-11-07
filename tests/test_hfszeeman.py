"""rhfszeeman95 默认分支的四问，以及与已有自定义输入的对照。"""

from pathlib import Path

import pytest

import grasp_config as config
import grasp_core as core


@pytest.mark.parametrize("ci", [False, True])
@pytest.mark.parametrize("offdiag", [False, True])
def test_hfszeeman_input(ci, offdiag):
    step = core.RHfsZeeman95()
    v = dict(step.defaults(), state=" state ", ci=ci, offdiag=offdiag)
    assert step.stdin(v) == f"y\nstate\n{'y' if ci else 'n'}\n{'y' if offdiag else 'n'}\n"
    assert step.post(v) == []
    v["saveas"] = " constants.txt "
    assert step.post(v) == [("cp", "state.ch" if ci else "state.h", "constants.txt")]


@pytest.mark.parametrize("state", ["", " ", "a b", "../state", "/state", "-state", "态", "a;echo", "a\nb", "x" * 24])
def test_hfszeeman_invalid_state(state):
    step = core.RHfsZeeman95()
    v = dict(step.defaults(), state=state)
    with pytest.raises(ValueError):
        step.validate(v)
    with pytest.raises(ValueError):
        step.stdin(v)


def test_hfszeeman_registration_and_fields():
    step = core.RHfsZeeman95()
    assert step.stdin(dict(step.defaults(), state="x" * 23)).splitlines()[1] == "x" * 23
    assert step.defaults()["ci"] is True
    assert step.defaults()["offdiag"] is True
    assert step.key in core.GROUP_STEPS
    keys = next(keys for _, keys in core.STEP_GROUPS["GRASP2018"] if "rhfs" in keys)     # v3：按程序包分组
    assert keys[keys.index("rhfs") + 1] == step.key
    assert all(f.added == "2.5.0" for f in step.fields if isinstance(f, core.Field))
    cfg, warnings = config.check(dict(format=config.FORMAT, version=config.VERSION,
                                      jobs=[dict(step=step.key, vals={})]))
    assert not warnings
    assert config.session(cfg)["pipeline"][0]["stdin"] == step.stdin(step.defaults())


def test_example9_uses_hfszeeman_form():
    """example9 原来的两个自定义步骤已换成程序页，stdin 与原来的手册输入逐字相同。"""
    cfg, _ = config.load(str(Path(__file__).resolve().parents[1] / "demos/example9.json"))
    step = core.RHfsZeeman95()
    jobs = [job for job in cfg["jobs"] if job["step"] == "rhfszeeman95"]
    assert [(job["group"], job["vals"]["state"]) for job in jobs] == [("even", "even_n3"), ("odd", "odd_n3")]
    for job in jobs:
        state = job["vals"]["state"]
        assert step.stdin(config.job_values(job)) == f"y\n{state}\ny\ny\n"
    assert not any(job["step"] == "custom" and job["vals"]["prog"] == "rhfszeeman95" for job in cfg["jobs"])
