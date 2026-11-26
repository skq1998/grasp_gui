"""连接检查的程序目录、旧版本兼容性和重新检查。"""

from collections import Counter
from concurrent.futures import Future
import os
from pathlib import Path
from queue import Queue
from types import SimpleNamespace

import pytest

import grasp_config as config
import grasp_core as core
import grasp_remote as remote
import grasp_ssh as ssh
from test_remote_edit_ssh import call, energies, peer, target, wait_done  # noqa: F401
from test_remote_run_settings import install, job, launches  # noqa: F401


def install_program(directory, name):
    directory.mkdir(exist_ok=True)
    program = directory / name
    program.write_text("#!/bin/sh\n")
    program.chmod(0o700)


@pytest.mark.parametrize("source", ["env", "saved", "path", None])
def test_hello_reports_same_directory_as_run_plan(tmp_path, monkeypatch, source):
    env_bin, saved_bin, path_bin = [tmp_path / name for name in ("env", "saved", "path")]
    monkeypatch.setattr(core, "CONFIG_FILE", str(tmp_path / "settings.json"))
    monkeypatch.setattr(core, "REPO_DIR", str(tmp_path))
    monkeypatch.delenv("GRASP_BINDIR", raising=False)
    monkeypatch.setenv("PATH", str(path_bin))
    if source:
        install_program(path_bin, "rmcdhf")
    if source in ("env", "saved"):
        install_program(saved_bin, "rmcdhf")
        core.save_config(bindir=str(saved_bin))
    if source == "env":
        install_program(env_bin, "rmcdhf")
        install_program(env_bin, "rci_mpi")
        monkeypatch.setenv("GRASP_BINDIR", str(env_bin))
    calc = tmp_path / "calc"
    install(calc, [job("rnucleus")])
    operations = remote.Operations()
    hello = operations.hello(remote.PROTOCOL)
    report = hello["grasp_check"]
    assert remote.GRASP_CHECK in hello["capabilities"]
    assert report["source"] == source
    assert report["directory"] == operations.run_plan(str(calc))["run_settings"]["bindir"]
    assert report["bindir_env"] == (str(env_bin) if source == "env" else "")
    assert report["path_env"] == str(path_bin)
    assert len(report["programs"]) == 9
    assert report["programs"]["rmcdhf"] is bool(source)
    assert report["programs"]["rci_mpi"] is (source == "env")
    if source == "saved":
        # Explicitly clearing the desktop field uses the server environment.
        automatic = operations.run_plan(str(calc), run_settings={"bindir": ""})["run_settings"]["bindir"]
        assert report["empty_bindir"]["directory"] == automatic == str(path_bin)


STANDARD_PROGRAMS = ("rnucleus", "rcsfgenerate", "rangular", "rwfnestimate", "rmcdhf", "rci",
                     "rangular_mpi", "rmcdhf_mpi", "rci_mpi")


def test_hello_accepts_standard_grasp_and_reuses_one_path_scan(tmp_path, monkeypatch):
    installed, first, second = (tmp_path / part for part in ("installed", "first", "second"))
    for name in STANDARD_PROGRAMS:
        install_program(installed, name)
    install_program(second, "rmcdhf")
    first.mkdir()
    monkeypatch.setattr(core, "CONFIG_FILE", str(tmp_path / "settings.json"))
    monkeypatch.delenv("GRASP_BINDIR", raising=False)
    monkeypatch.setenv("PATH", os.pathsep.join(map(str, (first, second, first))))
    core.save_config(bindir=str(installed))
    listdir = os.listdir
    reads = Counter()

    def counted(path):
        reads[path] += 1
        return listdir(path)

    monkeypatch.setattr(os, "listdir", counted)
    report = remote.Operations().hello(remote.PROTOCOL)["grasp_check"]
    assert report["programs"] == dict.fromkeys(STANDARD_PROGRAMS, True)
    assert report["path_programs"] == ["rmcdhf"]
    assert report["empty_bindir"]["directory"] == str(second)
    assert report["empty_bindir"]["path_programs"] == ["rmcdhf"]
    assert reads == {str(first): 1, str(second): 1}


def test_batch_lookup_matches_individual_programs(tmp_path, monkeypatch):
    bindir, first, second, cwd = (tmp_path / part for part in ("bin", "first", "second", "cwd"))
    for path in (bindir, first, second, cwd):
        path.mkdir()
    for path in (bindir, first, second):
        install_program(path, "local_first")
    for path in (first, second):
        install_program(path, "path_first")
    (first / "usable").write_text("not executable")
    (first / "directory").mkdir()
    install_program(second, "usable")
    install_program(second, "directory")
    install_program(second, "cwd_first")
    install_program(cwd, "cwd_first")
    install_program(bindir, "jj2lsj_2025")
    install_program(first, "jj2lsj")
    monkeypatch.chdir(cwd)
    monkeypatch.setenv("PATH", os.pathsep.join([str(first), "", str(tmp_path / "missing"), str(second)]))
    names = ("local_first", "path_first", "usable", "directory", "cwd_first", "jj2lsj", "absent",
             str(second / "usable"), "./cwd_first")
    expected = {name: core.program_path(str(bindir), name) for name in names}
    assert core.program_paths(str(bindir), iter(names)) == expected
    assert expected["cwd_first"] == "cwd_first"
    assert expected["jj2lsj"] == str(first / "jj2lsj")
    (first / "jj2lsj").unlink()
    assert core.program_paths(str(bindir), ["jj2lsj"]) == {"jj2lsj": str(bindir / "jj2lsj_2025")}
    monkeypatch.setenv("PATH", "")
    assert core.program_paths(str(bindir), ["cwd_first"]) == {"cwd_first": None}


@pytest.mark.gui
@pytest.mark.parametrize("field", ["programs", "missing_files"])
def test_older_server_reports_hide_nonexistent_mpi_programs(field):
    pytest.importorskip("PySide6.QtWidgets")
    from remote_panel import grasp_check_text

    obsolete = ["rnucleus_mpi", "rcsfgenerate_mpi", "rwfnestimate_mpi"]
    report = dict(directory="/grasp/bin", source="saved")
    source = SimpleNamespace(server=dict(capabilities=[remote.GRASP_CHECK]), grasp_check=report)
    report[field] = dict.fromkeys(obsolete + ["rci_mpi"], False) if field == "programs" else obsolete + ["rci_mpi"]
    text, _ = grasp_check_text(source, "workstation")
    assert "rci_mpi" in text and all(name not in text for name in obsolete)


@pytest.mark.gui
def test_grasp_check_refreshes_from_settings_and_reconnects(qapp, tmp_path, monkeypatch):
    from remote_panel import Source, grasp_check_text

    monkeypatch.setattr(core, "CONFIG_FILE", str(tmp_path / "settings.json"))
    monkeypatch.setattr(core, "LANG", "en")
    requests = []

    def request(method, args, cached=False):
        future = Future()
        future.request_id = len(requests) + 1
        requests.append((method, args, future))
        return future

    client = SimpleNamespace(host="fixture", events=Queue(), request=request, close=lambda **kw: None)
    source = Source(client)
    report = dict(directory="", source=None, programs=dict(rmcdhf=False, rci_mpi=False), path_programs=[],
                  bindir_env="", path_env="/no-grasp")
    try:
        client.events.put(dict(event="connected", capabilities=[]))
        source.poll()
        qapp.processEvents()
        assert "Update it" in grasp_check_text(source, "workstation")[0]
        client.events.put(dict(event="connected", capabilities=[remote.GRASP_CHECK], grasp_check=report))
        source.poll()
        qapp.processEvents()
        text, settings = grasp_check_text(source, "slurm")
        assert settings and "PATH=/no-grasp" in text and "login node" in text
        assert "Non-interactive SSH" in text
        core.save_config(remote_hosts={"fixture": dict(run=dict(bindir="~/grasp/bin"))})
        source.refresh_grasp_check()
        method, args, future = requests[-1]
        assert (method, args) == ("list_dir", {"directory": "~/grasp/bin"})
        future.set_result(dict(directory="/home/research/grasp/bin",
                               files=[dict(name="rmcdhf", directory=False)]))
        source.poll()
        qapp.processEvents()
        text, settings = grasp_check_text(source, "workstation")
        assert not settings and "/home/research/grasp/bin" in text and "settings saved" in text
        assert "Program files not found: rci_mpi" in text
        env = dict(report, source="env", directory="/from-env", programs=dict(rmcdhf=True))
        client.events.put(dict(event="connected", capabilities=[remote.GRASP_CHECK], grasp_check=env))
        source.poll()
        qapp.processEvents()
        method, args, future = requests[-1]
        assert (method, args) == ("list_dir", {"directory": "~/grasp/bin"})
        assert not future.done()
        future.set_result(dict(directory="/home/research/grasp/bin",
                               files=[dict(name="rmcdhf", directory=False)]))
        source.poll()
        qapp.processEvents()
        assert "/home/research/grasp/bin (settings saved for this host)" in grasp_check_text(source, "workstation")[0]
        core.save_config(remote_hosts={"fixture": dict(run=dict(bindir=""))})
        source.refresh_grasp_check()
        assert "/from-env (from GRASP_BINDIR)" in grasp_check_text(source, "workstation")[0]
        assert len(requests) == 2
    finally:
        source.close()


@pytest.mark.gui
@pytest.mark.parametrize("blank", [False, True])
def test_status_run_plan_and_launch_agree_with_saved_bindir(qapp, tmp_path, monkeypatch, launches, blank):
    from remote_panel import ServerRunDialog, Source, grasp_check_text

    env_bin, saved_bin, path_bin = [tmp_path / name for name in ("env", "saved", "path")]
    install_program(env_bin, "rmcdhf")
    install_program(env_bin, "rnucleus")
    install_program(saved_bin, "rnucleus")
    install_program(saved_bin, "rmcdhf")
    install_program(path_bin, "rmcdhf")
    monkeypatch.setenv("GRASP_BINDIR", str(env_bin))
    monkeypatch.setenv("PATH", str(path_bin))
    monkeypatch.setattr(core, "CONFIG_FILE", str(tmp_path / "settings.json"))
    monkeypatch.setattr(core, "LANG", "en")
    client = SimpleNamespace(host="fixture", identity="researcher@server:2200", events=Queue(), close=lambda **kw: None)
    saved_bindir = "" if blank else str(saved_bin)
    expected = str((env_bin if blank else saved_bin).resolve())
    origin = "from GRASP_BINDIR" if blank else "settings saved for this host"
    core.save_config(np=1)
    ssh.save_host_profile(client, run=dict(bindir=saved_bindir, np=1))
    calc = tmp_path / "calc"
    revision = install(calc, [job("rnucleus")])
    operations = remote.Operations()
    report = operations.hello(remote.PROTOCOL)
    assert report["grasp_check"]["directory"] == str(env_bin)

    def request(method, args, cached=False):
        future = Future()
        future.request_id = 1
        future.set_result(getattr(operations, method)(**args))
        return future

    client.request = request
    source = Source(client)
    try:
        client.events.put(dict(event="connected", **report))
        source.poll()
        qapp.processEvents()
        source.poll()
        qapp.processEvents()
        status, _ = grasp_check_text(source, "workstation")
        dialog = ServerRunDialog(None, source, str(calc), operations.run_plan(str(calc)), {}, run=False)
        try:
            assert dialog.settings.bindir() == saved_bindir
            dialog.check()
            source.poll()
            qapp.processEvents()
            actual = dialog.plan["run_settings"]["bindir"]
            assert actual == expected
            assert f"{actual} ({origin})" in status
            dialog.confirm()
            assert ssh.host_profile(client)["run"]["bindir"] == saved_bindir
            operations.run(str(calc), revision, accept_logout=True, run_settings=dialog.settings.values())
            assert launches[0][1]["bindir"] == actual
            assert core.program_path(launches[0][1]["bindir"], "rmcdhf") == str(Path(expected) / "rmcdhf")
        finally:
            dialog.close()
    finally:
        source.close()


@pytest.mark.grasp
@pytest.mark.gui
@pytest.mark.skipif(os.environ.get("GRASP_REMOTE_EDIT_TEST") != "1" or not os.environ.get("GRASP_SSH_TEST_HOST"),
                    reason="dedicated GRASP check SSH fixture not configured")
def test_real_ssh_blank_bindir_uses_discovered_grasp_for_example1(peer, qapp, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QDialog
    from remote_panel import ServerRunDialog, Source
    from test_gui import spin, window

    monkeypatch.setattr(core, "CONFIG_FILE", str(tmp_path / "settings.json"))
    monkeypatch.setattr(core, "LANG", "en")
    cfg, _ = config.load(Path(__file__).resolve().parents[1] / "demos/example1.json")
    directory = target()
    call(peer, "create_workdir", directory, configuration=cfg)
    ssh.save_host_profile(peer, run=dict(bindir="", np=1))
    plan = call(peer, "run_plan", directory, run_settings={"bindir": "", "np": 1})
    source = Source(peer)
    main = window(tmp_path)
    submitted = []
    original_request = peer.request

    def request(method, args=None, **kwargs):
        if method == "run":
            submitted.append(args["run_settings"]["bindir"])
        return original_request(method, args, **kwargs)

    def accept(dialog):
        values = dialog.settings.values()
        assert values["bindir"] == ""
        dialog.plan = call(peer, "run_plan", directory, run_settings=values)
        assert dialog.plan["run_settings"]["bindir"] == source.grasp_check["directory"]
        assert not dialog.plan["missing_programs"]
        dialog.checked_values = values
        dialog.confirm()
        return QDialog.Accepted

    monkeypatch.setattr(peer, "request", request)
    monkeypatch.setattr(ServerRunDialog, "exec", accept)
    try:
        workspace = main.open_remote_workspace(source, directory)
        spin(qapp, lambda: source.grasp_check is not None and workspace.calc.run_button.isEnabled())
        assert source.grasp_check["source"] == "env"
        assert source.grasp_check["directory"] == plan["run_settings"]["bindir"]
        assert "from GRASP_BINDIR" in workspace.grasp_status.text()
        if os.environ.get("GRASP_SSH_BINDIR_ONLY") == "1":
            assert "rmcdhf" not in source.grasp_check["path_programs"]
        assert ssh.host_profile(peer)["run"]["bindir"] == ""
        workspace.calc.run_button.click()
        spin(qapp, lambda: (workspace.calc.runtime_state or {}).get("status") == "ok", timeout=90)
        wait_done(peer, directory)
        assert submitted == [""] and ssh.host_profile(peer)["run"]["bindir"] == ""
        for reference in cfg["reference"]:
            assert energies(peer, directory, reference["file"]) == pytest.approx(reference["energies"], abs=1e-7)
    finally:
        main.close()
