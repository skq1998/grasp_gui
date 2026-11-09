"""远程 stdio、流式径向数据和共享结果的一致性。"""

import json
import os
from pathlib import Path
import select
import subprocess
import sys
import time

import pytest

import grasp_core as core
import grasp_config as config
import grasp_remote as remote
import grasp_service as service
import grasp_ssh as ssh
import grasp_wfn as wfn
from conftest import ROOT
from test_large_files import make_files
from test_mcp import wavefunction, document


class Peer:
    def __init__(self):
        self.proc = subprocess.Popen([sys.executable, str(Path(ROOT) / "grasp_gui.py"), "remote"],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        assert self.proc.stdout.readline().decode().strip() == remote.GREETING
        self.sequence = 0

    def send(self, method, **args):
        self.sequence += 1
        self.proc.stdin.write(remote.pack(dict(id=self.sequence, method=method, args=args)))
        self.proc.stdin.flush()
        return self.sequence

    def receive(self, ident, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            assert select.select([self.proc.stdout], [], [], max(0, deadline-time.monotonic()))[0]
            value = remote.unpack(self.proc.stdout.readline())
            if value.get("id") == ident and "event" not in value:
                return value
        pytest.fail("remote timeout")

    def call(self, method, **args):
        return self.receive(self.send(method, **args))

    def close(self):
        self.proc.stdin.close()
        self.proc.wait(timeout=5)
        self.proc.stdout.close()
        self.proc.stderr.close()


def test_protocol_no_mcp_permission_pipeline_and_results(tmp_path):
    make_files(tmp_path, 100, 2)
    wavefunction(tmp_path / "state.w")
    peer = Peer()
    try:
        assert peer.call("hello", protocol=1)["result"]["protocol"] == 1
        assert peer.call("hello", protocol=999).get("error")
        assert peer.call("exec", command="touch forbidden").get("error")
        directory = str(tmp_path)
        listing = peer.call("list_dir", directory=directory)["result"]
        assert {r["type"] for r in listing["files"]} >= {"mixing", "csf", "wavefunctions"}
        result = peer.call("levels", directory=directory)["result"]
        assert result == json.loads(json.dumps(service.Service("/").levels(directory)))
        curves = peer.call("wavefunctions", directory=directory, points=3)["result"]["curves"]
        assert len(curves[0]["P"]) == 3
        assert peer.call("wavefunctions", directory=directory, points=10001).get("error")
        first = peer.call("view", directory=directory, kind="levels")["result"]
        same = peer.call("view", directory=directory, kind="levels", known_signature=first["signature"])["result"]
        assert same["unchanged"]
        config_path = tmp_path / "calculation"
        config.install(document(), config_path)
        plan = peer.call("pipeline", directory=str(config_path))["result"]
        assert len(plan["jobs"]) == 1 and plan["revision"]
    finally:
        peer.close()


def test_streamed_radial_properties_match_existing_reader(tmp_path):
    path = tmp_path / "state.w"
    wavefunction(path)
    old = wfn.read_w(path)[0]
    identity, props = list(wfn.orbital_properties(path))[0]
    assert identity["name"] == old.name
    assert props["norm"] == pytest.approx(old.norm(), abs=1e-12)
    assert props["mean_r"] == pytest.approx(old.mean_r(), abs=1e-12)
    assert props["nodes"] == old.nodes()
    assert props["r_peak"] == old.r_peak()
    curve = wfn.sampled_w(path, points=3)[0]
    assert curve["r"][0] == old.r[0] and curve["r"][-1] == old.r[-1]
    assert len(curve["r"]) <= 3 and curve["points"] == len(old.r)


def test_compression_is_bounded():
    data = dict(result="text" * 20000)
    packed = remote.pack(data)
    assert b'"zlib"' in packed and len(packed) < 1000
    assert remote.unpack(packed) == data
    with pytest.raises(ValueError):
        remote.unpack(b'{"zlib":"bad"}')


def test_client_ignores_banner_pipelines_and_uses_cache(tmp_path):
    make_files(tmp_path, 10, 2)
    code = "print('welcome\\nmodule loaded',flush=True);import grasp_remote;grasp_remote.main([])"
    client = ssh.Client("test-host", command=[sys.executable, "-c", code],
                        cache=ssh.Cache("test", root=tmp_path / "cache"))
    try:
        a = client.request("list_dir", dict(directory=str(tmp_path)))
        b = client.request("view", dict(directory=str(tmp_path), kind="levels"), cached=True)
        assert a.result(timeout=10)["files"]
        value = b.result(timeout=10)
        assert value["data"]["levels"]
        again = client.request("view", dict(directory=str(tmp_path), kind="levels"), cached=True)
        assert again.result(timeout=10) == value
    finally:
        client.close()


def wait_for(qapp, predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return
        time.sleep(0.01)
    pytest.fail("GUI remote timeout")


@pytest.mark.gui
def test_existing_viewers_remote_data_without_local_reads(tmp_path, qapp, monkeypatch):
    from remote_panel import Source, RemoteWindow
    from PySide6.QtCore import Qt

    make_files(tmp_path, 100, 3)
    wavefunction(tmp_path / "state.w")
    config.install(document(), tmp_path / "calculation")
    client = ssh.Client("server-alias", command=[sys.executable, str(Path(ROOT) / "grasp_gui.py"), "remote"],
                        cache=ssh.Cache("gui", root=tmp_path / "cache"))
    calls = []
    request = client.request

    def recorded(method, *args, **kwargs):
        calls.append(method)
        return request(method, *args, **kwargs)
    monkeypatch.setattr(client, "request", recorded)
    source = Source(client)
    win = RemoteWindow(source, str(tmp_path))
    win.show()
    try:
        wait_for(qapp, lambda: win.levels.level_table.rowCount() == 3 and win.waves.table.rowCount() == 1
                 and not source.waiting)
        assert "server-alias:" + str(tmp_path) in win.windowTitle()
        assert win.waves.table.item(0, 8).data(Qt.DisplayRole) == 4
        assert win.calc.parameters.isReadOnly() and win.calc.preview.isReadOnly()
        count = len(calls)
        for key in win.levels.levels:
            win.levels.select(key)
            assert win.levels.jj_table.rowCount() > 0
            assert win.levels.level_desc(key)["energy"] < 0
        win.waves.reset_zoom()
        qapp.processEvents()
        assert len(calls) == count
        win.levels.more_composition()
        wait_for(qapp, lambda: not source.waiting)
        assert calls[count:] == ["composition"]
    finally:
        win.close()
        client.thread.join(timeout=5)


@pytest.mark.gui
def test_remote_readonly_pipeline_and_custom_confirmation(tmp_path, qapp, monkeypatch):
    from remote_panel import RemoteCalculation
    from PySide6.QtCore import QObject, Signal
    from PySide6.QtWidgets import QMessageBox

    class Source(QObject):
        push = Signal(object)

        def __init__(self):
            super().__init__()
            self.calls = []

        def submit(self, method, args, callback, **kwargs):
            self.calls.append((method, args))
            if method == "pipeline":
                callback(dict(jobs=config.session(document(True))["pipeline"], revision="rev"), None)
            elif method == "watch":
                callback(dict(text="old log", state=None), None)
            elif method == "run_plan":
                callback(dict(revision="rev", custom_commands=["sleep 30"], logout_kills=False, lingering=False), None)
    source = Source()
    panel = RemoteCalculation(source, "/server/calc")
    assert panel.parameters.isReadOnly() and "sleep" in panel.preview.toPlainText()
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.No)
    panel.prepare_run(1)
    assert not any(m == "run" for m, _ in source.calls)
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.Yes)
    panel.prepare_run(1)
    assert source.calls[-1][0] == "run"
    assert source.calls[-1][1]["allow_custom"] is True
    panel.push(dict(event="log", directory="/server/calc", text="new log", reset=True))
    assert panel.output.toPlainText() == "new log"
    panel.show_state(dict(status="running", step=0, titles=["step"]))
    assert not panel.run_button.isEnabled() and not panel.run_selected.isEnabled()


def test_user_scope_requires_explicit_logout_choice(tmp_path, monkeypatch):
    import grasp_bg as bg
    import grasp_session as sessions

    config.install(document(), tmp_path / "calc")
    op = remote.Operations()
    directory = str(tmp_path / "calc")
    revision = op.record(directory)[2]
    monkeypatch.setattr(op, "run_plan", lambda directory: dict(logout_kills=True, lingering=False))
    with pytest.raises(ValueError, match="scope"):
        op.run(directory, revision)
    with pytest.raises(ValueError, match="lingering"):
        op.run(directory, revision, user_scope=True)
    launched = []
    monkeypatch.setattr(service.Service, "run", lambda self, *a, **kw: launched.append(kw))
    op.run(directory, revision, accept_logout=True)
    assert launched == [dict(started_by="remote", user_scope=False)]
    monkeypatch.setattr(op, "run_plan", lambda directory: dict(logout_kills=True, lingering=True))
    op.run(directory, revision, user_scope=True)
    assert launched[-1]["user_scope"] is True
    command = []
    monkeypatch.setattr(bg.subprocess, "Popen", lambda argv, **kw: command.append((argv, kw)))
    bg.launch(directory, [], {}, user_scope=True)
    assert command[0][0][:5] == ["systemd-run", "--user", "--scope", "--quiet", "--"]
    assert command[0][1]["start_new_session"]
    assert command[0][1]["stdin"] == subprocess.DEVNULL


@pytest.mark.parametrize("method", ["list_dir", "run", "clear_slurm_record", "create_workdir", "update_workdir"])
def test_disconnect_retries_only_reads_and_bounds_reconnects(tmp_path, method):
    marker = tmp_path / "called"
    code = "\n".join([
        "import os,pathlib,grasp_remote as r",
        "s=r.Server()",
        f"marker=pathlib.Path({str(marker)!r})",
        "def operation(directory):",
        "    if not marker.exists():",
        "        marker.write_text('once')",
        "        os._exit(0)",
        "    marker.write_text(marker.read_text()+'twice')",
        "    return {'retried':True}",
        f"s.operation.{method}=operation",
        "s.serve()",
    ])
    client = ssh.Client("retry-test", command=[sys.executable, "-c", code],
                        cache=ssh.Cache(method, root=tmp_path / "cache"))
    try:
        future = client.request(method, dict(directory=str(tmp_path)))
        if method != "list_dir":
            with pytest.raises(ssh.UnknownOutcome):
                future.result(timeout=15)
            assert client.request("run_status", dict(directory=str(tmp_path))).result(timeout=15)["state"] is None
            assert marker.read_text() == "once"
        else:
            assert future.result(timeout=15) == dict(retried=True)
            assert marker.read_text() == "oncetwice"
    finally:
        client.close()


def test_radial_large_sparse_file_memory_is_bounded(tmp_path):
    import struct
    import tracemalloc

    count = 1000000
    path = tmp_path / "large.w"
    with path.open("wb") as f:
        def record(body):
            f.write(struct.pack("<i", len(body)) + body + struct.pack("<i", len(body)))
        def sparse(size):
            f.write(struct.pack("<i", size))
            f.seek(size, 1)
            f.write(struct.pack("<i", size))
        record(b"G92RWF")
        record(struct.pack("<iidi", 1, -1, -0.5, count))
        sparse(8 + 16 * count)
        sparse(8 * count)
    tracemalloc.start()
    try:
        curves = wfn.sampled_w(path, points=1000)
        assert len(curves[0]["r"]) == 1000
        assert tracemalloc.get_traced_memory()[1] < 3 << 20
    finally:
        tracemalloc.stop()
    assert path.stat().st_size > 24 << 19


@pytest.mark.parametrize("count", [1, 2, 4, 102, 8193, 16386])
def test_radial_streaming_matches_exponential_grid_and_node_filter(tmp_path, count):
    import math
    import struct

    path = tmp_path / "grid.w"
    r = [1e-6 * math.expm1(i * 0.001) for i in range(count)]
    p = [0.3 * math.sin(i / 15) * math.exp(-i / 5000) for i in range(count)]
    q = [v * 0.02 for v in p]
    def record(body):
        return struct.pack("<i", len(body)) + body + struct.pack("<i", len(body))
    path.write_bytes(b"".join(record(body) for body in (
        b"G92RWF", struct.pack("<iidi", 3, -1, -0.5, count),
        struct.pack(f"<{2 * count + 1}d", 0, *p, *q), struct.pack(f"<{count}d", *r))))
    old = wfn.read_w(path)[0]
    _, props = next(wfn.orbital_properties(path))
    assert props["norm"] == pytest.approx(old.norm(), rel=2e-12, abs=1e-20)
    assert props["mean_r"] == pytest.approx(old.mean_r(), rel=2e-12, abs=1e-20)
    assert props["nodes"] == old.nodes() and props["r_peak"] == old.r_peak()


def test_scope_failure_keeps_diagnostic_and_never_falls_back(tmp_path, monkeypatch):
    import grasp_bg as bg

    root = tmp_path / "calc"
    config.install(document(), root)
    calls = []
    class Process:
        def wait(self):
            return 1
    def failed(*args, **kwargs):
        calls.append(kwargs)
        kwargs["stderr"].write(b"Failed to connect to user bus: unavailable\n")
        kwargs["stderr"].flush()
        return Process()
    monkeypatch.setattr(bg, "launch", failed)
    monkeypatch.setattr(bg, "connect", lambda *a, **kw: None)
    with pytest.raises(ValueError, match="Failed to connect to user bus"):
        service.Service("/").run(str(root), user_scope=True)
    assert len(calls) == 1 and calls[0]["user_scope"]


def test_directory_browsing_retains_unreadable_entries(tmp_path, monkeypatch):
    (tmp_path / "private").write_text("private")
    op = remote.Operations()
    monkeypatch.setattr(op, "file_type", lambda path: (_ for _ in ()).throw(PermissionError("unreadable")))
    row = op.list_dir(str(tmp_path))["files"][0]
    assert row["name"] == "private" and row["type"] == "unreadable"


def test_server_pushes_utf8_and_resets_for_new_run(tmp_path):
    import threading
    import grasp_bg as bg
    import io

    server = remote.Server(target=io.BytesIO())
    server.watch(str(tmp_path))
    output = []
    server.send = output.append
    worker = threading.Thread(target=server.push)
    worker.start()
    try:
        path = tmp_path / bg.RUN_LOG
        raw = ("中" * 25000).encode()
        path.write_bytes(raw)
        deadline = time.monotonic() + 3
        while len([r for r in output if r["event"] == "log"]) < 2 and time.monotonic() < deadline:
            time.sleep(0.02)
        assert "".join(r["text"] for r in output if r["event"] == "log") == raw.decode()
        state = dict(start="new run", status="ok", pid=os.getpid(), host=__import__("socket").gethostname())
        (tmp_path / bg.RUN_STATE).write_text(json.dumps(state))
        deadline = time.monotonic() + 3
        while not any(r.get("reset") for r in output) and time.monotonic() < deadline:
            time.sleep(0.02)
        assert any(r.get("reset") for r in output)
    finally:
        server.cancel.set()
        worker.join(timeout=3)


@pytest.mark.gui
@pytest.mark.parametrize("choice,linger,expected", [("使用用户 scope", True, "scope"),
    ("照常启动", False, "normal"), ("导出 Slurm 作业脚本…", False, "export_script"), ("取消", True, None)])
def test_logout_dialog_requires_explicit_choice(qapp, monkeypatch, choice, linger, expected):
    import remote_panel as panel_module
    from PySide6.QtCore import QObject, Signal
    from PySide6.QtWidgets import QMessageBox

    class Source(QObject):
        push = Signal(object)

        def __init__(self):
            super().__init__()
            self.calls = []

        def submit(self, method, args, callback, **kwargs):
            self.calls.append((method, args))
            if method == "pipeline":
                callback(dict(jobs=config.session(document())["pipeline"], revision="rev"), None)
            elif method == "watch":
                callback(dict(text="", state=None), None)
            elif method == "run_plan":
                callback(dict(revision="rev", custom_commands=[], logout_kills=True, lingering=linger), None)

    class Box:
        AcceptRole, ActionRole, RejectRole = QMessageBox.AcceptRole, QMessageBox.ActionRole, QMessageBox.RejectRole

        def __init__(self, parent):
            self.buttons = {}

        def setWindowTitle(self, value):
            pass

        def setText(self, value):
            pass

        def addButton(self, text, role):
            self.buttons[text] = object()
            return self.buttons[text]

        def setDefaultButton(self, button):
            assert button is self.buttons["取消"]

        def exec(self):
            assert ("使用用户 scope" in self.buttons) == linger

        def clickedButton(self):
            return self.buttons[choice]
    monkeypatch.setattr(panel_module, "QMessageBox", Box)
    source = Source()
    panel = panel_module.RemoteCalculation(source, "/server/calculation")
    panel.prepare_run(1)
    methods = [m for m, _ in source.calls]
    if expected in ("scope", "normal"):
        assert methods[-1] == "run"
        assert source.calls[-1][1]["user_scope"] == (expected == "scope")
        assert source.calls[-1][1]["accept_logout"] == (expected == "normal")
    elif expected:
        assert methods[-1] == expected and "run" not in methods
    else:
        assert "run" not in methods and "export_script" not in methods


def test_banner_protocol_error_and_stderr_are_preserved(tmp_path):
    for code, expected in [("import sys;print('server loader failed',file=sys.stderr)", "server loader failed"),
                           ("print('GRASP_GUI_REMOTE 999',flush=True)", "远程协议不兼容")]:
        client = ssh.Client("bad-server", command=[sys.executable, "-c", code],
                            cache=ssh.Cache(code, root=tmp_path / "cache"))
        try:
            with pytest.raises(ConnectionError, match=expected):
                client.request("list_dir", dict(directory=str(tmp_path))).result(20)
        finally:
            client.close()


def test_request_pipelining_can_reply_out_of_order(tmp_path):
    code = "\n".join([
        "import time,grasp_remote as r",
        "s=r.Server()",
        "original=s.operation.list_dir",
        "def slow(directory):",
        "    time.sleep(0.5)",
        "    return original(directory)",
        "s.operation.list_dir=slow",
        "s.serve()",
    ])
    client = ssh.Client("pipeline", command=[sys.executable, "-c", code],
                        cache=ssh.Cache("pipeline", root=tmp_path / "cache"))
    try:
        slow = client.request("list_dir", dict(directory=str(tmp_path)))
        fast = client.request("run_status", dict(directory=str(tmp_path)))
        assert fast.result(timeout=10)["state"] is None
        assert not slow.done()
        assert slow.result(timeout=10)["directory"] == str(tmp_path)
    finally:
        client.close()
    with pytest.raises(ConnectionError):
        client.request("list_dir", dict(directory=str(tmp_path))).result(timeout=1)


def test_ssh_aliases_include_and_startup_quoting(tmp_path):
    (tmp_path / "extra").write_text("Host via-jump\n    ProxyJump gateway\n")
    path = tmp_path / "config"
    path.write_text('Host primary second *.invalid !excluded\nInclude extra\nHost "bad\n')
    assert ssh.hosts(path) == ["primary", "second", "via-jump"]
    path = tmp_path / "name with spaces;$(touch forbidden)"
    path.write_text("#!/bin/sh\nprintf 'test-version\\n'\n")
    path.chmod(0o700)
    result = subprocess.run(["sh", "-c", ssh.startup(str(path))], capture_output=True, text=True, cwd=tmp_path)
    assert result.returncode == 0 and "test-version" in result.stdout
    assert not (tmp_path / "forbidden").exists()
    with pytest.raises(ValueError, match="完整路径"):
        ssh.startup("relative")


def test_view_signature_invalidates_results_but_not_gui_log(tmp_path):
    import grasp_bg as bg

    path = make_files(tmp_path, 10, 1)
    op = remote.Operations()
    first = op.view(str(tmp_path), "levels")
    (tmp_path / bg.RUN_LOG).write_text("new log")
    assert op.view(str(tmp_path), "levels", known_signature=first["signature"])["unchanged"]
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1000000))
    changed = op.view(str(tmp_path), "levels", known_signature=first["signature"])
    assert changed["signature"] != first["signature"] and not changed.get("unchanged")


@pytest.mark.gui
def test_fake_server_directory_does_not_exist_on_desktop(tmp_path, qapp, monkeypatch):
    from PySide6.QtCore import QObject, Signal
    from types import SimpleNamespace
    from remote_panel import RemoteWindow
    import grasp_mix as mix

    make_files(tmp_path, 100, 2)
    wavefunction(tmp_path / "state.w")
    op = remote.Operations()
    data = {kind: op.view(str(tmp_path), kind) for kind in ("levels", "wfn")}
    data["levels"]["data"]["transitions"] = [dict(kind="M1", upper=["state.cm", 1, -1, 2],
        lower=["state.cm", 1, -1, 1], wavelength_angstrom=100, values={"M": [1e8, 0.1, 0.1]})]

    class Source(QObject):
        push, notice = Signal(object), Signal(str)

        def __init__(self):
            super().__init__()
            self.client = SimpleNamespace(host="remote.example")

        def submit(self, method, args, callback, **kwargs):
            value = {"pipeline": dict(jobs=config.session(document())["pipeline"], revision="revision"),
                     "watch": dict(text="server log", state=None)}
            callback(data[args["kind"]] if method == "view" else value[method], None)

        def close(self):
            pass

        def reconnect(self):
            pass

    def forbidden(*args, **kwargs):
        raise AssertionError("desktop must not read remote files")
    monkeypatch.setattr(wfn, "read_w", forbidden)
    monkeypatch.setattr(mix, "open_mix", forbidden)
    win = RemoteWindow(Source(), "/remote-only/not-a-local-directory")
    monkeypatch.setattr(win.levels, "workspace", forbidden)
    try:
        assert win.levels.level_table.rowCount() == 2 and win.waves.table.rowCount() == 1
        win.levels.select(next(iter(win.levels.levels)))
        assert win.levels.jj_table.rowCount() > 0
        keys = list(win.levels.levels)
        win.levels.connect_pair(*keys)
        assert win.levels.level_tr_table.rowCount() == 1
        win.levels.redraw()
        assert win.levels.marked and win.levels.diagram.arrows
        win.levels.export_grasp()
        assert "remote.example:/remote-only/" in win.windowTitle()
        assert "server log" in win.calc.output.toPlainText()
    finally:
        win.close()


def test_cache_identity_uses_discovered_executable(tmp_path):
    make_files(tmp_path, 10, 1)
    command = [sys.executable, "-c", "import grasp_remote;r=grasp_remote.Server();r.serve()"]
    root = None
    for program in ("", "/explicit/path/to/same/server"):
        client = ssh.Client("same-server", program, command=command)
        try:
            client.request("view", dict(directory=str(tmp_path), kind="levels"), cached=True).result(10)
            if root is None:
                root = client.cache.root
            else:
                assert client.cache.root == root
                assert any(e.get("cached") for e in list(client.events.queue))
        finally:
            client.close()


def test_ssh_version_probe_uses_clean_environment(monkeypatch):
    calls = []
    monkeypatch.setattr(core, "child_env", lambda: {"PATH": "/system/bin"})
    def run(argv, **kwargs):
        calls.append(kwargs)
        return subprocess.CompletedProcess(argv, 0, "", "OpenSSH_10.0p2")
    monkeypatch.setattr(ssh.subprocess, "run", run)
    assert ssh.modern_ssh()
    assert calls[0]["env"] == {"PATH": "/system/bin"}


def test_remote_summary_uses_server_pipeline_state_and_shared_renderer(tmp_path):
    import grasp_bg as bg
    import grasp_summary as summary
    import socket

    make_files(tmp_path, 100, 3)
    step = config.STEPS["rci"]
    jobs = [step.job(dict(step.defaults(), state="state"))]
    core.save_session(tmp_path, dict(pipeline=jobs))
    core.save_config(lang="zh")
    bg.write_state(tmp_path, dict(host=socket.gethostname(), pid=1, start="2026-01-01", status="ok",
                                  step=0, titles=[jobs[0]["title"]]))
    peer = Peer()
    try:
        selected = ["levels", "run_status", "configuration"]
        result = peer.call("summary", directory=str(tmp_path), per_block=1, sections=selected)["result"]
        assert result["has_configuration"]
        cfg = config.export(jobs, dict(title=tmp_path.name), validate=False)
        expected = summary.render(summary.collect(str(tmp_path), cfg, jobs, history=(), per_block=1), selected)
        def parts(text):
            rows = [json.loads(p.split("\n```", 1)[0]) for p in text.split("```json\n")[1:]]
            rows[0].pop("generated_at")
            return rows
        assert parts(result["text"]) == parts(expected)
        assert len(result["text"].encode("utf-8")) <= summary.MAX_BYTES
        assert peer.call("summary", directory=str(tmp_path), per_block=201).get("error")
        assert peer.call("summary", directory=str(tmp_path), sections=["exec"]).get("error")
        assert peer.call("summary", directory=str(tmp_path), sections=[]).get("error")
    finally:
        peer.close()


def test_remote_summary_without_saved_pipeline(tmp_path):
    make_files(tmp_path, 10, 2)
    result = remote.Operations().summary(str(tmp_path), per_block=1, sections=["levels"])
    assert result["has_configuration"] is False
    assert "state.cm" in result["text"]


@pytest.mark.gui
@pytest.mark.parametrize("finish", ["copy", "save", "cancel"])
def test_remote_summary_button_never_reads_desktop_results(tmp_path, qapp, monkeypatch, finish):
    import grasp_summary as summary
    import grasp_session as sessions
    from PySide6.QtCore import QObject
    from PySide6.QtWidgets import QPushButton, QDialog, QFileDialog
    from types import SimpleNamespace
    from remote_panel import RemoteLevels
    from summary_panel import SummaryDialog

    class FakeSource(QObject):
        def __init__(self):
            super().__init__()
            self.client = SimpleNamespace(host="remote.example")
            self.calls = []

        def submit(self, method, args, callback, **kwargs):
            self.calls.append((method, args))
            if method == "view":
                callback(dict(data=dict(levels=[], files=[])), None)
            else:
                assert method == "summary" and args["directory"] == "/server-only/calc"
                callback(dict(text="remote report\n" + json.dumps(args), has_configuration=True), None)

        def cancel(self, ident):
            pass
    source = FakeSource()
    panel = RemoteLevels(source, "/server-only/calc")
    def forbidden(*args, **kwargs):
        pytest.fail("remote summary must not inspect local result files")
    monkeypatch.setattr(summary, "collect", forbidden)
    monkeypatch.setattr(summary, "render", forbidden)
    monkeypatch.setattr(sessions, "read", forbidden)
    target = tmp_path / "saved.md"
    def save_file(parent, title, initial, filters):
        assert Path(initial).parent == Path.home()
        return str(target), ""
    monkeypatch.setattr(QFileDialog, "getSaveFileName", save_file)
    qapp.clipboard().setText("preserve clipboard")
    def preview(dialog):
        assert dialog.worker is None and not dialog.history
        assert "remote.example:/server-only/calc" in dialog.windowTitle()
        assert dialog.copy_button.isEnabled() and "remote report" in dialog.content
        dialog.sections["configuration"].setChecked(True)
        dialog.limit.setValue(9)
        calls = [args for method, args in source.calls if method == "summary"]
        assert len(calls) == 3 and calls[-1]["per_block"] == 9
        assert "configuration" in calls[-1]["sections"]
        content = dialog.content
        if finish == "copy":
            dialog.copy()
            assert qapp.clipboard().text() == content
        elif finish == "save":
            dialog.save()
            assert target.read_text() == content
            assert qapp.clipboard().text() == "preserve clipboard"
        else:
            dialog.reject()
            assert qapp.clipboard().text() == "preserve clipboard"
        return dialog.result()
    monkeypatch.setattr(SummaryDialog, "exec", preview)
    try:
        button = next(b for b in panel.findChildren(QPushButton) if b.text() == core.T("结果摘要…"))
        button.click()
        assert len([m for m, _ in source.calls if m == "summary"]) == 3
    finally:
        panel.shutdown()
        panel.close()


@pytest.mark.gui
def test_remote_summary_ignores_stale_and_closed_responses(qapp):
    from summary_panel import SummaryDialog

    calls, cancelled = [], []
    def load(n, sections, callback):
        calls.append(callback)
        ident = len(calls)
        return lambda: cancelled.append(ident)
    dialog = SummaryDialog(None, "/server-only/calc", None, [], loader=load)
    dialog.limit.setValue(7)
    calls[0](dict(text="stale", has_configuration=True), None)
    assert not dialog.copy_button.isEnabled() and dialog.content == ""
    calls[1](dict(text="current", has_configuration=False), None)
    assert dialog.content == "current" and not dialog.sections["configuration"].isEnabled()
    dialog.limit.setValue(8)
    calls[2](None, ValueError("server read failed"))
    assert not dialog.copy_button.isEnabled() and "server read failed" in dialog.preview.toPlainText()
    dialog.reject()
    calls[2](dict(text="after close", has_configuration=True), None)
    assert dialog.content != "after close"
    assert cancelled == [1, 2, 3]
