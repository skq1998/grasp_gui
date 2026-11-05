"""NSIC 非默认输入按 GRASP 源码核对；关闭时保持原输入。"""

import pytest

import grasp_config as config
import grasp_core as core


@pytest.mark.parametrize("levels, weights, uweights, middle", [
    ("1", "9", "", "1\n"),
    ("1\n1", "5", "", "1\n1\n5\n"),
    ("1-2", "9", "2,1", "1-2\n9\n2,1\n"),
])
def test_nsic_input(levels, weights, uweights, middle):
    step = core.RMcdhf()
    vals = dict(step.defaults(), levels=levels, weights=weights, uweights=uweights, nsic="3")
    assert step.stdin(vals) == "y\n" + middle + "*\n*\n100\n"
    vals["modify_nsic"] = True
    # 默认、调试、网格、ACCY；现有参数；其它默认值；SCF 正交化顺序。
    assert step.stdin(vals) == ("n\nn\nn\nn\n" + middle + "*\n*\n100\n"
                                "y\nn\nn\nn\nn\nn\ny\n3\nn\nn\n1\n")


@pytest.mark.parametrize("value", ["", "-1", "1.5", "abc", "1\n2", "1e2", "１"])
def test_invalid_nsic(value):
    vals = dict(core.RMcdhf.defaults(), modify_nsic=True, nsic=value)
    doc = dict(format=config.FORMAT, version=config.VERSION, jobs=[dict(step="rmcdhf", vals=vals)])
    with pytest.raises(config.ConfigError, match="NSIC"):
        config.check(doc)
    with pytest.raises(ValueError, match="NSIC"):
        core.RMcdhf().stdin(vals)
    vals["modify_nsic"] = False
    config.check(doc)
    assert core.RMcdhf().stdin(vals).startswith("y\n")


def test_old_config_and_zero_nsic():
    vals = core.RMcdhf.defaults()
    del vals["modify_nsic"], vals["nsic"]
    cfg, warnings = config.check(dict(format=config.FORMAT, version=config.VERSION,
                                     jobs=[dict(step="rmcdhf", vals=vals)]))
    assert not warnings
    imported = config.job_values(cfg["jobs"][0])
    assert not imported["modify_nsic"]
    assert core.RMcdhf().stdin(imported) == core.RMcdhf().stdin(vals)
    imported.update(modify_nsic=True, nsic=" 0 ")
    assert "\ny\n0\nn\nn\n1\n" in core.RMcdhf().stdin(imported)


def test_config_calls_validation_hook(monkeypatch):
    class CheckedStep(core.Step):
        key = "validation_test"
        fields = [core.Entry("value", "value", "0")]

        def validate(self, v):
            if v["value"] != "0":
                raise ValueError("invalid value")

    monkeypatch.setitem(config.STEPS, CheckedStep.key, CheckedStep())
    monkeypatch.setattr(core, "package_allows", lambda package, key: True)     # 临时程序页不属于任何程序包
    job = dict(step=CheckedStep.key, vals=dict(value=0))
    doc = dict(format=config.FORMAT, version=config.VERSION, jobs=[job])
    assert config.check(doc)[1] == []
    job["vals"]["value"] = 1
    with pytest.raises(config.ConfigError, match="invalid value"):
        config.check(doc)
