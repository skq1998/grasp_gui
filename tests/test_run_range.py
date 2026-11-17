"""本机、远程和 MCP 共用范围规则；不改后台请求或导出脚本的格式。"""

import io
import json
from pathlib import Path
import socket
import subprocess
from types import SimpleNamespace

import pytest

import grasp_bg as bg
import grasp_config as config
import grasp_core as core
import grasp_mcp as mcp
import grasp_remote as remote
import grasp_service as service
import grasp_slurm as slurm
import grasp_summary as summary
from conftest import DEMOS
from test_summary import job


def install(root, jobs):
    config.install(config.export(jobs, dict(title="range")), str(root))
    return service.Service("/").record(str(root))[2]


@pytest.fixture
def launches(monkeypatch):
    calls = []
    monkeypatch.setattr(bg, "launch", lambda directory, jobs, cfg, **kw:
                        calls.append((jobs, cfg, kw)) or SimpleNamespace(wait=lambda: None))
    monkeypatch.setattr(bg, "connect", lambda *a, **kw: SimpleNamespace(close=lambda: None) if calls else None)
    monkeypatch.setattr(service.Service, "handshake", lambda *a: io.BytesIO())
    monkeypatch.setattr(bg, "logout_kills", lambda: False)
    return calls


@pytest.mark.parametrize("mode,expected", [("all", (0, 5)), ("from", (1, 5)),
                                           ("to", (0, 3)), ("selected", (1, 3))])
def test_selection_uses_first_and_last_in_pipeline_order(mode, expected):
    assert summary.selected_run_range(5, [2, 1], mode) == expected
    assert summary.run_bounds(5, 2, 3) == (1, 3)


@pytest.mark.parametrize("mode", ["from", "to", "selected"])
def test_noncontiguous_selection_is_rejected(mode):
    with pytest.raises(ValueError, match="连续"):
        summary.selected_run_range(5, [1, 3], mode)
    assert summary.selected_run_range(5, [1, 3], "all") == (0, 5)


@pytest.mark.parametrize("start,end", [(0, 2), (2, 1), (1, 4), (True, 2), (1, False), (1, "2")])
def test_invalid_bounds_rejected_by_all_entry_points(tmp_path, monkeypatch, start, end):
    root = tmp_path / "calc"
    revision = install(root, [job("rnucleus")] * 3)
    monkeypatch.setattr(core, "CONFIG_FILE", str(tmp_path / "isolated-mcp.json"))
    core.save_config(mcp_enabled=True)
    monkeypatch.setattr(bg, "launch", lambda *a, **kw: pytest.fail("invalid range launched"))
    op = remote.Operations()
    for call in (lambda: summary.run_bounds(3, start, end),
                 lambda: op.run_plan(str(root), start=start, end=end),
                 lambda: op.run(str(root), revision, start=start, end=end),
                 lambda: op.run(str(root), revision, start=start, end=end, server_type="slurm", slurm={}),
                 lambda: service.Service("/").run(str(root), start=start, end=end)):
        with pytest.raises(ValueError, match="范围"):
            call()
    result = mcp.Server(str(tmp_path)).result("run", dict(directory="calc", start=start, end=end))
    assert result["isError"] and result["content"][0]["text"]


@pytest.mark.parametrize("entry", ["local", "remote", "slurm", "mcp"])
def test_authorization_and_preflight_only_cover_range(tmp_path, monkeypatch, launches, entry):
    root = tmp_path / "calc"
    jobs = [job("custom", prog="printf", args="OUTSIDE-RANGE"), job("rnucleus"),
            job("rangular", csf="missing.c")]
    revision = install(root, jobs)
    op = remote.Operations()
    plan = op.run_plan(str(root), start=2, end=2)
    assert not plan["custom_commands"]
    assert op.run_plan(str(root), start=1, end=1)["custom_commands"]
    assert "run_range_v1" in op.hello(remote.PROTOCOL)["capabilities"]
    if entry == "slurm":
        scripts = []
        monkeypatch.setattr(slurm, "submit", lambda root, script, state:
                            scripts.append(script) or dict(backend="slurm", job_id="42"))
        op.run(str(root), revision, start=2, end=2, server_type="slurm",
               slurm=dict(core.SLURM_DEFAULTS, mpirun=""))
        assert len(scripts) == 1 and "rnucleus" in scripts[0]
        assert "OUTSIDE-RANGE" not in scripts[0] and "rangular" not in scripts[0]
        assert not launches
    else:
        if entry == "mcp":
            monkeypatch.setattr(core, "CONFIG_FILE", str(tmp_path / "isolated-mcp.json"))
            core.save_config(mcp_enabled=True)
            result = mcp.Server(str(tmp_path)).result("run", dict(directory="calc", start=2, end=2))
            assert not result["isError"], result
            response = json.loads(result["content"][0]["text"])
        elif entry == "remote":
            response = op.run(str(root), revision, start=2, end=2)
        else:
            response = service.Service("/").run(str(root), start=2, end=2)
        assert (response["from_step"], response["to_step"], response["total_steps"]) == (2, 2, 3)
        assert [j["step"] for j in launches[0][0]] == ["rnucleus"]
    with pytest.raises(ValueError, match="自定义"):
        service.Service("/").run(str(root), start=1, end=1)
    # 导出始终是完整流程，不受上一次运行范围影响。
    exported = op.export_script(str(root))["text"]
    assert "OUTSIDE-RANGE" in exported and "rangular" in exported
    assert "end" in mcp.TOOLS["run"][1] and "default" not in mcp.TOOLS["run"][1]["end"]


@pytest.mark.parametrize("backend", ["local", "workstation", "slurm"])
def test_segment_needs_earlier_outputs_and_keeps_global_step_numbers(tmp_path, launches, monkeypatch, backend):
    root = tmp_path / "calc"
    revision = install(root, [job("rcsfgenerate", saveas="earlier.c"), job("rangular", csf="earlier.c"), job("rci")])
    scripts = []
    monkeypatch.setattr(slurm, "submit", lambda root, script, state: scripts.append(script))
    def run():
        if backend == "local":
            return service.Service("/").run(str(root), start=2, end=2)
        options = dict(server_type=backend)
        if backend == "slurm":
            options["slurm"] = dict(core.SLURM_DEFAULTS, mpirun="")
        return remote.Operations().run(str(root), revision, start=2, end=2, **options)
    with pytest.raises(service.PreflightError) as error:
        run()
    assert any(row["file"] == "earlier.c" for row in error.value.data["missing"])
    assert all(row["step"] == 2 for row in error.value.data["missing"])
    assert all(row["step"] == 2 for row in error.value.data["issues"])
    assert not launches and not scripts
    for name in ("earlier.c", "isodata"):
        (root / name).write_text("already produced")
    run()
    if backend == "slurm":
        assert len(scripts) == 1 and "rangular" in scripts[0] and "rci" not in scripts[0]
    else:
        assert [j["step"] for j in launches[0][0]] == ["rangular"]


@pytest.mark.parametrize("backend", ["workstation", "slurm"])
def test_custom_command_confirmation_uses_pipeline_step_numbers(tmp_path, backend):
    root = tmp_path / "calc"
    install(root, [job("custom", prog="printf", args="before"), job("rnucleus"),
                   job("custom", prog="printf", args="selected")])
    op = remote.Operations()
    whole = op.run_plan(str(root), server_type=backend)["custom_commands"]
    selected = op.run_plan(str(root), server_type=backend, start=3, end=3)["custom_commands"]
    assert [i for i, _ in whole] == [1, 3]
    assert selected == [whole[1]] and selected[0][0] == 3
    assert not op.run_plan(str(root), server_type=backend, start=2, end=2)["custom_commands"]


def test_segment_success_does_not_mark_following_steps_successful(tmp_path):
    jobs = [job("rnucleus"), job("rcsfgenerate"), job("rangular"), job("rwfnestimate")]
    titles = [j["title"] for j in jobs[1:3]]
    bg.write_state(tmp_path, dict(pid=0, host=socket.gethostname(), status="ok", step=1, titles=titles))
    assert [row["status"] for row in summary.run_status(jobs, tmp_path)] == [
        "unknown", "success", "success", "unknown"]
    assert summary.title_range(jobs, titles) == (2, 3, 4)
    assert summary.title_range([jobs[0], jobs[0]], [jobs[0]["title"]]) is None


def test_stdio_range_capability_plan_and_bounds_errors(tmp_path, monkeypatch):
    from test_mcp import Client, data
    from test_remote import Peer

    root = tmp_path / "calc"
    revision = install(root, [job("custom"), job("rnucleus"), job("rangular", csf="missing.c")])
    peer = Peer()
    try:
        assert "run_range_v1" in peer.call("hello", protocol=1)["result"]["capabilities"]
        plan = peer.call("run_plan", directory=str(root), start=2, end=2)["result"]
        assert not plan["custom_commands"] and plan["revision"] == revision
        assert peer.call("run_plan", directory=str(root), start=3, end=2).get("error")
        assert peer.call("run", directory=str(root), revision=revision, start=1, end=4).get("error")
    finally:
        peer.close()
    settings = tmp_path / "isolated-mcp.json"
    monkeypatch.setattr(core, "CONFIG_FILE", str(settings))
    client = Client(tmp_path, settings, no_qt=True)
    try:
        client.initialize()
        tools = client.request("tools/list")["result"]["tools"]
        run = next(tool for tool in tools if tool["name"] == "run")
        assert run["inputSchema"]["properties"]["end"]["minimum"] == 1
        result = client.call("run", directory="calc", start=3, end=2)
        assert result["isError"] and "范围" in result["content"][0]["text"]
        assert data(client.call("get_pipeline", directory="calc"))["revision"] == revision
    finally:
        client.close()


def test_out_of_range_invalid_names_do_not_block_run(tmp_path, launches):
    root = tmp_path / "calc"
    install(root, [job("rnucleus"), job("jj2lsj", state="valid")])
    path = root / core.SESSION_FILE
    record = json.loads(path.read_text())
    record["pipeline"][1]["vals"]["state"] = "x" * 60
    path.write_text(json.dumps(record))
    op = remote.Operations()
    assert not op.run_plan(str(root), end=1)["custom_commands"]
    op.run(str(root), op.record(str(root))[2], end=1)
    assert [j["step"] for j in launches[0][0]] == ["rnucleus"]
    with pytest.raises(service.PreflightError) as error:
        service.Service("/").run(str(root), start=2)
    assert error.value.data["invalid"] and all(row["step"] == 2 for row in error.value.data["invalid"])
    assert all(row["step"] == 2 for row in error.value.data["issues"])


@pytest.mark.grasp
def test_real_example1_split_at_first_rmcdhf(tmp_path, grasp_bindir):
    cfg, _ = config.load(Path(DEMOS) / "example1.json")
    config.install(cfg, str(tmp_path / "calc"))
    root = tmp_path / "calc"
    jobs = config.session(cfg)["pipeline"]
    end = next(i + 1 for i, j in enumerate(jobs) if j["step"] == "rmcdhf")
    for start, last in ((1, end), (end + 1, len(jobs))):
        first, stop = summary.run_bounds(len(jobs), start, last)
        checked = summary.check_files(root, jobs[first:stop], offset=first)
        assert not checked["missing"] and not checked["invalid"], checked
        script = core.shell_script(jobs[first:stop], grasp_bindir, ".")
        result = subprocess.run(["sh", "-c", script], cwd=root, env=core.child_env(),
                                capture_output=True, text=True, timeout=180)
        assert result.returncode == 0, result.stderr[-2000:]
        if start == 1:
            assert (root / "rwfn.out").stat().st_size > 0
            assert not any((root / ref["file"]).exists() for ref in cfg["reference"])
            assert not (root / "2s_3.uni.lsj.lbl").exists()
    for ref in cfg["reference"]:
        assert config.reference_energies(str(root / ref["file"])) == pytest.approx(ref["energies"], abs=1e-7)
