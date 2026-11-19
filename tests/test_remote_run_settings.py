"""桌面运行设置只用于本次服务器请求，不读取或覆盖服务器偏好。"""

from pathlib import Path

import pytest

import grasp_core as core
import grasp_config as config
import grasp_remote as remote
import grasp_session as sessions
from test_run_range import install, launches  # noqa: F401
from test_summary import job


@pytest.mark.parametrize("settings", [
    [], {"extra": 1}, {"bindir": "relative"}, {"bindir": "/nonexistent/grasp/bin"},
    {"bindir": 5}, {"bindir": "/tmp\n"}, {"np": True}, {"np": "2"}, {"np": 0}, {"np": 100001},
    {"mpirun": 5}, {"mpirun": "mpirun\n"}, {"mpitmp": "tmp\x00mpi"},
    {"mpitmp": "x" * 4097}, {"nice": 1},
])
@pytest.mark.parametrize("method", ["run_plan", "run", "preview_upload"])
def test_invalid_run_settings_block_runs_but_only_warn_in_upload_preview(tmp_path, launches, settings, method):
    cfg = config.export([job("rnucleus")], {})
    directory = tmp_path / "calc"
    revision = install(directory, [job("rnucleus")])
    args = dict(directory=str(directory), run_settings=settings)
    if method == "run":
        args["revision"] = revision
    elif method == "preview_upload":
        args.update(directory=str(tmp_path / "new"), configuration=cfg)
    if method == "preview_upload":
        result = remote.Operations().preview_upload(**args)
        assert result["run_settings_warning"]
        assert not Path(args["directory"]).exists()
    else:
        with pytest.raises(ValueError):
            getattr(remote.Operations(), method)(**args)
    assert not launches


@pytest.mark.parametrize("settings", [{"bindir": "", "np": 1}, {"np": 1}, None])
def test_remote_start_never_passes_an_empty_bin_directory(tmp_path, monkeypatch, launches, settings):
    monkeypatch.setattr(core, "CONFIG_FILE", str(tmp_path / "preferences.json"))
    absent = str(tmp_path / "not-installed")
    monkeypatch.setattr(core, "default_bindir", lambda **kwargs: absent)
    core.save_config(np=1)
    original = Path(core.CONFIG_FILE).read_bytes()
    root = tmp_path / "calc"
    revision = install(root, [job("rnucleus")])
    op = remote.Operations()
    plan = op.run_plan(str(root), run_settings=settings)
    if settings is None or settings.get("bindir") == "":
        assert plan["run_settings"]["bindir"] == ""
    op.run(str(root), revision, run_settings=settings, accept_logout=True)
    assert launches[0][1]["bindir"] == absent
    assert Path(core.CONFIG_FILE).read_bytes() == original


def test_invalid_upload_settings_use_server_defaults_without_saving_them(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "CONFIG_FILE", str(tmp_path / "preferences.json"))
    bindir = tmp_path / "bin"
    bindir.mkdir()
    program = bindir / "rnucleus"
    program.write_text("#!/bin/sh\n")
    program.chmod(0o755)
    core.save_config(bindir=str(bindir), np=1)
    original = Path(core.CONFIG_FILE).read_bytes()
    cfg = config.export([job("rnucleus")], {})
    root = tmp_path / "new"
    value = remote.Operations().preview_upload(str(root), cfg, run_settings=dict(bindir="/removed/bin"))
    assert value["run_settings_warning"] and value["missing_programs"] == []
    assert not root.exists() and Path(core.CONFIG_FILE).read_bytes() == original


def test_explicit_settings_never_read_or_write_server_preferences(tmp_path, monkeypatch, launches):
    root = tmp_path / "calc"
    revision = install(root, [job("rnucleus")])
    bindir = tmp_path / "bin"
    bindir.mkdir()
    executable = bindir / "rnucleus"
    executable.write_text("#!/bin/sh\n")
    executable.chmod(0o755)
    settings = dict(bindir=str(bindir), np=2, mpirun="/usr/bin/mpirun", mpitmp="scratch", nice=False)
    def forbidden(*args, **kwargs):
        pytest.fail("server preferences were accessed")
    monkeypatch.setattr(core, "load_config", forbidden)
    monkeypatch.setattr(core, "save_config", forbidden)
    op = remote.Operations()
    plan = op.run_plan(str(root), run_settings=settings)
    assert plan["run_settings"] == settings and not plan["missing_programs"]
    cfg = config.export([job("rnucleus")], {})
    preview = op.preview_upload(str(tmp_path / "new"), cfg, run_settings=settings)
    assert not preview["missing_programs"]
    result = op.run(str(root), revision, run_settings=settings)
    assert result["started"]
    assert launches[0][1] == dict(settings, cwd=str(root))
    saved, actual_revision = sessions.read(root)
    assert actual_revision == result["revision"] and actual_revision != revision
    assert {key: saved[key] for key in ("np", "mpirun", "mpitmp", "nice")} == {
        key: settings[key] for key in ("np", "mpirun", "mpitmp", "nice")}
    assert "bindir" not in saved and str(bindir) not in (root / "grasp_run/run.sh").read_text()
    launches.clear()  # 模拟首次运行结束，可以用同样的设置再次启动。
    same = op.run(str(root), actual_revision, run_settings=settings)
    assert same["revision"] == actual_revision


def test_remote_plan_is_readonly_and_default_run_still_uses_saved_values(tmp_path, monkeypatch, launches):
    preferences = tmp_path / "preferences.json"
    monkeypatch.setattr(core, "CONFIG_FILE", str(preferences))
    root = tmp_path / "calc"
    revision = install(root, [job("rnucleus")])
    op = remote.Operations()
    op.run_plan(str(root))
    assert not preferences.exists()
    core.save_config(bindir=str(tmp_path), np=1, mpirun="my-mpirun", mpitmp="saved", nice=False)
    original = preferences.read_bytes()
    op.run(str(root), revision)
    assert launches[0][1]["mpitmp"] == "saved"
    assert preferences.read_bytes() == original


def test_mpi_program_checks_follow_selected_range(tmp_path, monkeypatch):
    root = tmp_path / "calc"
    install(root, [job("rnucleus"), job("rangular")])
    monkeypatch.setattr(core, "program_path", lambda bindir, program:
                        "/bin/" + program if program == "rnucleus" else None)
    settings = dict(bindir="", np=2, mpirun="/missing/mpirun")
    op = remote.Operations()
    assert op.run_plan(str(root), end=1, run_settings=settings)["missing_programs"] == []
    plan = op.run_plan(str(root), start=2, run_settings=settings)
    assert plan["missing_programs"] == ["rangular_mpi", "/missing/mpirun"]
    assert plan["checked"]["missing"]


def test_settings_capability_and_tilde_resolution(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "bin").mkdir()
    root = tmp_path / "calc"
    install(root, [job("rnucleus")])
    op = remote.Operations()
    assert "run_settings_v1" in op.hello(1)["capabilities"]
    plan = op.run_plan(str(root), run_settings=dict(bindir="~/bin", np=100000))
    assert Path(plan["run_settings"]["bindir"]) == tmp_path / "bin"
    assert plan["run_settings"]["np"] == 100000


def test_mpi_launcher_uses_the_same_bin_and_path_search_as_the_runner(tmp_path, monkeypatch):
    root, bindir = tmp_path / "calc", tmp_path / "bin"
    install(root, [job("rangular")])
    bindir.mkdir()
    for name in ("rangular_mpi", "mpirun"):
        program = bindir / name
        program.write_text("#!/bin/sh\n")
        program.chmod(0o755)
    monkeypatch.setenv("PATH", "")
    settings = dict(bindir=str(bindir), np=2, mpirun="mpirun")
    assert not remote.Operations().run_plan(str(root), run_settings=settings)["missing_programs"]
