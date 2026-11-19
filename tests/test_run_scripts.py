"""工作目录派生脚本、记录提交和真实 GRASP 重算。"""

import os
from pathlib import Path
import shutil
import subprocess

import pytest

from conftest import DEMOS
import grasp_config as config
import grasp_core as core
import grasp_run_scripts as scripts
import grasp_session as sessions
import grasp_workdir as work


def nucleus(z="3"):
    return dict(format=config.FORMAT, version=config.VERSION, jobs=[dict(step="rnucleus", vals={"z": z})])


def test_every_demo_shares_the_exact_describe_body():
    settings = dict(bindir="", np=1, mpirun="mpirun", mpitmp="tmp_mpi", nice=False)
    for file in sorted(Path(DEMOS).glob("*.json")):
        cfg, _ = config.load(file)
        data = config.session(cfg)
        text = scripts.run_text(data, "a" * 64, settings)
        for i, job in enumerate(data["pipeline"], 1):
            block = text.split(f"\nstep={i}\n", 1)[1]
            assert block.startswith(core.describe(job, fallback=False) + "\nfi\n"), (file, i)
            if job.get("mpi"):
                assert f"{job['prog']}() {{" in text
        assert "\n#!/bin/bash" not in text
        subprocess.run(["sh", "-n"], input=text, text=True, check=True)


def test_install_cli_language_switch_and_moved_workdir(tmp_path, monkeypatch, capsys):
    root = tmp_path / "original"
    config.install(nucleus(), root)
    data, revision = sessions.read(root)
    assert "bindir" not in data and (root / scripts.DIRECTORY).is_dir()
    paths = [root / scripts.DIRECTORY / name for name in scripts.SCRIPTS]
    first = {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths}
    assert all(f"# Record revision: {revision}".encode() in raw for raw, _ in first.values())
    record = root / core.SESSION_FILE
    raw_record = record.read_bytes()
    os.utime(record, ns=(1, 1))
    assert sessions.write(root, data, expected=revision) == revision
    assert record.read_bytes() == raw_record and record.stat().st_mtime_ns != 1
    assert first == {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths}
    monkeypatch.setattr(core, "LANG", "en" if core.LANG == "zh" else "zh")
    assert config.cli(["script", str(root)]) == 0
    assert capsys.readouterr().out.splitlines() == list(map(str, paths))
    assert first == {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths}
    moved = tmp_path / "moved directory"
    root.rename(moved)
    assert str(root) not in (moved / scripts.DIRECTORY / "run.sh").read_text()
    settings = core.saved_run_settings(persist=False)
    settings["bindir"] = "/opt/local GRASP/bin"
    monkeypatch.setattr(core, "saved_run_settings", lambda **kwargs: settings)
    assert config.cli(["script", str(moved)]) == 0
    assert "/opt/local GRASP/bin" in (moved / scripts.DIRECTORY / "run.sh").read_text()
    assert "/opt/local GRASP/bin" not in (moved / scripts.DIRECTORY / "submit.sbatch").read_text()
    assert sessions.read(moved)[1] == revision


@pytest.mark.parametrize("cluster_path", [None, "$HOME/cluster software/bin", "~/cluster software/bin"])
def test_sbatch_uses_cluster_bindir_and_expands_home(tmp_path, cluster_path):
    home = tmp_path / "cluster home"
    bindir = home / ("cluster software/bin" if cluster_path else "grasp/bin")
    bindir.mkdir(parents=True)
    executable = bindir / "rnucleus"
    executable.write_text("#!/bin/sh\nprintf '%s\\n' \"$PATH\" > path.txt\n")
    executable.chmod(0o755)
    root = tmp_path / "calc"
    config.install(nucleus(), root)
    data, revision = sessions.read(root)
    if cluster_path is not None:
        data["slurm"] = {"bindir": cluster_path}
    desktop_path = "/desktop/local GRASP/bin"
    sessions.write(root, data, expected=revision, bindir=desktop_path)
    generated = root / scripts.DIRECTORY
    submit = generated / "submit.sbatch"
    assert desktop_path in (generated / "run.sh").read_text()
    assert desktop_path not in submit.read_text()
    env = core.child_env(HOME=str(home), SLURM_SUBMIT_DIR=str(root), GRASP_RUN_NICE="0")
    env.pop("GRASP_RUN_BINDIR", None)
    result = subprocess.run(["bash", str(submit)], env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert (root / "path.txt").read_text().split(os.pathsep)[0] == str(bindir)


def test_record_update_restore_and_empty_cleanup(tmp_path):
    config.install(nucleus(), tmp_path)
    data, initial = sessions.read(tmp_path)
    data["slurm"] = {"bindir": "$HOME/cluster/grasp"}
    initial = sessions.write(tmp_path, data, expected=initial)
    plan = work.prepare_update(tmp_path, nucleus("4"))
    work.apply_update(plan)
    updated, revised = sessions.read(tmp_path)
    assert revised != initial
    assert updated["slurm"]["bindir"] == "$HOME/cluster/grasp"
    generated = tmp_path / scripts.DIRECTORY
    run = generated / "run.sh"
    assert f"# Record revision: {revised}" in run.read_text()
    work.restore(tmp_path)
    assert f"# Record revision: {initial}" in run.read_text()

    owned = generated / "submit.sbatch"
    extra = generated / "notes.txt"
    user_run = "#!/bin/sh\necho manual\n"
    run.write_text(user_run)
    extra.write_text("keep")
    warnings = []
    sessions.write(tmp_path, config.session(config.check(nucleus("5"))[0]), warnings=warnings)
    assert len(warnings) == 1 and run.read_text() == user_run
    assert "Record revision:" in owned.read_text() and extra.exists()
    sessions.write(tmp_path, {"pipeline": []})
    assert run.read_text() == user_run and extra.read_text() == "keep" and not owned.exists()
    separate = tmp_path / "empty"
    separate.mkdir()
    sessions.write(separate, {"pipeline": []})
    assert not (separate / scripts.DIRECTORY).exists()
    sessions.write(separate, config.session(config.check(nucleus())[0]))
    sessions.write(separate, {"pipeline": []})
    assert not (separate / scripts.DIRECTORY).exists()


def test_script_failure_does_not_roll_back_record_or_follow_links(tmp_path):
    root = tmp_path / "calc"
    root.mkdir()
    target = tmp_path / "outside"
    target.mkdir()
    output = target / "run.sh"
    output.write_text("outside")
    (root / scripts.DIRECTORY).symlink_to(target, target_is_directory=True)
    data = config.session(config.check(nucleus())[0])
    warnings = []
    revision = sessions.write(root, data, warnings=warnings)
    assert sessions.read(root)[1] == revision
    assert len(warnings) == 1 and scripts.DIRECTORY in warnings[0]
    assert output.read_text() == "outside"
    assert not (target / "submit.sbatch").exists()


def test_record_revision_updates_only_when_effective_settings_change(tmp_path):
    config.install(nucleus(), tmp_path)
    data, initial = sessions.read(tmp_path)
    settings = core.saved_run_settings(persist=False)
    record = tmp_path / core.SESSION_FILE
    script = tmp_path / scripts.DIRECTORY / "run.sh"
    times = record.stat().st_mtime_ns, script.stat().st_mtime_ns
    same = sessions.save_run_settings(tmp_path, settings)
    assert same["revision"] == initial
    assert times == (record.stat().st_mtime_ns, script.stat().st_mtime_ns)
    changed = dict(settings, np=2 if settings["np"] != 2 else 3, bindir="/desktop/other")
    new = sessions.save_run_settings(tmp_path, changed, defaults=settings)
    assert new["revision"] != initial
    data, revision = sessions.read(tmp_path)
    assert revision == new["revision"] and data["np"] == changed["np"]
    assert "bindir" not in data and "/desktop/other" not in script.read_text()
    assert sessions.save_run_settings(tmp_path, changed, defaults=settings)["revision"] == revision


def test_run_reports_bad_settings_range_and_failing_step(tmp_path):
    data = {"pipeline": [core.Custom().job(dict(core.Custom.defaults(), prog=prog, args=args))
                         for prog, args in (("touch", "first"), ("false", ""), ("touch", "third"))]}
    sessions.write(tmp_path, data)
    run = tmp_path / scripts.DIRECTORY / "run.sh"

    def execute(*args, **settings):
        return subprocess.run(["sh", str(run), *args], cwd=tmp_path.parent, text=True, capture_output=True,
                              env=core.child_env(GRASP_RUN_NP="1", GRASP_RUN_NICE="0") | settings, timeout=15)

    for args in (("0", "1"), ("2", "1"), ("1", "4"), ("1",), ("xyz", "2"), ("1234567899999", "2")):
        assert execute(*args).returncode == 2
    assert "GRASP_RUN_NP: bad" in execute(GRASP_RUN_NP="bad").stderr
    assert "GRASP_RUN_NICE: 9" in execute(GRASP_RUN_NICE="9").stderr
    assert not (tmp_path / "first").exists()
    failure = execute()
    assert failure.returncode != 0 and "Step 2 failed" in failure.stderr
    assert (tmp_path / "first").exists() and not (tmp_path / "third").exists()
    assert execute("3", "3").returncode == 0 and (tmp_path / "third").exists()


@pytest.mark.grasp
@pytest.mark.parametrize("mode", ["whole", "split", "mpi", "batch-wrapper"])
def test_example1_generated_scripts_reproduce_reference(tmp_path, grasp_bindir, mode):
    cfg, _ = config.load(Path(DEMOS) / "example1.json")
    if mode == "mpi" and (not shutil.which("mpirun") or any(
            not (Path(grasp_bindir) / f"{job['prog']}_mpi").is_file()
            for job in config.session(cfg)["pipeline"] if job.get("mpi"))):
        pytest.skip("MPI GRASP programs and mpirun required")
    config.install(cfg, tmp_path)
    run = tmp_path / scripts.DIRECTORY / "run.sh"
    submit = tmp_path / scripts.DIRECTORY / "submit.sbatch"
    env = core.child_env(GRASP_RUN_BINDIR=grasp_bindir, GRASP_RUN_NP="2" if mode == "mpi" else "1",
                         GRASP_RUN_NICE="0", SLURM_SUBMIT_DIR=str(tmp_path), SLURM_NTASKS="1")
    stop = next(i for i, job in enumerate(cfg["jobs"]) if job["step"] == "rmcdhf")
    if mode == "split":
        args = [["sh", str(run), "1", str(stop)], ["sh", str(run), str(stop + 1), str(len(cfg["jobs"]))]]
    elif mode == "batch-wrapper":
        settings = dict(core.SLURM_DEFAULTS, bindir=grasp_bindir, nodes="1", ntasks="1", mpirun="")
        sessions.save_run_settings(tmp_path, settings, slurm=True)
        env.pop("GRASP_RUN_BINDIR")
        args = [["bash", str(submit)]]
    else:
        args = [["sh", str(run)]]
    for argv in args:
        result = subprocess.run(argv, cwd=tmp_path.parent, capture_output=True, text=True, env=env, timeout=300)
        assert result.returncode == 0, result.stderr[-3000:]
    for ref in cfg["reference"]:
        actual = config.reference_energies(tmp_path / ref["file"])
        assert actual == pytest.approx(ref["energies"], abs=1e-7)
