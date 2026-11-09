"""复制目录、丢失提交回答、无记账和显式解除未知状态。"""

import json
import shutil
import subprocess

import pytest

import grasp_core as core
import grasp_service as service
import grasp_remote as remote
import grasp_slurm as slurm
import grasp_summary as summary
import grasp_workdir as work
from test_slurm import submitted, isolated_cache, queue, account


def test_copied_record_does_not_belong_to_new_directory(submitted, monkeypatch):
    root, record = submitted
    target = root / "copy"
    target.mkdir()
    shutil.copyfile(root / slurm.RECORD, target / slurm.RECORD)
    monkeypatch.setattr(slurm, "command", lambda *args: pytest.fail("copied job queried"))
    assert slurm.read(target) is None and slurm.status(target) is None
    assert service.Service("/").status(str(target))["state"] is None
    work.idle(target)
    assert summary.run_status([dict(title="step")], target)[0]["status"] == "unknown"
    assert slurm.read(root)["token"] == record["token"]


def test_lost_submission_reply_recovers_id_by_exact_token(submitted, monkeypatch):
    root, record = submitted
    record["job_id"] = ""
    (root / slurm.RECORD).write_text(json.dumps(record))
    monkeypatch.setattr(slurm, "command", lambda *args: "9|RUNNING|someone-else\n" + queue(record, job="73"))
    state = slurm.status(root)
    assert state["job_id"] == "73" and state["status"] == "running" and state["can_cancel"]
    assert slurm.read(root)["job_id"] == "73"
    assert slurm.status(root)["record_revision"] == state["record_revision"]


@pytest.mark.parametrize("code", [0, 7])
def test_exit_marker_preserves_status_before_any_command(submitted, monkeypatch, code):
    root, record = submitted
    script = slurm.with_finish_marker("#!/bin/bash\n#SBATCH --job-name=test\nset -e\nexit " + str(code) + "\n",
                                     root, record)
    assert script.index("#SBATCH") < script.index("trap ") < script.index("set -e")
    path = root / "batch.sh"
    path.write_text(script)
    result = subprocess.run(["bash", str(path)], cwd=root, env=core.child_env(SLURM_JOB_ID="42"))
    assert result.returncode == code
    def command(args, directory):
        if args[0] == "squeue":
            return ""
        raise slurm.CommandFailed("no slurmdbd")
    monkeypatch.setattr(slurm, "command", command)
    state = slurm.status(root)
    assert not state["active"] and state["status"] == ("ok" if code == 0 else "failed")
    assert state["evidence"] == "batch_exit_marker"
    work.idle(root)


def test_active_queue_takes_precedence_over_completion_marker(submitted, monkeypatch):
    root, record = submitted
    slurm.finish_file(root, record).write_text(record["token"] + "|42|0\n")
    monkeypatch.setattr(slurm, "command", lambda *args: queue(record))
    assert slurm.status(root)["active"]


def test_accounting_failure_takes_precedence_over_shell_success_marker(submitted, monkeypatch):
    root, record = submitted
    slurm.finish_file(root, record).write_text(record["token"] + "|42|0\n")
    monkeypatch.setattr(slurm, "command", lambda args, wd: "" if args[0] == "squeue" else
                        account(root, record, "FAILED", "1:0"))
    state = slurm.status(root)
    assert state["status"] == "failed" and state["evidence"] == "accounting"


@pytest.mark.parametrize("code, expected", [(130, "stopped"), (143, "stopped"), (7, "failed"), (0, "ok")])
def test_requested_cancel_is_restored_without_accounting(submitted, monkeypatch, code, expected):
    root, record = submitted
    def active(args, wd):
        return queue(record) if args[0] == "squeue" else ""
    monkeypatch.setattr(slurm, "command", active)
    slurm.cancel(root)
    assert slurm.read(root)["cancel_requested"] is True
    slurm.finish_file(root, record).write_text(record["token"] + f"|42|{code}\n")
    def ended(args, wd):
        if args[0] == "squeue":
            return ""
        raise slurm.CommandFailed("no accounting")
    monkeypatch.setattr(slurm, "command", ended)
    assert slurm.status(root)["status"] == expected


@pytest.mark.parametrize("code", [130, 143])
def test_signal_exit_without_successful_cancel_stays_failed(submitted, monkeypatch, code):
    root, record = submitted
    slurm.finish_file(root, record).write_text(record["token"] + f"|42|{code}\n")
    monkeypatch.setattr(slurm, "command", lambda *args: "")
    assert slurm.status(root)["status"] == "failed"


def test_rejected_cancel_does_not_record_successful_request(submitted, monkeypatch):
    root, record = submitted
    def rejected(args, wd):
        if args[0] == "squeue":
            return queue(record)
        raise slurm.CommandFailed("scancel refused")
    monkeypatch.setattr(slurm, "command", rejected)
    with pytest.raises(slurm.CommandFailed):
        slurm.cancel(root)
    assert not slurm.read(root).get("cancel_requested")


@pytest.mark.parametrize("raw", ["wrong|42|0\n", "TOKEN|17|0\n", "TOKEN|42|999\n", "TOKEN|42|0"])
def test_invalid_finish_marker_cannot_unlock_directory(submitted, monkeypatch, raw):
    root, record = submitted
    slurm.finish_file(root, record).write_text(raw.replace("TOKEN", record["token"]))
    monkeypatch.setattr(slurm, "command", lambda *args: "")
    assert slurm.status(root)["active"]
    with pytest.raises(ValueError):
        work.idle(root)


def test_finished_marker_recovers_lost_job_id(submitted, monkeypatch):
    root, record = submitted
    record["job_id"] = ""
    (root / slurm.RECORD).write_text(json.dumps(record))
    slurm.finish_file(root, record).write_text(record["token"] + "|73|0\n")
    monkeypatch.setattr(slurm, "command", lambda *args: "")
    assert slurm.status(root)["status"] == "ok"
    assert slurm.read(root)["job_id"] == "73"


def test_clear_requires_confirmation_fresh_queue_and_unchanged_record(submitted, monkeypatch):
    root, record = submitted
    raw = (root / slurm.RECORD).read_bytes()
    revision = slurm.sessions.revision(raw)
    with pytest.raises(ValueError):
        slurm.clear(root, revision)
    with pytest.raises(ValueError):
        slurm.clear(root, "outdated", True)
    monkeypatch.setattr(slurm, "command", lambda *args: queue(record))
    with pytest.raises(ValueError, match="队列"):
        slurm.clear(root, revision, True)
    def unavailable(*args):
        raise ValueError("squeue unavailable")
    monkeypatch.setattr(slurm, "command", unavailable)
    with pytest.raises(ValueError, match="unavailable"):
        slurm.clear(root, revision, True)
    assert (root / slurm.RECORD).read_bytes() == raw
    monkeypatch.setattr(slurm, "command", lambda *args: "99|RUNNING|another-job")
    result = service.Service("/").clear_slurm_record(str(root), revision, True)
    assert (root / result["archive"]).read_bytes() == raw and not (root / slurm.RECORD).exists()
    assert slurm.status(root) is None
    work.idle(root)


def test_corrupt_record_can_be_archived_only_after_empty_queue_check(tmp_path, monkeypatch):
    path = tmp_path / slurm.RECORD
    path.write_text("broken json")
    state = service.Service("/").status(str(tmp_path))["state"]
    assert state["record_invalid"] and state["can_clear"] and state["error"]
    assert service.Service("/").tail_log(str(tmp_path))["text"] == ""
    monkeypatch.setattr(slurm, "command", lambda *args: "99|RUNNING|another-job")
    with pytest.raises(ValueError):
        slurm.clear(tmp_path, state["record_revision"], True)
    monkeypatch.setattr(slurm, "command", lambda *args: "")
    result = slurm.clear(tmp_path, state["record_revision"], True)
    assert (tmp_path / result["archive"]).read_text() == "broken json"


def test_cli_timeouts_allow_slow_controller(tmp_path, monkeypatch):
    limits = []
    def run(args, **kwargs):
        limits.append(kwargs["timeout"])
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
    monkeypatch.setattr(subprocess, "run", run)
    slurm.command(["sbatch"], tmp_path)
    slurm.command(["squeue"], tmp_path)
    assert limits == [60, 30]


def test_clear_is_a_mutation_in_remote_dispatch(submitted, monkeypatch):
    root, record = submitted
    monkeypatch.setattr(slurm, "command", lambda *args: "")
    server = remote.Server()
    replies = []
    def sent(value):
        replies.append(value)
        server.cancel.set()
    monkeypatch.setattr(server, "send", sent)
    state = slurm.status(root)
    server.tasks.put(dict(id=7, method="clear_slurm_record", args=dict(directory=str(root),
                         revision=state["record_revision"], confirmed=True)))
    server.worker()
    assert replies[0]["result"]["cleared"] and not (root / slurm.RECORD).exists()
    assert "clear_slurm_record" not in remote.READ_ONLY
