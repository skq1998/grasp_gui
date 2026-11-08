"""服务器入口复用原命令、没有 Qt 时的诊断与协议可用性。"""

import io
import json
import subprocess
import sys

import pytest

import grasp_config as config
import grasp_core as core
import grasp_mcp as mcp
import grasp_server as server
from conftest import ROOT


def command(args):
    code = ("import sys,runpy; sys.modules['PySide6'] = None; sys.modules['PySide2'] = None; "
            "runpy.run_path('grasp_server.py', run_name='__main__')")
    return subprocess.run([sys.executable, "-c", code, *args], cwd=ROOT, input="",
                          capture_output=True, text=True, timeout=10)


@pytest.mark.parametrize("args", [[], ["levels"], ["wfn", "/server/calc"], ["state.cm"], ["_askpass"]])
def test_gui_commands_have_server_diagnostic_without_qt(args):
    result = command(["--lang", "en", *args])
    assert result.returncode == 2 and not result.stdout
    assert "server build" in result.stderr and "desktop or legacy" in result.stderr
    assert "Traceback" not in result.stderr


def test_version_help_and_remote_handshake():
    result = command(["--version"])
    assert result.returncode == 0 and core.VERSION in result.stdout and "remote protocol 1" in result.stdout
    assert command(["--help"]).returncode == 0
    result = command(["remote"])
    assert result.returncode == 0 and result.stdout.startswith("GRASP_GUI_REMOTE 1\n")


@pytest.mark.parametrize("args", [["steps"], ["check", "demos/example1.json"],
                                  ["script", "demos/example1.json", "--bindir", "/opt/grasp/bin"]])
def test_config_commands_match_original(args, capsys):
    assert config.cli(args) == 0
    expected = capsys.readouterr()
    result = command(args)
    assert result.returncode == 0
    assert (result.stdout, result.stderr) == (expected.out, expected.err)


def test_reference_matches_original(tmp_path, capsys):
    from test_remote import make_files
    path = make_files(tmp_path, 10, 2)
    args = ["reference", str(path)]
    assert config.cli(args) == 0
    expected = capsys.readouterr().out
    result = command(args)
    assert result.returncode == 0 and result.stdout == expected


def test_run_dispatch_reuses_background_runner(tmp_path, monkeypatch):
    import grasp_bg
    seen = []
    monkeypatch.setattr(grasp_bg, "serve", lambda directory: seen.append(directory) or 7)
    assert server.main(["_run", str(tmp_path)]) == 7
    assert seen == [str(tmp_path)]


def test_mcp_server_dispatch_and_no_qt_plot_error(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "CONFIG_FILE", str(tmp_path / "settings.json"))
    core.save_config(mcp_enabled=True)
    request = dict(jsonrpc="2.0", id=1, method="initialize", params=dict(
        protocolVersion=mcp.PROTOCOLS[0], capabilities={}, clientInfo=dict(name="test", version="1")))
    stream = io.BytesIO((json.dumps(request) + "\n").encode())
    target = io.StringIO()
    original = mcp.Server.serve
    monkeypatch.setattr(mcp.Server, "serve", lambda self: original(self, stream, target))
    assert server.main(["mcp", "--root", str(tmp_path)]) == 0
    assert json.loads(target.getvalue())["result"]["serverInfo"]["version"] == core.VERSION
    monkeypatch.setitem(sys.modules, "PySide6", None)
    monkeypatch.setitem(sys.modules, "PySide6.QtCore", None)
    result = mcp.Server(str(tmp_path)).result("plot_levels", dict(directory=str(tmp_path)))
    assert result["isError"]
    assert "桌面版或旧系统版" in result["content"][0]["text"]
