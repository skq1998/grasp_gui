"""调度状态、持久化恢复、提交边界和服务器分类；不需要真实 Slurm。"""

import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

import grasp_bg as bg
import grasp_config as config
import grasp_core as core
import grasp_remote as remote
import grasp_service as service
import grasp_slurm as slurm
import grasp_summary as summary
import grasp_workdir as work


@pytest.fixture(autouse=True)
def isolated_cache():
    slurm._cache.clear()
    yield
    slurm._cache.clear()


@pytest.fixture
def submitted(tmp_path, monkeypatch):
    monkeypatch.setattr(slurm.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(slurm, "command", lambda *args: "42;research")
    slurm.submit(tmp_path, "#!/bin/sh\nexit 0\n")
    return tmp_path, slurm.read(tmp_path)


def queue(record, state="RUNNING", job="42", token=None):
    return f"{job}|{state}|grasp-gui:{token or record['token']}"


def account(root, record, state="COMPLETED", exit_code="0:0", job="42"):
    stamp = datetime.datetime.fromisoformat(record["submitted"]).replace(tzinfo=None).isoformat(timespec="seconds")
    return f"{job}|{state}|{exit_code}|{root}|{stamp}"


def test_submit_records_id_cluster_script_and_owned_log(submitted):
    root, record = submitted
    assert record["job_id"] == "42" and record["cluster"] == "research"
    assert (root / (".grasp_gui_slurm_" + record["token"] + ".sh")).read_text().endswith("exit 0\n")
    assert slurm.log_file(root, record).parent == root


@pytest.mark.parametrize("state, result", [("PENDING", "queued"), ("CONFIGURING", "queued"),
    ("RUNNING", "running"), ("COMPLETING", "running"), ("SUSPENDED", "running"), ("NEW_STATE", "unknown")])
def test_active_queue_state_and_rate_limit(submitted, monkeypatch, state, result):
    root, record = submitted
    calls = []
    def run(args, workdir):
        calls.append(args)
        return queue(record, state)
    monkeypatch.setattr(slurm, "command", run)
    for _ in range(20):
        status = slurm.status(root)
        assert status["status"] == result and status["active"] and status["can_cancel"]
    assert len(calls) == 1 and "--clusters=research" in calls[0]
    assert not any(args[0] == "sacct" for args in calls)


@pytest.mark.parametrize("state, code, expected", [("COMPLETED", "0:0", "ok"),
    ("COMPLETED", "1:0", "failed"), ("CANCELLED by 123", "0:15", "stopped"),
    ("FAILED", "1:0", "failed"), ("TIMEOUT", "0:15", "failed"), ("OUT_OF_MEMORY", "0:9", "failed"),
    ("NODE_FAIL", "0:0", "failed"), ("PREEMPTED", "0:15", "failed")])
def test_terminal_state_requires_allocation_record(submitted, monkeypatch, state, code, expected):
    root, record = submitted
    monkeypatch.setattr(slurm, "command", lambda args, wd: "" if args[0] == "squeue" else
                        account(root, record, "COMPLETED", job="42.batch") + "\n" + account(root, record, state, code))
    status = slurm.status(root)
    assert status["status"] == expected and not status["active"] and status["exit_code"] == code


@pytest.mark.parametrize("answer", ["", "step_only", "wrong_directory", "old_job"])
def test_missing_or_unrelated_accounting_is_unknown(submitted, monkeypatch, answer):
    root, record = submitted
    raw = {"": "", "step_only": account(root, record, job="42.batch"),
           "wrong_directory": account(root / "other", record),
           "old_job": account(root, record).rsplit("|", 1)[0] + "|2000-01-01T00:00:00"}[answer]
    monkeypatch.setattr(slurm, "command", lambda args, wd: "" if args[0] == "squeue" else raw)
    status = slurm.status(root)
    assert status["status"] == "unknown" and status["active"] and not status["can_cancel"]
    with pytest.raises(ValueError, match="Slurm"):
        work.idle(root)


def test_accounting_error_does_not_mark_complete_and_is_cached(submitted, monkeypatch):
    root, record = submitted
    calls = []
    def failed(args, wd):
        calls.append(args[0])
        if args[0] == "squeue":
            return ""
        raise ValueError("accounting unavailable")
    monkeypatch.setattr(slurm, "command", failed)
    for _ in range(10):
        assert slurm.status(root)["error"] == "accounting unavailable"
    assert calls == ["squeue", "sacct"]


def test_cancel_verifies_owner_marker_before_scancel(submitted, monkeypatch):
    root, record = submitted
    calls = []
    def run(args, wd):
        calls.append(args)
        return queue(record) if args[0] == "squeue" else ""
    monkeypatch.setattr(slurm, "command", run)
    assert service.Service("/").stop(str(root))["confirmed"] is False
    assert calls[-1] == ["scancel", "--clusters=research", "42"]
    calls.clear()
    monkeypatch.setattr(slurm, "command", lambda args, wd: calls.append(args) or queue(record, token="different"))
    with pytest.raises(ValueError, match="不匹配"):
        slurm.cancel(root)
    assert [args[0] for args in calls] == ["squeue"]


def test_reconnect_reads_saved_job_and_its_log_on_new_server_process(submitted, monkeypatch):
    root, record = submitted
    monkeypatch.setattr(slurm, "command", lambda args, wd: queue(record, "PENDING"))
    first = remote.Operations().run_status(str(root))["state"]
    assert first["job_id"] == "42" and first["status"] == "queued"
    slurm.log_file(root, record).write_text("previous output\nnew output\n")
    slurm._cache.clear()
    monkeypatch.setattr(slurm, "command", lambda args, wd: queue(record))
    second = remote.Server()
    value = second.watch(str(root))
    assert value["state"]["job_id"] == "42" and value["state"]["status"] == "running"
    assert "new output" in value["text"]
    assert "new output" in remote.Operations().tail_log(str(root))["text"]


def test_submission_timeout_never_allows_automatic_resubmit(tmp_path, monkeypatch):
    monkeypatch.setattr(slurm.shutil, "which", lambda name: name)
    def fail(*args):
        raise ValueError("timeout")
    monkeypatch.setattr(slurm, "command", fail)
    with pytest.raises(ValueError, match="timeout"):
        slurm.submit(tmp_path, "exit 0")
    assert slurm.read(tmp_path)["job_id"] == ""
    assert slurm.status(tmp_path)["active"]
    with pytest.raises(ValueError, match="Slurm"):
        work.idle(tmp_path)


def test_explicit_rejection_can_be_corrected_and_retried(tmp_path, monkeypatch):
    monkeypatch.setattr(slurm.shutil, "which", lambda name: name)
    def fail(*args):
        raise slurm.CommandFailed("invalid partition")
    monkeypatch.setattr(slurm, "command", fail)
    with pytest.raises(ValueError, match="invalid partition"):
        slurm.submit(tmp_path, "exit 0")
    assert slurm.status(tmp_path)["status"] == "failed" and not slurm.status(tmp_path)["active"]
    work.idle(tmp_path)


def test_missing_slurm_tools_never_create_submission_record(tmp_path, monkeypatch):
    monkeypatch.setattr(slurm.shutil, "which", lambda name: None)
    with pytest.raises(ValueError, match="sbatch"):
        slurm.submit(tmp_path, "exit 0")
    assert not (tmp_path / slurm.RECORD).exists()


@pytest.mark.parametrize("job", ["--all", "42;rm -rf", 42, 0, None])
def test_invalid_records_do_not_reach_scheduler(submitted, monkeypatch, job):
    root, record = submitted
    record["job_id"] = job
    (root / slurm.RECORD).write_text(json.dumps(record))
    monkeypatch.setattr(slurm, "command", lambda *args: pytest.fail("invalid record reached scheduler"))
    with pytest.raises(ValueError):
        slurm.read(root)
    state = slurm.status(root)
    assert state["record_invalid"] and state["active"] and state["can_clear"]


def test_new_background_run_takes_precedence_over_old_job_record(submitted, monkeypatch):
    root, record = submitted
    monkeypatch.setattr(slurm, "command", lambda *args: pytest.fail("old scheduler job queried"))
    assert slurm.status(root, dict(host="node", pid=7, start="later")) is None


def test_remote_slurm_submit_never_launches_background_runner(tmp_path, monkeypatch):
    cfg = dict(format=config.FORMAT, version=config.VERSION,
               jobs=[dict(step="rnucleus", vals=config.STEPS["rnucleus"].defaults())])
    root = tmp_path / "calc"
    config.install(cfg, str(root))
    op = remote.Operations()
    plan = op.run_plan(str(root), server_type="slurm")
    assert plan["server_type"] == "slurm" and not plan["logout_kills"]
    settings = dict(plan["slurm"], nodes="1", ntasks="1", mpirun="")
    monkeypatch.setattr(slurm.shutil, "which", lambda name: name)
    monkeypatch.setattr(bg, "launch", lambda *a, **k: pytest.fail("Slurm must not use bg.launch"))
    calls = []
    def run(args, wd):
        calls.append(args[0])
        return "42" if args[0] == "sbatch" else queue(slurm.read(root), "PENDING")
    monkeypatch.setattr(slurm, "command", run)
    for changes in (dict(ntasks="0"), dict(nodes="100001"), dict(extra="--array=1-3")):
        with pytest.raises(ValueError):
            op.run(str(root), plan["revision"], server_type="slurm", slurm=dict(settings, **changes))
    assert not (root / slurm.RECORD).exists()
    result = op.run(str(root), plan["revision"], server_type="slurm", slurm=settings)
    assert result["backend"] == "slurm" and result["job_id"] == "42"
    with pytest.raises(ValueError, match="Slurm"):
        op.run(str(root), plan["revision"], server_type="slurm", slurm=settings)
    assert calls.count("sbatch") == 1


def test_scheduler_success_is_not_per_step_physics_evidence(submitted, monkeypatch):
    root, record = submitted
    monkeypatch.setattr(slurm, "command", lambda args, wd: "" if args[0] == "squeue" else account(root, record))
    rows = summary.run_status([dict(title="test")], root)
    assert rows[0]["status"] == "unknown" and "Slurm" in rows[0]["basis"]


def test_cli_failure_and_timeout_are_distinct(tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr="rejected"))
    with pytest.raises(slurm.CommandFailed, match="rejected"):
        slurm.command(["sbatch"], tmp_path)
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("sbatch", 10)
    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(ValueError) as error:
        slurm.command(["sbatch"], tmp_path)
    assert not isinstance(error.value, slurm.CommandFailed)


@pytest.fixture
def scheduler_cli(tmp_path, monkeypatch):
    directory = tmp_path / "bin"
    directory.mkdir()
    database = tmp_path / "scheduler.json"
    database.write_text("{}")
    program = directory / "scheduler"
    program.write_text("#!" + sys.executable + "\n" + '''
import datetime, json, os, sys
from pathlib import Path
path = Path(os.environ["GRASP_TEST_SCHEDULER"])
data = json.loads(path.read_text())
name = Path(sys.argv[0]).name
if name == "sbatch":
    record = json.loads(Path(".grasp_gui_slurm.json").read_text())
    data.update(queued=True, directory=os.getcwd(), submitted=record["submitted"].split(".")[0],
                token=record["token"], script=sys.argv[-1], arguments=sys.argv[1:])
    print("42;research")
elif name == "squeue" and data.get("queued"):
    print("42|PENDING|grasp-gui:" + data["token"])
elif name == "scancel":
    data.update(queued=False, state="CANCELLED", exit_code="0:15")
elif name == "sacct" and not data.get("queued"):
    print("42|" + data["state"] + "|" + data["exit_code"] + "|" + data["directory"] + "|" + data["submitted"])
path.write_text(json.dumps(data))
''')
    program.chmod(0o755)
    for name in ("sbatch", "squeue", "sacct", "scancel"):
        (directory / name).symlink_to(program)
    monkeypatch.setenv("PATH", str(directory) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setenv("GRASP_TEST_SCHEDULER", str(database))
    return database


def test_real_cli_transport_reconnect_and_cancel(tmp_path, scheduler_cli):
    root = tmp_path / "calculation with spaces"
    root.mkdir()
    slurm.submit(root, "#!/bin/sh\nexit 0\n")
    assert slurm.status(root)["status"] == "queued"
    data = json.loads(scheduler_cli.read_text())
    assert "--parsable" in data["arguments"] and "--chdir=" + str(root) in data["arguments"]
    slurm._cache.clear()
    assert remote.Operations().run_status(str(root))["state"]["job_id"] == "42"
    slurm.cancel(root)
    assert slurm.status(root)["status"] == "stopped"


def test_batch_paths_expand_variables_without_executing_commands(tmp_path):
    sentinel = tmp_path / "unwanted"
    value = '$HOME/a space/$(touch ' + str(sentinel) + ')'
    expression = slurm.path_expression(value)
    result = subprocess.run(["bash", "-c", "printf '%s' " + expression], capture_output=True, text=True, check=True)
    assert result.stdout == str(Path.home()) + "/a space/$(touch " + str(sentinel) + ")"
    assert not sentinel.exists()


def test_single_rank_mpi_uses_ranked_angular_file_dependencies(tmp_path):
    # GRASP mpi90/cpath.f90 按 MPI rank 建目录；MPI 单进程也使用 rank 000。
    assert core.angular_files(dict(np=1)) == ["mcp.30"]
    assert core.angular_files(dict(np=1, mpi=True, mpitmp=str(tmp_path))) == [str(tmp_path / "000/mcp000.30")]


@pytest.mark.grasp
def test_submitted_batch_reproduces_real_grasp_reference(tmp_path, scheduler_cli, grasp_bindir):
    root = tmp_path / "real calculation with spaces"
    cfg, _ = config.load(Path(__file__).resolve().parents[1] / "demos/example1.json")
    config.install(cfg, str(root))
    operation = remote.Operations()
    plan = operation.run_plan(str(root), server_type="slurm")
    settings = dict(plan["slurm"], nodes="1", ntasks="1", mpirun="", bindir=grasp_bindir)
    operation.run(str(root), plan["revision"], server_type="slurm", slurm=settings)
    data = json.loads(scheduler_cli.read_text())
    env = core.child_env(SLURM_SUBMIT_DIR=str(root), SLURM_JOB_ID="42", SLURM_JOB_NODELIST="test", SLURM_NTASKS="1")
    result = subprocess.run(["bash", data["script"]], cwd=root, capture_output=True, text=True, env=env, timeout=90)
    assert result.returncode == 0, result.stderr[-2000:]
    for ref in cfg["reference"]:
        assert config.reference_energies(str(root / ref["file"])) == pytest.approx(ref["energies"], abs=1e-7)
    assert (root / "2s_3.uni.lsj.lbl").stat().st_size > 0
    data.update(queued=False, state="COMPLETED", exit_code="0:0")
    scheduler_cli.write_text(json.dumps(data))
    slurm._cache.clear()
    assert remote.Operations().run_status(str(root))["state"]["status"] == "ok"
