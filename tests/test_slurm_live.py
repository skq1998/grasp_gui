"""专用单节点 Slurm 验收；无显式环境变量时跳过，不能指向生产调度器。"""

import os
from pathlib import Path
import subprocess
import tempfile
import time

import pytest

import grasp_config as config
import grasp_core as core
import grasp_remote as remote
import grasp_slurm as slurm

pytestmark = pytest.mark.skipif(os.environ.get("GRASP_SLURM_TEST") != "1",
                               reason="dedicated Slurm fixture not configured")


def wait_for(root, predicate, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = slurm.status(root, refresh=True)
        if predicate(state):
            return state
        time.sleep(1)
    pytest.fail("Slurm state did not settle: " + repr(state))


@pytest.fixture(autouse=True)
def isolated_scheduler():
    # fixture 容器没有其他用户；只允许对特定的隔离集群运行这些取消操作。
    name = slurm.command(["scontrol", "show", "config"], "/tmp")
    assert any(line.split("=", 1)[-1].strip() == "fixture" for line in name.splitlines()
               if line.strip().startswith("ClusterName"))
    slurm._cache.clear()
    yield
    subprocess.run(["scancel", "--user=" + str(os.getuid())], check=True)
    slurm._cache.clear()


def install(root, grasp_bindir, jobs=None, modules="", extra=""):
    cfg, _ = config.load(Path(__file__).resolve().parents[1] / "demos/example1.json")
    if jobs is not None:
        cfg = dict(cfg, jobs=jobs)
        cfg.pop("reference", None)
    config.install(cfg, str(root))
    operation = remote.Operations()
    plan = operation.run_plan(str(root), server_type="slurm")
    settings = dict(plan["slurm"], nodes="1", ntasks="1", mpirun="", bindir=grasp_bindir,
                    time="00:05:00", modules=modules, extra=extra)
    return cfg, operation, plan, settings


@pytest.mark.grasp
def test_real_slurm_example1_pending_running_complete(tmp_path, grasp_bindir):
    root = tmp_path / "example1"
    cfg, operation, plan, settings = install(root, grasp_bindir, modules="sleep 3", extra="hold")
    try:
        result = operation.run(str(root), plan["revision"], server_type="slurm", slurm=settings)
        assert slurm.status(root, refresh=True)["status"] == "queued"
        slurm.command(["scontrol", "release", result["job_id"]], root)
        wait_for(root, lambda s: s.get("scheduler_state") == "RUNNING")
        state = wait_for(root, lambda s: not s["active"])
        assert state["status"] == "ok", state
        for ref in cfg["reference"]:
            energies = config.reference_energies(str(root / ref["file"]))
            assert energies == pytest.approx(ref["energies"], abs=1e-7)
        assert (root / "2s_3.uni.lsj.lbl").stat().st_size > 0
        record = slurm.read(root)
        if os.environ["GRASP_SLURM_TEST_ACCOUNTING"] == "1":
            # 移除标记，证明这一轮终态确实来自 sacct，而不是 trap 的回退。
            slurm.finish_file(root, record).unlink()
            state = wait_for(root, lambda s: s.get("evidence") == "accounting")
        else:
            answer = subprocess.run(["sacct", "--noheader", "--jobs=" + result["job_id"]], capture_output=True)
            assert answer.returncode != 0
            assert state["evidence"] == "batch_exit_marker"
        assert state["status"] == "ok"
        print("example1 terminal evidence:", state)
    finally:
        record = slurm.read(root)
        if record and record["job_id"]:
            subprocess.run(["scancel", record["job_id"]], capture_output=True)


def test_real_slurm_running_cancel(tmp_path):
    root = tmp_path / "cancel"
    root.mkdir()
    slurm.submit(root, "#!/bin/bash\n#SBATCH --ntasks=1\n#SBATCH --time=00:05:00\nset -e\nsleep 120\n")
    wait_for(root, lambda s: s.get("scheduler_state") == "RUNNING")
    assert slurm.cancel(root)["stop_requested"]
    deadline = time.monotonic() + 60
    record = slurm.read(root)
    while slurm.queued(root, record) is not None:
        assert time.monotonic() < deadline
        time.sleep(1)
    if os.environ["GRASP_SLURM_TEST_ACCOUNTING"] == "1":
        state = wait_for(root, lambda s: s.get("evidence") == "accounting")
        assert state["status"] == "stopped"
    else:
        state = slurm.status(root, refresh=True)
        if state["status"] == "unknown":
            # 被 SIGKILL 的批处理可能无法写 trap 标记；必须能显式归档解除。
            assert slurm.clear(root, state["record_revision"], confirmed=True)["cleared"]
        else:
            assert state["status"] == "stopped" and state["evidence"] == "batch_exit_marker"
    print("cancel state:", state)


@pytest.mark.grasp
def test_real_slurm_single_rank_mpi_angular_files(grasp_bindir):
    cfg, _ = config.load(Path(__file__).resolve().parents[1] / "demos/example1.json")
    # GRASP MPI 路径限制为 128 字符，不能沿用较长的 pytest 用例目录名。
    with tempfile.TemporaryDirectory(prefix="gmpi-", dir="/tmp") as temporary:
        root = Path(temporary) / "calc"
        _, operation, plan, settings = install(root, grasp_bindir, jobs=cfg["jobs"][:3])
        settings.update(mpirun="mpirun -np $SLURM_NTASKS", mpitmp=str(root / "mpi"))
        try:
            operation.run(str(root), plan["revision"], server_type="slurm", slurm=settings)
            state = wait_for(root, lambda s: not s["active"])
            assert state["status"] == "ok", state
            assert (root / "mpi/000/mcp000.30").stat().st_size > 0
            print("MPI single-rank angular file:", root / "mpi/000/mcp000.30")
        finally:
            record = slurm.read(root)
            if record:
                subprocess.run(["scancel", record["job_id"]], capture_output=True)
