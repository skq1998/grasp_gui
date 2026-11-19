"""审核中发现的独立回归：保持既有输入和协议格式。"""

import shlex
import subprocess
import json
import math
import struct

import pytest

import grasp_core as core
import grasp_config as config
import grasp_remote as remote
import grasp_mix as mix
import grasp_index as index
import grasp_service as service
import grasp_summary as summary
from test_large_files import make_files
from test_run_range import launches  # noqa: F401


@pytest.mark.parametrize("name", ["mpi", "MPI space's $HOME"])
def test_absolute_mpi_temporary_directory_is_not_prefixed_with_workdir(tmp_path, name):
    workdir = tmp_path / "work"
    workdir.mkdir()
    temporary = tmp_path / name
    script = core.shell_script([], "/opt/grasp/bin", str(workdir), "mpirun -np 2", str(temporary))
    result = subprocess.run(["sh", "-c", script + '\nprintf "%s" "$MPI_TMP"\n'],
                            capture_output=True, text=True, env=core.child_env(), timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout == str(temporary)
    assert temporary.is_dir()


@pytest.mark.parametrize("temporary", ["tmp_mpi", "relative mpi", ""])
def test_relative_mpi_script_remains_byte_identical(temporary):
    expected = ("#!/bin/sh\nset -x\n\nexport PATH=/opt/grasp/bin:$PATH\ncd .\n\n"
                'export MPI_TMP="$PWD"/' + shlex.quote(temporary or "tmp_mpi") + '\nmkdir -p "$MPI_TMP"\n'
                "# hwloc's GL plugin makes mpirun hang under WSLg\n"
                'export HWLOC_COMPONENTS="${HWLOC_COMPONENTS:--gl}"\n')
    assert core.shell_script([], "/opt/grasp/bin", ".", "mpirun -np 2", temporary) == expected


@pytest.mark.parametrize("directory", ["calc", "work/../calc", "~/calc"])
def test_remote_workstation_run_resolves_paths_consistently(tmp_path, monkeypatch, launches, directory):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "work").mkdir()
    root = tmp_path / "calc"
    config.install(dict(format=config.FORMAT, version=config.VERSION,
                        jobs=[dict(step="rnucleus", vals=config.STEPS["rnucleus"].defaults())]), str(root))
    operation = remote.Operations()
    revision = operation.record(directory)[2]
    result = operation.run(directory, revision)
    assert result["started"] and result["directory"] == str(root)
    assert launches[0][1]["cwd"] == str(root)


@pytest.mark.parametrize("language", ["zh", "en"])
@pytest.mark.parametrize("block,index", [(2, 1), (1, 2)])
def test_missing_composition_has_a_clear_error(tmp_path, language, block, index):
    core.set_lang(language)
    make_files(tmp_path, ncf=2, nev=1)
    message = "块或能级不存在" if language == "zh" else "Block or level does not exist"
    with pytest.raises(ValueError, match=message):
        remote.Operations().composition(str(tmp_path), "state.cm", block, index)


def test_remote_composition_error_does_not_break_later_requests(tmp_path):
    from test_remote import Peer
    make_files(tmp_path, ncf=2, nev=1)
    peer = Peer()
    try:
        result = peer.call("composition", directory=str(tmp_path), file="state.cm", block=2, index=1)
        assert result.get("error") and "块或能级不存在" in result["error"]
        result = peer.call("composition", directory=str(tmp_path), file="state.cm", block=1, index=1)
        assert result["result"]["components"]
    finally:
        peer.close()


@pytest.mark.parametrize("validate", [True, False])
@pytest.mark.parametrize("changes", [
    dict(pre=[1]), dict(args=5), dict(stdin=5), dict(pre=[["run"]]),
    dict(title=5), dict(log=None), dict(mpi=1), dict(args="argument"), dict(args=[1]),
    dict(pre=None), dict(post=5), dict(pre=[["cp", "a"]]), dict(post=[["cp", "a", 3]]),
    dict(pre=[["run", "echo"]]), dict(pre=[["run", []]]), dict(pre=[["run", ["echo", 1]]]),
    dict(post=[["run", ["echo"], 5]]), dict(post=[["run", ["echo"], "input", "extra"]]),
    dict(pre=[["unknown", "a", "b"]]),
])
def test_malformed_commands_raise_config_error(changes, validate):
    cfg = dict(format=config.FORMAT, version=config.VERSION, jobs=[dict(command=dict(prog="x", **changes))])
    with pytest.raises(config.ConfigError):
        config.check(cfg, validate=validate)


@pytest.mark.parametrize("value", [5, None, "abc", {}])
def test_malformed_attached_files_raise_config_error(value):
    cfg = dict(format=config.FORMAT, version=config.VERSION,
               jobs=[dict(command=dict(prog="x"))], files=value)
    with pytest.raises(config.ConfigError):
        config.check(cfg)


@pytest.mark.parametrize("value", ["abc", [1, 2], None, {}])
@pytest.mark.parametrize("validate", [True, False])
def test_malformed_csf_lists_raise_config_error(value, validate):
    cfg = dict(format=config.FORMAT, version=config.VERSION,
               jobs=[dict(step="rcsfgenerate", vals=dict(config.STEPS["rcsfgenerate"].defaults(), lists=value))])
    with pytest.raises(config.ConfigError):
        config.check(cfg, validate=validate)


def test_legacy_command_actions_still_roundtrip_through_export():
    command = dict(prog="printf", args=["%s", "message"], title="legacy", stdin="", log="", mpi=False,
                   pre=[["cp", "source", "target"], ["run", ["printf", "before"], "input\n"]],
                   post=[["run", ["printf", "after"]]])
    cfg, _ = config.check(dict(format=config.FORMAT, version=config.VERSION, jobs=[dict(command=command)]))
    pipeline = config.session(cfg)["pipeline"]
    exported = config.export(pipeline, {})
    restored, _ = config.check(exported)
    assert config.session(restored)["pipeline"] == pipeline
    assert config.custom_commands(restored)[0][0] == 1
    assert "cp source target" in core.shell_script(pipeline, "/opt/grasp/bin", ".")


def nonfinite_files(root, value=float("nan"), second_level=False):
    healthy = make_files(root, ncf=2, nev=2)
    raw = healthy.read_bytes()
    old = struct.pack("<d", 0.001 if second_level else -7.4)
    bad = root / "bad.cm"
    bad.write_bytes(raw.replace(old, struct.pack("<d", value), 1))
    return healthy, bad


def mixed_warning_files(root):
    """一个正常文件、一个 NaN 文件和真实缺块文件的未写完副本。"""
    from test_skipped_blocks import DATA
    healthy, bad = nonfinite_files(root)
    source = DATA / "mid.cm"
    with source.open("rb") as stream:
        end = list(index.record_index(stream))[5]["end"]
    partial = root / "partial.cm"
    partial.write_bytes(source.read_bytes()[:end])
    (root / "skip1.c").write_bytes((DATA / "skip1.c").read_bytes())
    return healthy, bad, partial


@pytest.mark.parametrize("brief", [False, True])
def test_level_results_keep_errors_and_partial_files_separate(tmp_path, brief):
    healthy, bad, partial = mixed_warning_files(tmp_path)
    result = service.Service(str(tmp_path)).levels(".", brief=brief)
    assert {row["file"] for row in result["levels"]} == {healthy.name, partial.name}
    assert [row["file"] for row in result["errors"]] == [bad.name]
    assert result["partial_files"] == [partial.name]


@pytest.mark.parametrize("value,second_level", [(float("nan"), False), (float("inf"), False),
                                               (-float("inf"), False), (float("nan"), True)])
@pytest.mark.parametrize("prepared", [False, True])
@pytest.mark.parametrize("cache_writable", [False, True])
def test_nonfinite_energy_rejects_whole_mix_file(tmp_path, monkeypatch, value, second_level, prepared, cache_writable):
    if not cache_writable:
        monkeypatch.setattr(index, "cache_dirs", lambda *args: [])
    healthy, bad = nonfinite_files(tmp_path, value, second_level)
    with pytest.raises(ValueError, match="能量不是有限的数"):
        mix.open_mix(bad, summary=prepared)
    assert all(math.isfinite(lv.energy) for b in mix.open_mix(healthy) for lv in b.levels)


@pytest.mark.parametrize("brief", [False, True])
def test_nonfinite_file_isolated_in_service_views_and_summaries(tmp_path, brief):
    healthy, bad = nonfinite_files(tmp_path)
    result = service.Service("/").levels(str(tmp_path), brief=brief)
    assert len(result["levels"]) == 2 and {row["file"] for row in result["levels"]} == {healthy.name}
    assert result["errors"] == [dict(file=bad.name, error=core.T("能量不是有限的数，计算可能已经发散"))]
    json.dumps(result, allow_nan=False)
    local = mix.load_result_files([str(healthy), str(bad)], 5, str(tmp_path))
    assert isinstance(local[str(bad)], ValueError) and not isinstance(local[str(healthy)], Exception)
    data = summary.collect(tmp_path, None, [])
    assert {row["file"] for row in data["levels"]} == {healthy.name}
    assert any(row["file"] == bad.name and "能量不是有限的数" in row["error"] for row in data["warnings"])
    emitted = []
    view = remote.Operations().view(str(tmp_path), "levels", emit=emitted.append)
    assert {row["file"] for row in view["data"]["levels"]} == {healthy.name}
    for payload in [view, *emitted]:
        remote.pack(payload)


def test_mcp_levels_reports_nonfinite_file_without_losing_other_results(tmp_path, monkeypatch):
    import grasp_mcp as mcp
    healthy, bad = nonfinite_files(tmp_path)
    monkeypatch.setattr(core, "CONFIG_FILE", str(tmp_path / "isolated-mcp.json"))
    core.save_config(mcp_enabled=True)
    result = mcp.Server(str(tmp_path)).result("levels", dict(directory=str(tmp_path)))
    assert not result["isError"], result
    data = json.loads(result["content"][0]["text"])
    assert {row["file"] for row in data["levels"]} == {healthy.name}
    assert data["errors"][0]["file"] == bad.name
