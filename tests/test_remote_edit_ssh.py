"""可选受限 SSH/rsync 验收；只在显式指定的专用目录中新建测试计算。"""

import copy
import os
from pathlib import Path
import shutil
import subprocess
import time
import uuid

import pytest

import grasp_config as config
import grasp_core as core
import grasp_remote as remote
import grasp_remote_edit as edit
import grasp_ssh as ssh
import grasp_transfer as transfer

pytestmark = pytest.mark.skipif(os.environ.get("GRASP_REMOTE_EDIT_TEST") != "1"
                                or not os.environ.get("GRASP_SSH_TEST_HOST"),
                                reason="dedicated remote-edit SSH fixture not configured")


@pytest.fixture
def peer():
    options = ["-F", os.environ["GRASP_SSH_TEST_CONFIG"]] if os.environ.get("GRASP_SSH_TEST_CONFIG") else []
    client = ssh.Client(os.environ["GRASP_SSH_TEST_HOST"], os.environ.get("GRASP_SSH_TEST_PROGRAM", ""),
                        options=options)
    hello = client.request("hello", dict(protocol=remote.PROTOCOL)).result(60)
    assert edit.CAPABILITY in hello.get("capabilities", []), "Install this branch's server build first"
    yield client
    client.close()


def call(peer, method, directory, **args):
    return peer.request(method, dict(directory=directory, **args)).result(180)


def target():
    base = os.environ["GRASP_SSH_TEST_DIRECTORY"].rstrip("/")
    assert base.startswith("/") and base != "/"
    return base + "/edit-" + uuid.uuid4().hex[:12]


def wait_done(peer, directory):
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        state = call(peer, "run_status", directory)["state"]
        if state and state["status"] != "running":
            assert state["status"] == "ok", (state, call(peer, "tail_log", directory))
            return
        time.sleep(1)
    pytest.fail("Remote pipeline did not finish")


def energies(peer, directory, name):
    result = call(peer, "levels", directory, files=[name], components=0, brief=True)
    return [row["energy_hartree"] for row in result["levels"]]


@pytest.mark.grasp
def test_real_ssh_new_fetch_edit_upload_and_direct_reference(peer, tmp_path, grasp_bindir):
    assert shutil.which("rsync"), "Install local rsync for this opt-in acceptance test"
    example, _ = config.load(Path(__file__).resolve().parents[1] / "demos/example1.json")
    # 使用本机制备的 isodata，证明所选输入确实通过 rsync 上传后参与服务器计算。
    prepared = tmp_path / "prepared"
    prepared.mkdir()
    script = core.shell_script([config.make_job(example["jobs"][0])], grasp_bindir, ".")
    subprocess.run(["sh", "-c", script], cwd=prepared, check=True, capture_output=True, timeout=60)
    cfg = dict(example, jobs=example["jobs"][1:])
    directory = target()
    planned = call(peer, "preview_upload", directory, configuration=cfg, uploads=["isodata"])
    assert not planned["checked"]["missing"] and planned["checked"]["pending"]
    created = call(peer, "create_workdir", directory, configuration=cfg)
    rows = transfer.scan(prepared, ["isodata"])["files"]
    transfer.Transfer(peer).run(str(prepared), directory, rows, upload=True)
    call(peer, "run", directory, revision=created["revision"], accept_logout=True)
    wait_done(peer, directory)
    for ref in cfg["reference"]:
        assert energies(peer, directory, ref["file"]) == pytest.approx(ref["energies"], abs=1e-7)
    reply = call(peer, "get_pipeline", directory)
    local = tmp_path / "copy"
    edit.checkout(local, peer.host, directory, reply)
    changed = copy.deepcopy(reply["config"])
    ci = next(job for job in changed["jobs"] if job.get("step") == "rci")
    ci["vals"]["trans"] = not ci["vals"]["trans"]
    updated = call(peer, "update_workdir", directory, revision=reply["revision"], configuration=changed)
    with pytest.raises(ValueError):
        call(peer, "update_workdir", directory, revision=reply["revision"], configuration=cfg)
    call(peer, "run", directory, revision=updated["revision"], accept_logout=True)
    wait_done(peer, directory)

    direct = target()
    call(peer, "create_workdir", direct, configuration=changed)
    transfer.Transfer(peer).run(str(prepared), direct, rows, upload=True)
    settings = call(peer, "run_plan", direct)["run_settings"]
    mpi = f"{settings['mpirun']} -np {settings['np']}" if settings["np"] > 1 else ""
    script = core.shell_script(config.session(changed)["pipeline"], settings["bindir"], direct,
                               mpi, settings["mpitmp"])
    script = script.replace("\n", "\nset -e\n", 1)
    result = subprocess.run(peer.argv()[:-1] + ["bash -s"], input=script, env=peer.environment(),
                            text=True, capture_output=True, timeout=180)
    assert result.returncode == 0, result.stderr[-3000:]
    for ref in cfg["reference"]:
        assert energies(peer, directory, ref["file"]) == pytest.approx(energies(peer, direct, ref["file"]), abs=1e-7)


def test_real_ssh_running_directory_refuses_upload(peer):
    cfg = dict(format=config.FORMAT, version=config.VERSION,
               jobs=[dict(command=dict(title="fixture", prog="sleep", args=["30"], stdin=""))])
    directory = target()
    created = call(peer, "create_workdir", directory, configuration=cfg, allow_custom=True)
    call(peer, "run", directory, revision=created["revision"], allow_custom=True, accept_logout=True)
    try:
        with pytest.raises(ValueError):
            call(peer, "update_workdir", directory, revision=created["revision"], configuration=cfg, allow_custom=True)
        assert call(peer, "get_pipeline", directory)["revision"] == created["revision"]
    finally:
        call(peer, "stop", directory)


def test_real_ssh_old_server_has_no_write_capability():
    program = os.environ.get("GRASP_SSH_TEST_OLD_PROGRAM")
    if not program:
        pytest.skip("old server fixture program not configured")
    options = ["-F", os.environ["GRASP_SSH_TEST_CONFIG"]] if os.environ.get("GRASP_SSH_TEST_CONFIG") else []
    client = ssh.Client(os.environ["GRASP_SSH_TEST_HOST"], program, options=options)
    try:
        hello = client.request("hello", dict(protocol=remote.PROTOCOL)).result(60)
        assert edit.CAPABILITY not in hello.get("capabilities", [])
    finally:
        client.close()
