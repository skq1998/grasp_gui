"""真实 rsync 验收；专用账户和专用目录由 REMOTE.md 中的环境变量指定。"""

import hashlib
import os
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
import sys

import pytest

import grasp_ssh as ssh
import grasp_transfer as transfer

pytestmark = pytest.mark.skipif(not os.environ.get("GRASP_RSYNC_TEST_HOST") or not shutil.which("rsync")
                                or not sys.platform.startswith("linux"),
                                reason="dedicated rsync fixture not configured")


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for data in iter(lambda: stream.read(1 << 20), b""):
            result.update(data)
    return result.hexdigest()


def remote(peer, *args):
    result = subprocess.run(peer.argv()[:-1] + [shlex.join(args)], env=peer.environment(),
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.fixture(scope="module")
def peer():
    options = ["-F", os.environ["GRASP_RSYNC_TEST_CONFIG"]] if os.environ.get("GRASP_RSYNC_TEST_CONFIG") else []
    client = ssh.Client(os.environ["GRASP_RSYNC_TEST_HOST"], options=options)
    base = os.environ["GRASP_RSYNC_TEST_DIRECTORY"].rstrip("/")
    assert base.startswith("/") and base
    client.request("list_dir", dict(directory=base)).result(30)
    yield client
    client.close()


@pytest.fixture
def target(tmp_path, peer):
    directory = os.environ["GRASP_RSYNC_TEST_DIRECTORY"].rstrip("/") + "/" + tmp_path.name
    data = tmp_path / "transfer-data"
    data.mkdir()
    remote(peer, "mkdir", "-p", "--", directory)
    try:
        yield peer, directory, data
    finally:
        try:
            remote(peer, "rm", "-rf", "--", directory)
        finally:
            shutil.rmtree(data)


@pytest.mark.parametrize("upload", [False, True], ids=["download", "upload"])
@pytest.mark.parametrize("interruption", ["disconnect", "cancel"])
def test_large_transfer_resume_and_integrity(target, monkeypatch, upload, interruption):
    client, directory, data = target
    source = data / "source"
    source.mkdir()
    (source / "small file.txt").write_bytes(b"small selected file\n")
    large = source / "large.w"
    block = os.urandom(1 << 20)
    with large.open("wb") as stream:
        for _ in range(200):
            stream.write(block)
    rows = transfer.scan(source)["files"]
    small = [row for row in rows if row["size"] <= transfer.DEFAULT_THRESHOLD]
    transfer.Transfer(client).run(str(source), directory, small, upload=True)
    assert len(client.request("transfer_files", dict(directory=directory)).result(30)["files"]) == 1
    if not upload:
        transfer.Transfer(client).run(str(source), directory, rows, upload=True, policy="resume")
        rows = client.request("transfer_files", dict(directory=directory)).result(30)["files"]
    destination = source if upload else data / "download"
    expected, original = digest(large), subprocess.Popen
    def throttled(args, *rest, **kwargs):
        if Path(args[0]).name == "rsync" and "--files-from=-" in args:
            args = [args[0], "--bwlimit=10240", *args[1:]]
        return original(args, *rest, **kwargs)
    monkeypatch.setattr(subprocess, "Popen", throttled)
    job, stopped = transfer.Transfer(client), []
    def interrupt(state):
        if stopped or state["bytes"] < 1 << 20:
            return
        stopped.append(state)
        if interruption == "cancel":
            job.cancel()
        else:
            path = Path(f"/proc/{job.process.pid}/task/{job.process.pid}/children")
            for child in path.read_text().split():
                command = Path(f"/proc/{child}/cmdline").read_bytes().split(b"\0")[0]
                if Path(os.fsdecode(command)).name == "ssh":
                    os.kill(int(child), signal.SIGKILL)
                    return
            pytest.fail("rsync SSH child not found")
    with pytest.raises(InterruptedError if interruption == "cancel" else RuntimeError):
        job.run(str(destination), directory, rows, upload=upload, policy="resume", notify=interrupt)
    assert stopped
    assert client.request("list_dir", dict(directory=directory)).result(30)
    monkeypatch.setattr(subprocess, "Popen", original)
    transfer.Transfer(client).run(str(destination), directory, rows, upload=upload, policy="resume")
    actual = remote(client, "sha256sum", "--", directory + "/large.w").split()[0] if upload else digest(
        destination / "large.w")
    assert actual == expected


def test_existing_file_policies(target):
    client, directory, data = target
    path = data / "file"
    path.write_bytes(b"original")
    transfer.Transfer(client).run(str(data), directory, transfer.scan(data)["files"], upload=True)
    before = remote(client, "sha256sum", "--", directory + "/file").split()[0]
    info = path.stat()
    path.write_bytes(b"modified")
    os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
    rows = transfer.scan(data)["files"]
    transfer.Transfer(client).run(str(data), directory, rows, upload=True, policy="skip")
    assert remote(client, "sha256sum", "--", directory + "/file").split()[0] == before
    transfer.Transfer(client).run(str(data), directory, rows, upload=True, policy="resume")
    assert remote(client, "sha256sum", "--", directory + "/file").split()[0] == digest(path)
    path.write_bytes(b"overwrite")
    transfer.Transfer(client).run(str(data), directory, transfer.scan(data)["files"], upload=True, policy="overwrite")
    assert remote(client, "sha256sum", "--", directory + "/file").split()[0] == digest(path)


def test_missing_rsync_keeps_analysis_alive(target):
    client, directory, data = target
    (data / "file").write_text("data")
    with pytest.raises(Exception, match="rsync"):
        transfer.Transfer(client).run(str(data), directory, transfer.scan(data)["files"],
                                      upload=True, rsync="/no-such-rsync")
    assert client.request("list_dir", dict(directory=directory)).result(30)


def test_maxsessions_one_keeps_analysis_alive(tmp_path):
    host = os.environ.get("GRASP_RSYNC_TEST_SINGLE_HOST")
    if not host:
        pytest.skip("MaxSessions 1 fixture not configured")
    options = ["-F", os.environ["GRASP_RSYNC_TEST_CONFIG"]] if os.environ.get("GRASP_RSYNC_TEST_CONFIG") else []
    client = ssh.Client(host, options=options)
    directory = os.environ["GRASP_RSYNC_TEST_DIRECTORY"]
    try:
        client.request("list_dir", dict(directory=directory)).result(30)
        (tmp_path / "file").write_text("data")
        with pytest.raises(RuntimeError, match="MaxSessions"):
            transfer.Transfer(client).run(str(tmp_path), directory, transfer.scan(tmp_path)["files"], upload=True)
        assert client.request("list_dir", dict(directory=directory)).result(30)
    finally:
        client.close()
