"""可选的真实 SSH 验收；专用测试账户和目录的准备见 REMOTE.md。"""

import os
import queue
import time

import pytest

import grasp_ssh as ssh

pytestmark = pytest.mark.skipif(not os.environ.get("GRASP_SSH_TEST_HOST"),
                                reason="dedicated SSH fixture not configured")


@pytest.fixture
def client():
    options = ["-F", os.environ["GRASP_SSH_TEST_CONFIG"]] if os.environ.get("GRASP_SSH_TEST_CONFIG") else []
    peer = ssh.Client(os.environ["GRASP_SSH_TEST_HOST"], os.environ.get("GRASP_SSH_TEST_PROGRAM", ""), options=options)
    yield peer
    peer.close()


def call(client, method, directory, **args):
    return client.request(method, dict(directory=directory, **args), cached=method == "view").result(timeout=90)


def test_real_ssh_results_and_stop(client):
    root = os.environ["GRASP_SSH_TEST_DIRECTORY"]
    rows = call(client, "list_dir", root + "/example1")
    assert any(r["type"] == "mixing" for r in rows["files"])
    levels = call(client, "view", root + "/example1", kind="levels")
    assert levels["data"]["levels"]
    waves = call(client, "view", root + "/example1", kind="wfn", points=100)
    assert waves["data"]["curves"] and all(len(c["r"]) <= 100 for c in waves["data"]["curves"])
    directory = root + "/ssh-stop"
    revision = call(client, "pipeline", directory)["revision"]
    assert call(client, "run", directory, revision=revision, allow_custom=True, accept_logout=True)["started"]
    result = call(client, "stop", directory)
    assert result["stop_requested"]
    assert call(client, "run_status", directory)["state"]["status"] == "stopped"


@pytest.mark.parametrize("moment", ["startup", "running"])
def test_real_ssh_disconnect_entire_pipeline(client, moment):
    directory = os.environ["GRASP_SSH_TEST_DIRECTORY"] + "/ssh-" + moment
    revision = call(client, "pipeline", directory)["revision"]
    assert call(client, "run", directory, revision=revision, allow_custom=True, accept_logout=True)["started"]
    if moment == "running":
        time.sleep(6)
    client.process.kill()
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        state = call(client, "run_status", directory)["state"]
        if state["status"] != "running":
            break
        time.sleep(1)
    assert state["status"] == "ok" and state["step"] == 2
    log = call(client, "tail_log", directory)["text"]
    assert all("step-" + str(i) in log for i in range(3))
    events = []
    while True:
        try:
            events.append(client.events.get_nowait()["event"])
        except queue.Empty:
            break
    assert "disconnected" in events and events.count("connected") >= 2
