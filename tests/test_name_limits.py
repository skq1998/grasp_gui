"""名称限制、派生文件来源，以及 GUI/MCP 不可强制跳过的预检。"""

import json
from pathlib import Path
import time

import pytest

import grasp_bg as bg
import grasp_config as config
import grasp_core as core
import grasp_service as service
import grasp_summary as summary
from conftest import DEMOS
from test_mcp import Client, data, settings

LONG = "rn2_odd_cv6_low_corr_qed"
assert len(LONG) == 24


def raw_job(key, **values):
    step = config.STEPS[key]
    return dict(step=key, vals=dict(step.defaults(), **values), title=step.prog, prog=step.prog)


def config_doc(jobs):
    return dict(format=config.FORMAT, version=config.VERSION, jobs=[config.export_job(j) for j in jobs])


def test_name_error_keeps_missing_files_and_limit(tmp_path):
    checked = summary.check_files(tmp_path, [raw_job("jj2lsj", state=LONG)])
    row, = checked["invalid"]
    assert (row["step"], row["program"], row["field"], row["name"], row["limit"]) == (1, "jj2lsj", "state", LONG, 23)
    assert row["unit"] == "characters" and row["kind"] == "name"
    assert checked["missing"] and {r["kind"] for r in checked["issues"]} == {"name", "missing"}
    assert LONG in summary.check_text(checked)


@pytest.mark.parametrize("producer,values,field", [
    ("rmcdhf", dict(save=LONG), "save"),
    ("rsave", dict(state=LONG), "state"),
    ("rcsfgenerate", dict(saveas=LONG + ".c"), "saveas"),
    ("rcsfsplit", dict(state=LONG[:-1], splits="3s,3p,3d d"), "state, splits"),
])
def test_derived_names_report_the_declared_producer(tmp_path, producer, values, field):
    name = LONG[:-1] + "d" if producer == "rcsfsplit" else LONG
    jobs = [raw_job(producer, **values), raw_job("jj2lsj", state=name)]
    checked = summary.check_files(tmp_path, jobs)
    source = next(r for r in checked["invalid"] if r["step"] == 1)
    assert source["program"] == producer and source["field"] == field
    assert source["name"] == name and source["limit"] == 23
    assert source["required_by"] == dict(step=2, program="jj2lsj", field="state")
    assert len([r for r in checked["invalid"] if r["step"] == 1]) == 1


def test_producer_trace_respects_overwrites_and_run_start(tmp_path):
    jobs = [raw_job("rsave", state=LONG), raw_job("rsave", state=LONG), raw_job("jj2lsj", state=LONG)]
    checked = summary.check_files(tmp_path, jobs)
    assert {r["step"] for r in checked["invalid"]} == {2, 3}
    checked = summary.check_files(tmp_path, jobs, start=2)
    assert {r["step"] for r in checked["invalid"]} == {3}


@pytest.mark.parametrize("name", ["case1", "example1"])
def test_manual_pipelines_have_no_name_issues(tmp_path, name):
    cfg, _ = config.load(Path(DEMOS) / (name + ".json"))
    checked = summary.check_files(tmp_path, config.session(cfg)["pipeline"])
    assert not checked["issues"]


def test_limits_come_from_validate_and_count_utf8_bytes(tmp_path):
    checked = summary.check_files(tmp_path, [raw_job("rwfnestimate", use_file=True, wfile="中" * 43)])
    row = checked["invalid"][0]
    assert row["field"] == "wfile" and row["limit"] == 128 and row["unit"] == "bytes"
    assert not summary.check_files(None, [raw_job("rci", state="x" * 24)])["invalid"]
    with pytest.raises(config.ConfigError):
        config.check(config_doc([raw_job("jj2lsj", state=LONG)]))


def test_mcp_run_name_failure_is_structured_and_cannot_be_forced(tmp_path, settings):
    root = tmp_path / "allowed"
    root.mkdir()
    core.save_session(root, dict(pipeline=[raw_job("rmcdhf", save=LONG), raw_job("jj2lsj", state=LONG)]))
    client = Client(root, settings, no_qt=True)
    try:
        client.initialize()
        for force in (False, True):
            response = client.call("run", directory=".", force=force)
            assert response["isError"]
            result = response["structuredContent"]
            assert result == json.loads(response["content"][0]["text"])
            assert result["started"] is False and result["missing"]
            assert {r["step"] for r in result["invalid"]} == {1, 2}
            assert any(r.get("limit") == 23 for r in result["issues"])
            assert not (root / bg.RUN_REQUEST).exists() and bg.status(root) is None
    finally:
        client.close()


def test_mcp_validate_reports_names_without_checking_local_inputs(tmp_path, settings):
    client = Client(tmp_path, settings, no_qt=True)
    try:
        client.initialize()
        response = client.call("validate_config", config=config_doc([
            raw_job("rmcdhf", save=LONG), raw_job("jj2lsj", state=LONG)]))
        assert response["isError"] and response["structuredContent"]["valid"] is False
        assert {r["step"] for r in response["structuredContent"]["issues"]} == {1, 2}
        assert all(r["kind"] == "name" for r in response["structuredContent"]["issues"])
        assert data(client.call("validate_config", config=config_doc([raw_job("rangular", csf="remote.c")])))["valid"]
    finally:
        client.close()


def test_mcp_missing_files_need_explicit_boolean_force(tmp_path, settings, monkeypatch):
    monkeypatch.delenv("GRASP_BINDIR", raising=False)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    stub = bindir / "rhfs"
    stub.write_text("#!/bin/sh\ncat >/dev/null\nprintf done > executed\n")
    stub.chmod(0o700)
    core.save_config(bindir=str(bindir), np=1)
    root = tmp_path / "allowed"
    root.mkdir()
    core.save_session(root, dict(pipeline=[raw_job("rhfs", state="state")]))
    client = Client(root, settings, no_qt=True)
    try:
        client.initialize()
        result = client.call("run", directory=".")
        assert result["isError"] and result["structuredContent"]["missing"][0]["file"] == "isodata"
        assert client.call("run", directory=".", force="true")["isError"]
        assert bg.status(root) is None and not (root / "executed").exists()
        assert data(client.call("run", directory=".", force=True))["started"]
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and (bg.status(root) or {}).get("status") == "running":
            time.sleep(0.02)
        assert (root / "executed").read_text() == "done"
    finally:
        client.close()


@pytest.mark.grasp
def test_real_grasp_example1_through_mcp_preflight(tmp_path, settings, grasp_bindir):
    core.save_config(bindir=grasp_bindir, np=1)
    client = Client(tmp_path / "allowed", settings, no_qt=True)
    try:
        client.initialize()
        data(client.call("create_workdir", directory="calc", example="example1"))
        assert data(client.call("run", directory="calc"))["started"]
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            state = data(client.call("status", directory="calc"))["state"]
            if state["status"] != "running":
                break
            time.sleep(0.05)
        assert state["status"] == "ok"
        cfg, _ = config.load(Path(DEMOS) / "example1.json")
        root = tmp_path / "allowed" / "calc"
        for reference in cfg["reference"]:
            got = config.reference_energies(str(root / reference["file"]))
            assert got == pytest.approx(reference["energies"], abs=1e-7)
    finally:
        client.close()


def test_os_filename_limit_does_not_hide_validation_details(tmp_path):
    item = raw_job("rcsfzerofirst", zero="z" * 257, full="full.c")
    core.save_session(tmp_path, dict(pipeline=[item]))
    with pytest.raises(service.PreflightError) as caught:
        service.Service(tmp_path).run(".", force=True)
    result = caught.value.data
    assert result["invalid"][0]["field"] == "zero" and result["invalid"][0]["limit"] == 256
    assert result["missing"]


def test_stored_copy_actions_are_checked_as_the_gui_will_run_them(tmp_path):
    step = config.STEPS["rnucleus"]
    item = step.job(step.defaults())
    item["pre"] = [("cp", "seed", "isodata")]
    item["post"] = [("cp", "generated", "archive")]
    checked = summary.check_files(tmp_path, [item])
    assert {r["file"] for r in checked["missing"]} == {"seed", "generated"}


def test_validate_config_does_not_trust_extra_copy_actions(tmp_path, settings):
    cfg = config_doc([raw_job("rnucleus"), raw_job("jj2lsj", state=LONG)])
    cfg["jobs"][0]["pre"] = [["cp", "seed", LONG + ".c"]]
    client = Client(tmp_path, settings, no_qt=True)
    try:
        client.initialize()
        response = client.call("validate_config", config=cfg)
        assert response["isError"]
        assert {r["step"] for r in response["structuredContent"]["issues"]} == {2}
    finally:
        client.close()
