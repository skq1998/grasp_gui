"""四个工具的源码输入顺序和参数边界。"""

import pytest

import grasp_core as core


@pytest.mark.parametrize("scope", ["0", "1"])
@pytest.mark.parametrize("ci, sort", [(False, False), (True, True), (False, True), (True, False)])
def test_mixextract(scope, ci, sort):
    step = core.RMixExtract()
    v = dict(step.defaults(), state="state", scope=scope, ci=ci, cutoff="0.01", sort=sort)
    assert step.stdin(v) == f"{scope}\nstate\n{'y' if ci else 'n'}\n0.01\n{'y' if sort else 'n'}\n"
    v["saveas"] = "reduced.c"
    assert step.post(v) == [("cp", "rcsf.out", "reduced.c")]


@pytest.mark.parametrize("ci, sort", [(False, False), (True, True), (False, True), (True, False)])
def test_mixaccumulate(ci, sort):
    step = core.RMixAccumulate()
    v = dict(step.defaults(), state="state", ci=ci, fraction="0.9999", sort=sort)
    assert step.stdin(v) == f"state\n{'y' if ci else 'n'}\n0.9999\n{'y' if sort else 'n'}\n"
    assert step.post(v) == []


@pytest.mark.parametrize("files, expected", [("a.chlsj", "1\na.chlsj\n0\n"),
                                               ("a.hlsj\nb.chlsj", "2\na.hlsj\nb.chlsj\n0\n")])
def test_tabhfs(files, expected):
    step = core.RTabHfs()
    assert step.stdin(dict(step.defaults(), files=files)) == expected


def test_asfsplit():
    step = core.RAsfSplit()
    assert step.stdin(dict(step.defaults(), state="state")) == "state\ny\n"
    assert "same_orbitals" not in step.defaults()


@pytest.mark.parametrize("step, vals", [
    (core.RMixExtract(), dict(cutoff="nan")), (core.RMixExtract(), dict(cutoff="-0.1")),
    (core.RMixExtract(), dict(scope="2")), (core.RMixAccumulate(), dict(fraction="1.1")),
    (core.RMixAccumulate(), dict(fraction="inf")), (core.RMixAccumulate(), dict(state="a b")),
    (core.RMixAccumulate(), dict(fraction="0_1")), (core.RMixExtract(), dict(cutoff="０.５")),
    (core.RAsfSplit(), dict(state="a;echo")), (core.RTabHfs(), dict(files="")),
    (core.RTabHfs(), dict(files="\n".join(["x"] * 21))), (core.RTabHfs(), dict(nskip="-1")),
])
def test_invalid_tool_params(step, vals):
    with pytest.raises(ValueError):
        step.stdin(dict(step.defaults(), **vals))


def test_existing_extract_custom_steps_match():
    import grasp_config as config
    from pathlib import Path

    cfg, _ = config.load(str(Path(__file__).resolve().parents[1] / "demos/example8.json"))
    for job in cfg["jobs"]:
        v = job["vals"]
        if job["step"] == "custom" and v["prog"] == "rmixextract":
            scope, state, ci, cutoff, sort = v["stdin"].splitlines()
            step = core.RMixExtract()
            assert step.stdin(dict(step.defaults(), scope=scope, state=state, ci=ci == "y", cutoff=cutoff,
                                   sort=sort == "y")) == v["stdin"]


@pytest.mark.parametrize("name", ["manual_tools", "manual_asfsplit", "manual_accumulate"])
def test_new_tool_demo_transcripts(name, tmp_path):
    from pathlib import Path
    from tools import make_demos as demos

    d = getattr(demos, name)()
    d.checks = [(i, src) for i, src in d.checks if isinstance(src, str) and Path(src).is_absolute()]
    assert demos.check(d, [str(tmp_path)]) == 0
