"""MCP 协议、显式开关、目录范围和工作目录的修订冲突。"""

import io
import json
import multiprocessing
import os
from pathlib import Path
import queue
import select
import subprocess
import sys
import time

import pytest

import grasp_bg as bg
import grasp_config as config
import grasp_core as core
import grasp_mcp as mcp
import grasp_service as service
import grasp_session as sessions
from conftest import ROOT


@pytest.fixture
def settings(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    monkeypatch.setattr(core, "CONFIG_FILE", str(path))
    return path


def document(custom=False):
    step = config.STEPS["custom" if custom else "rnucleus"]
    values = step.defaults()
    if custom:
        values.update(prog="sh", args="-c 'sleep 2; echo from-mcp'")
    return dict(format=config.FORMAT, version=config.VERSION, jobs=[dict(step=step.key, vals=values)])


class Client:
    def __init__(self, directory, settings, enabled=True, allow_custom=False, no_qt=False):
        self.counter = 0
        core.save_config(mcp_enabled=enabled)
        code = ("import sys,runpy\n" + f"sys.path.insert(0, {ROOT!r})\n" +
                "import grasp_core as core\n" + f"core.CONFIG_FILE = {str(settings)!r}\n")
        if no_qt:
            code += "sys.modules['PySide6'] = None; sys.modules['PySide2'] = None\n"
        code += f"runpy.run_path({os.path.join(ROOT, 'grasp_gui.py')!r}, run_name='__main__')\n"
        self.command = [sys.executable, "-c", code, "mcp", "--root", str(directory)]
        if allow_custom:
            self.command.append("--allow-custom")
        self.proc = subprocess.Popen(self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True)

    def send(self, message):
        self.proc.stdin.write(json.dumps(message) + "\n")
        self.proc.stdin.flush()

    def request(self, method, params=None):
        self.counter += 1
        self.send(dict(jsonrpc="2.0", id=self.counter, method=method, params=params or {}))
        if not select.select([self.proc.stdout], [], [], 10)[0]:
            raise AssertionError("MCP timeout: " + method)
        raw = self.proc.stdout.readline()
        assert raw, self.proc.stderr.read() if self.proc.poll() is not None else "MCP exited"
        result = json.loads(raw)
        assert result["id"] == self.counter
        return result

    def initialize(self):
        result = self.request("initialize", dict(protocolVersion="2025-11-25", capabilities={},
                                                 clientInfo=dict(name="pytest", version="1")))
        assert result["result"]["protocolVersion"] == "2025-11-25"
        self.send(dict(jsonrpc="2.0", method="notifications/initialized"))

    def call(self, name, **arguments):
        return self.request("tools/call", dict(name=name, arguments=arguments))["result"]

    def close(self):
        if self.proc.poll() is None:
            self.proc.stdin.close()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)
        self.proc.stdout.close()
        self.proc.stderr.close()


def data(result):
    assert not result.get("isError"), result
    return json.loads(next(x["text"] for x in result["content"] if x["type"] == "text"))


def test_disabled_by_default_and_config_output(tmp_path, settings):
    client = Client(tmp_path / "allowed", settings, enabled=False, no_qt=True)
    try:
        assert client.proc.wait(timeout=5) == 1
        assert "--enable" in client.proc.stderr.read()
        assert client.proc.stdout.read() == ""
    finally:
        client.close()
    for target in ("claude", "codex", "gemini"):
        command = client.command + ["--print-config", target]
        result = subprocess.run(command, capture_output=True, text=True, timeout=10)
        assert result.returncode == 0 and str(tmp_path / "allowed") in result.stdout
        assert "grasp_gui.py" in result.stdout and "--root" in result.stdout
        assert not core.load_config().get("mcp_enabled", False)
    for flag, expected in (("--enable", True), ("--disable", False)):
        result = subprocess.run(client.command + [flag], capture_output=True, text=True, timeout=10)
        assert result.returncode == 0, result.stderr
        assert core.load_config()["mcp_enabled"] is expected


def test_stdio_protocol_config_and_live_disable(tmp_path, settings):
    root = tmp_path / "allowed"
    client = Client(root, settings, no_qt=True)
    try:
        assert client.request("tools/list")["error"]["code"] == -32602
        client.initialize()
        tools = client.request("tools/list")["result"]["tools"]
        assert {t["name"] for t in tools} == mcp.TOOLS.keys()
        assert not any("enable" in t["name"] for t in tools)
        assert data(client.call("list_steps"))["steps"]
        assert data(client.call("describe_step", step="rmcdhf"))["fields"]
        assert any(x["id"] == "example1" for x in data(client.call("list_examples"))["examples"])
        assert data(client.call("validate_config", config=document()))["valid"]
        result = data(client.call("create_workdir", directory="calc", example="example1"))
        assert result["revision"]
        plan = data(client.call("get_pipeline", directory="calc"))
        assert len(plan["config"]["jobs"]) == 27
        script = data(client.call("export_script", directory="calc"))
        assert "rnucleus" in script["text"] and not script["truncated"]
        slurm = data(client.call("export_script", directory="calc", slurm=dict(job="mcp", ntasks="2")))
        assert "#SBATCH --job-name=mcp" in slurm["text"]
        assert "#SBATCH --ntasks-per-node=2" in slurm["text"]
        core.save_config(mcp_enabled=False)
        assert client.call("create_workdir", directory="forbidden", example="example1")["isError"]
        assert not (root / "forbidden").exists()
        assert not core.load_config()["mcp_enabled"]
    finally:
        client.close()


@pytest.mark.parametrize("language,notice", [("zh", "不要代为执行"), ("en", "do not run commands on their behalf")])
def test_disabled_tool_informs_user_without_enable_command(tmp_path, settings, language, notice):
    core.save_config(lang=language)
    client = Client(tmp_path / "allowed", settings, no_qt=True)
    try:
        client.initialize()
        core.save_config(mcp_enabled=False)
        result = client.call("list_steps")
        assert result["isError"]
        text = "\n".join(item["text"] for item in result["content"] if item["type"] == "text")
        assert "--enable" not in text
        assert notice in text
        assert not core.load_config()["mcp_enabled"]
    finally:
        client.close()


def test_paths_custom_and_revision_conflicts(tmp_path, settings):
    root = tmp_path / "allowed"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "escape").symlink_to(outside, target_is_directory=True)
    client = Client(root, settings)
    try:
        client.initialize()
        for directory in ("../outside", str(outside), "escape/new"):
            assert client.call("create_workdir", directory=directory, config=document())["isError"]
        assert client.call("create_workdir", directory="custom", config=document(True))["isError"]
        initial = data(client.call("create_workdir", directory="calc", config=document()))
        changed = document()
        changed["jobs"][0]["vals"]["z"] = "4"
        updated = data(client.call("update_workdir", directory="calc", revision=initial["revision"], config=changed))
        assert updated["revision"] != initial["revision"]
        assert client.call("update_workdir", directory="calc", revision=initial["revision"],
                           config=document())["isError"]
        assert core.load_session(root / "calc")["pipeline"][0]["vals"]["z"] == "4"
        assert bg.status(root / "calc") is None
    finally:
        client.close()


def test_background_run_stop_and_source(tmp_path, settings):
    root = tmp_path / "allowed"
    client = Client(root, settings, allow_custom=True)
    try:
        client.initialize()
        data(client.call("create_workdir", directory="calc", config=document(True)))
        assert data(client.call("run", directory="calc"))["started_by"] == "mcp"
        assert client.call("run", directory="calc")["isError"]
        assert data(client.call("status", directory="calc"))["state"]["started_by"] == "mcp"
        assert data(client.call("stop", directory="calc"))["stop_requested"]
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if data(client.call("status", directory="calc"))["state"]["status"] != "running":
                break
            time.sleep(0.02)
        else:
            pytest.fail("background calculation did not stop")
        assert "text" in data(client.call("tail_log", directory="calc"))
    finally:
        client.close()


def test_bad_frames_and_protocol_negotiation(tmp_path, settings):
    core.save_config(mcp_enabled=True)
    server = mcp.Server(tmp_path)
    output = io.StringIO()
    requests = [b"not json\n", b"[]\n", json.dumps(dict(jsonrpc="2.0", id=1, method="initialize", params=dict(
        protocolVersion="2099-01-01", capabilities={}, clientInfo={}))).encode() + b"\n"]
    assert server.serve(io.BytesIO(b"".join(requests)), output) == 0
    rows = [json.loads(x) for x in output.getvalue().splitlines()]
    assert rows[0]["error"]["code"] == -32700
    assert rows[1]["error"]["code"] == -32600
    assert rows[2]["result"]["protocolVersion"] == mcp.PROTOCOLS[0]
    output = io.StringIO()
    assert mcp.Server(tmp_path).serve(io.BytesIO(b"\xff\n"), output) == 0
    assert json.loads(output.getvalue())["error"]["code"] == -32700
    for value in (0, 1001, True, "10"):
        with pytest.raises(ValueError):
            mcp.arguments("levels", dict(directory=".", limit=value))


def _writer(directory, configuration, expected, mark, ready, results):
    core.CONFIG_FILE = configuration
    ready.put(True)
    try:
        sessions.write(directory, dict(pipeline=[], mark=mark), expected)
        results.put("written")
    except sessions.Conflict:
        results.put("conflict")


def test_atomic_compare_and_swap_serializes_writers(tmp_path, settings):
    root = tmp_path / "calc"
    root.mkdir()
    revision = sessions.write(root, dict(pipeline=[]))
    ctx = multiprocessing.get_context("spawn")
    ready, results = ctx.Queue(), ctx.Queue()
    processes = [ctx.Process(target=_writer, args=(str(root), str(settings), revision, n, ready, results))
                 for n in range(2)]
    with sessions.locked(root):
        for process in processes:
            process.start()
        for _ in processes:
            assert ready.get(timeout=10)
        with pytest.raises(queue.Empty):
            results.get(timeout=0.1)
    assert sorted(results.get(timeout=10) for _ in processes) == ["conflict", "written"]
    for process in processes:
        process.join(timeout=10)
        assert process.exitcode == 0
    ready.close()
    results.close()


def wavefunction(path):
    import struct

    def record(raw):
        return struct.pack("<i", len(raw)) + raw + struct.pack("<i", len(raw))

    path.write_bytes(b"".join(record(x) for x in [b"G92RWF", struct.pack("<iidi", 1, -1, 0.5, 4),
                                                struct.pack("<9d", 0, 0, 0.5, 0.2, 0, 0, 0.1, 0.05, 0),
                                                struct.pack("<4d", 0, 1, 2, 3)]))


def test_result_tools_and_png_svg_pdf(tmp_path, settings):
    from test_large_files import make_files

    root = tmp_path / "allowed"
    root.mkdir()
    path = make_files(root / "calc", 25, 2)
    wavefunction(path.parent / "state.w")
    client = Client(root, settings)
    try:
        client.initialize()
        files = data(client.call("list_results", directory="calc"))["files"]
        assert {x["type"] for x in files} >= {"mixing", "csf", "wavefunctions"}
        levels = data(client.call("levels", directory="calc", limit=1, components=3))
        assert len(levels["levels"]) == 1 and levels["truncated"]
        assert len(levels["levels"][0]["components"]) == 3
        assert levels["levels"][0]["J"] == "1/2"
        orbitals = data(client.call("orbitals", directory="calc"))["orbitals"]
        assert orbitals[0]["name"] == "1s" and orbitals[0]["norm"] > 0
        pytest.importorskip("PySide6.QtWidgets")
        for tool in ("plot_levels", "plot_wavefunctions"):
            for extension in ("png", "svg", "pdf"):
                result = client.call(tool, directory="calc", output=tool + "." + extension)
                assert not result["isError"], result
                image = next(c for c in result["content"] if c["type"] == "image")
                import base64
                assert base64.b64decode(image["data"]).startswith(b"\x89PNG")
                assert (path.parent / (tool + "." + extension)).stat().st_size > 0
        assert client.call("plot_levels", directory="calc", output="../../outside.png")["isError"]
        assert not (tmp_path / "outside.png").exists()
    finally:
        client.close()


def test_live_disable_leaves_started_run_alive(tmp_path, settings):
    root = tmp_path / "allowed"
    client = Client(root, settings, allow_custom=True)
    try:
        client.initialize()
        data(client.call("create_workdir", directory="calc", config=document(True)))
        data(client.call("run", directory="calc"))
        core.save_config(mcp_enabled=False)
        assert client.call("stop", directory="calc")["isError"]
        assert bg.status(root / "calc")["status"] == "running"
        core.save_config(mcp_enabled=True)
        assert data(client.call("stop", directory="calc"))["confirmed"]
    finally:
        client.close()


def test_no_qt_and_forged_job_metadata(tmp_path, settings, monkeypatch):
    root = tmp_path / "allowed"
    client = Client(root, settings, no_qt=True)
    try:
        client.initialize()
        data(client.call("create_workdir", directory="calc", config=document()))
        assert "Qt" in str(client.call("plot_levels", directory="calc"))
        record = core.load_session(root / "calc")
        record["pipeline"][0].update(prog="sh", pre=[("cmd", "echo forbidden")])
        sessions.write(root / "calc", record)
        launched = []
        from types import SimpleNamespace
        monkeypatch.setattr(bg, "launch", lambda d, jobs, cfg, **kw:
                            launched.extend(jobs) or SimpleNamespace(wait=lambda: 0))
        monkeypatch.setattr(bg, "connect", lambda *a, **kw: None)
        with pytest.raises(ValueError, match="后台运行"):
            service.Service(root).run("calc")
        assert len(launched) == 1
        assert launched[0]["prog"] == "rnucleus" and launched[0].get("pre", []) == []
        record["pipeline"] = config.session(document(True))["pipeline"]
        sessions.write(root / "calc", record)
        assert client.call("run", directory="calc")["isError"]
    finally:
        client.close()


def test_special_metadata_files_are_refused(tmp_path, settings):
    root = tmp_path / "calc"
    config.install(document(), root)
    svc = service.Service(tmp_path)
    for name, action in ((bg.RUN_STATE, svc.status), (bg.RUN_LOG, svc.tail_log)):
        path = root / name
        os.mkfifo(path)
        with pytest.raises(ValueError):
            action("calc")
        path.unlink()


def test_transition_units_gauges_and_branching(tmp_path, settings):
    root = tmp_path / "calc"
    root.mkdir()
    text = "f1 = upper\nf2 = lower\n\nElectric 2**( 1)-pole\n\n\n\n\n"
    for lower, rate in ((1, 3.0), (2, 1.0)):
        text += (f" f1{1:3d} {1:4d}{'+':>4s}f2{lower:3d} {0:4d}{'+':>4s}"
                 f"{10000.0:13.5E}{'B':>2s}{rate:13.5E}{0.25:13.5E}{0.5:13.5E}\n")
    (root / "result.ct").write_text(text)
    client = Client(tmp_path, settings, no_qt=True)
    try:
        client.initialize()
        result = data(client.call("transitions", directory="calc", files=["result.ct"]))
        rows = result["transitions"]
        assert len(rows) == 2 and rows[0]["wavelength_angstrom"] == 10000
        assert rows[0]["A"] == 3.0 and rows[0]["gf"] == 0.25 and rows[0]["S"] == 0.5
        assert [row["branching_ratio"] for row in rows] == [0.75, 0.25]
        assert rows[0]["upper"] == ["upper", 2, 1, 1]
    finally:
        client.close()


@pytest.mark.grasp
def test_mcp_example1_reproduces_reference(tmp_path, settings, grasp_bindir, monkeypatch):
    monkeypatch.setenv("GRASP_BINDIR", grasp_bindir)
    core.save_config(np=1)
    client = Client(tmp_path, settings)
    try:
        client.initialize()
        data(client.call("create_workdir", directory="example1", example="example1"))
        data(client.call("run", directory="example1"))
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            state = data(client.call("status", directory="example1"))["state"]
            if state["status"] != "running":
                break
            time.sleep(0.05)
        assert state["status"] == "ok" and state["started_by"] == "mcp"
        cfg, _ = config.load(Path(ROOT) / "demos" / "example1.json")
        for ref in cfg["reference"]:
            rows = data(client.call("levels", directory="example1", files=[ref["file"]]))["levels"]
            got = sorted(row["energy_hartree"] for row in rows)
            expected = sorted(ref["energies"])
            assert len(got) == len(expected)
            assert max(abs(a - b) for a, b in zip(got, expected)) < 1e-7
        pytest.importorskip("PySide6.QtWidgets")
        result = client.call("plot_levels", directory="example1")
        assert not result["isError"] and any(c["type"] == "image" for c in result["content"])
    finally:
        client.close()
