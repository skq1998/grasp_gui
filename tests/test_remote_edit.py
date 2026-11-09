"""远程新建/取出/修订更新、预览、冲突与本机副本；不要求 SSH 守护进程。"""

from concurrent.futures import ThreadPoolExecutor
import copy
import json
from pathlib import Path

import pytest

import grasp_config as config
import grasp_core as core
import grasp_remote as remote
import grasp_remote_edit as edit
import grasp_session as sessions
from test_remote import Peer


@pytest.fixture
def cfg():
    return dict(format=config.FORMAT, version=config.VERSION,
                jobs=[dict(step="rnucleus", vals=config.STEPS["rnucleus"].defaults())],
                files=[dict(name="input.txt", text="input\n")])


def test_new_preview_is_readonly_and_create_roundtrip(tmp_path, cfg):
    operation = remote.Operations()
    root = tmp_path / "new"
    before = operation.preview_upload(str(root), cfg, uploads=["manual.c"])
    assert not root.exists() and before["revision"] is None
    created = operation.create_workdir(str(root), cfg)
    read = operation.get_pipeline(str(root))
    assert read["revision"] == created["revision"]
    assert edit.fingerprint(read["config"]) == edit.fingerprint(cfg)
    raw = (root / core.SESSION_FILE).read_bytes()
    with pytest.raises((ValueError, OSError)):
        operation.create_workdir(str(root), cfg)
    assert (root / core.SESSION_FILE).read_bytes() == raw


def test_concurrent_create_has_one_winner(tmp_path, cfg):
    root = tmp_path / "same"
    def create():
        try:
            return remote.Operations().create_workdir(str(root), cfg)
        except FileExistsError:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda n: create(), range(2)))
    assert sum(result is not None for result in results) == 1
    assert (root / "input.txt").read_text() == "input\n"


def test_preview_marks_selected_inputs_pending(tmp_path):
    cfg = dict(format=config.FORMAT, version=config.VERSION,
               jobs=[dict(step="rangular", vals=dict(config.STEPS["rangular"].defaults(), csf="manual.c"))])
    root = tmp_path / "new"
    operation = remote.Operations()
    missing = operation.preview_upload(str(root), cfg)
    planned = operation.preview_upload(str(root), cfg, uploads=["manual.c"])
    assert any(row["file"] == "manual.c" for row in missing["checked"]["missing"])
    assert not planned["checked"]["missing"]
    assert planned["checked"]["pending"][0]["file"] == "manual.c"
    assert not root.exists()


def test_update_keeps_file_conflicts_and_rechecks_revision(tmp_path, cfg):
    operation = remote.Operations()
    root = tmp_path / "calc"
    original = operation.create_workdir(str(root), cfg)
    changed = copy.deepcopy(cfg)
    changed["jobs"][0]["vals"]["z"] = "26"
    changed["files"][0]["text"] = "replacement\n"
    preview = operation.preview_upload(str(root), changed, original["revision"])
    assert preview["kept_files"] == ["input.txt"]
    updated = operation.update_workdir(str(root), original["revision"], changed)
    assert updated["kept_files"] == ["input.txt"]
    assert (root / "input.txt").read_text() == "input\n"
    raw = (root / core.SESSION_FILE).read_bytes()
    with pytest.raises(sessions.Conflict):
        operation.update_workdir(str(root), original["revision"], cfg)
    with pytest.raises(sessions.Conflict):
        operation.preview_upload(str(root), cfg, original["revision"])
    assert (root / core.SESSION_FILE).read_bytes() == raw


@pytest.mark.parametrize("state", ["running", "queued", "unknown"])
def test_active_or_uncertain_slurm_blocks_update(tmp_path, cfg, monkeypatch, state):
    import grasp_slurm
    operation = remote.Operations()
    root = tmp_path / "calc"
    created = operation.create_workdir(str(root), cfg)
    raw = (root / core.SESSION_FILE).read_bytes()
    monkeypatch.setattr(grasp_slurm, "status", lambda *args: dict(status=state, active=True))
    with pytest.raises(ValueError, match="Slurm"):
        operation.update_workdir(str(root), created["revision"], cfg)
    assert (root / core.SESSION_FILE).read_bytes() == raw


def test_unconfirmed_custom_commands_refused(tmp_path):
    from test_mcp import document
    cfg = document(True)
    operation = remote.Operations()
    root = tmp_path / "custom"
    assert operation.preview_upload(str(root), cfg)["custom_commands"]
    with pytest.raises(ValueError, match="自定义"):
        operation.create_workdir(str(root), cfg)
    assert not root.exists()
    operation.create_workdir(str(root), cfg, allow_custom=True)
    assert (root / core.SESSION_FILE).exists()


def test_checkout_stores_association_only_in_user_preferences(tmp_path, cfg, monkeypatch):
    monkeypatch.setattr(core, "CONFIG_FILE", str(tmp_path / "preferences.json"))
    remote_root, local = tmp_path / "server", tmp_path / "local"
    operation = remote.Operations()
    operation.create_workdir(str(remote_root), cfg)
    reply = operation.get_pipeline(str(remote_root))
    edit.checkout(local, "cluster", str(remote_root), reply)
    assert edit.association(local)["revision"] == reply["revision"]
    assert edit.association(local)["host"] == "cluster"
    assert edit.configuration(local, sessions.read(local)[0]["pipeline"])["files"] == cfg["files"]
    data = json.loads((local / core.SESSION_FILE).read_text())
    assert "remote_copies" not in data and "host" not in data
    with pytest.raises(ValueError):
        edit.checkout(local, "other-server", str(remote_root), reply, replace=True)
    edit.checkout(local, "cluster", str(remote_root), reply, replace=True)


def test_stdio_write_capability_and_mutation_roundtrip(tmp_path, cfg):
    peer = Peer()
    root = str(tmp_path / "server")
    try:
        hello = peer.call("hello", protocol=remote.PROTOCOL)["result"]
        assert edit.CAPABILITY in hello["capabilities"]
        assert "create_workdir" not in remote.READ_ONLY and "update_workdir" not in remote.READ_ONLY
        assert "get_pipeline" in remote.READ_ONLY and "preview_upload" in remote.READ_ONLY
        created = peer.call("create_workdir", directory=root, configuration=cfg)["result"]
        read = peer.call("get_pipeline", directory=root)["result"]
        assert read["revision"] == created["revision"]
        assert edit.fingerprint(read["config"]) == edit.fingerprint(cfg)
        assert "error" in peer.call("update_workdir", directory=root, revision="stale", configuration=cfg)
    finally:
        peer.close()


def test_preview_checks_programs_using_server_run_settings(tmp_path, cfg, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    monkeypatch.setattr(core, "saved_run_settings", lambda: dict(bindir=str(bindir), np=1))
    monkeypatch.setenv("PATH", "")
    operation = remote.Operations()
    root = str(tmp_path / "server")
    assert operation.preview_upload(root, cfg)["missing_programs"] == ["rnucleus"]
    program = bindir / "rnucleus"
    program.write_text("#!/bin/sh\nexit 0\n")
    program.chmod(0o755)
    assert operation.preview_upload(root, cfg)["missing_programs"] == []
    assert not Path(root).exists()
