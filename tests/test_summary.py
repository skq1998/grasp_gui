"""逐层摘要的证据、文件推算、成分上限与不改变既有输入的回归。"""

import copy
import json
from pathlib import Path
import socket
import struct

import pytest

import grasp_bg as bg
import grasp_config as config
import grasp_core as core
import grasp_mix as mix
import grasp_summary as summary
from conftest import DEMOS
from test_large_files import make_files

DATA = Path(__file__).parent / "data"


def job(key, **values):
    step = config.STEPS[key]
    return step.job(dict(step.defaults(), **values))


def test_complete_manual_pipelines_have_no_false_missing_files(tmp_path):
    for name in ("case1", "case2", "example1"):
        cfg, _ = config.load(Path(DEMOS) / (name + ".json"))
        jobs = config.session(cfg)["pipeline"]
        assert not summary.check_files(tmp_path, jobs)["missing"], name
        assert not summary.check_files(tmp_path, jobs, run=dict(np=2, mpitmp="mpi"))["missing"], name


def test_missing_layer_typo_deleted_producer_and_unknown_steps(tmp_path):
    jobs = [job("rnucleus"), job("rcsfgenerate", saveas="odd3.c"), job("rangular", csf="odd_3.c")]
    result = summary.check_files(tmp_path, jobs)
    assert len(result["missing"]) == 1 and result["missing"][0]["file"] == "odd_3.c"
    assert "odd3.c" in result["missing"][0]["similar"]
    result = summary.check_files(tmp_path, jobs[::2])
    assert any(row["file"] == "odd_3.c" for row in result["missing"])
    result = summary.check_files(tmp_path, [job("custom")])
    assert not result["missing"] and len(result["skipped"]) == 1
    assert "无法检查" in result["skipped"][0]["reason"]


def test_fixed_intermediates_save_moves_and_run_start(tmp_path):
    jobs = [job("rnucleus"), job("rcsfgenerate", saveas="a.c"), job("rangular", csf="a.c"),
            job("rwfnestimate"), job("rmcdhf", save="a"), job("rsave", state="b")]
    result = summary.check_files(tmp_path, jobs)
    assert [row["file"] for row in result["missing"]] == ["rmix.out"]
    assert any(row["file"] == "a.c" for row in summary.check_files(tmp_path, jobs, start=2)["missing"])
    (tmp_path / "a.c").write_text("existing")
    result = summary.check_files(tmp_path, jobs[2:4])
    assert [row["file"] for row in result["missing"]] == ["isodata"]


@pytest.mark.parametrize("name,converged,count", [("scf_converged", True, 3), ("scf_unconverged", False, 1)])
def test_scf_format_and_failure_marker(name, converged, count):
    result = summary.scf_log(DATA / (name + ".txt"))
    assert result["converged"] is converged and result["iterations"] == count
    assert len(result["last_iterations"]) <= 3
    if converged:
        last = result["last_iterations"][-1]
        assert last["max_printed_self_consistency"] == pytest.approx(7.63e-8)
        assert last["delta_weighted_energy_hartree"] == pytest.approx(-1e-10, abs=1e-14)


def test_incomplete_and_missing_or_reused_log(tmp_path):
    path = tmp_path / "running.txt"
    path.write_text("Iteration number 1\nWeighted average energy of these levels = -7.1D+00\n")
    assert summary.scf_log(path)["converged"] is None
    jobs = [job("rmcdhf", log=""), job("rmcdhf", log="running.txt"), job("rmcdhf", log="running.txt")]
    cfg = config.export(jobs, dict(title="test"))
    rows = summary.collect(tmp_path, cfg, jobs)["scf"]
    assert rows[0]["reason"] == "无日志，无法判断"
    assert "后续步骤复用" in rows[1]["reason"]
    assert rows[2]["converged"] is None


def test_status_uses_latest_run_and_does_not_guess_ambiguous_titles(tmp_path):
    jobs = [job("rnucleus"), job("rcsfgenerate"), job("rangular")]
    history = [dict(first=0, keys=[summary.job_key(j) for j in jobs[:2]], basis="session",
                    state=dict(start="2026-01-01", pid=0, titles=[j["title"] for j in jobs[:2]],
                               status="ok", step=1))]
    bg.write_state(tmp_path, dict(pid=0, host=socket.gethostname(), start="2026-01-02", status="failed",
                                  step=0, titles=[jobs[2]["title"]]))
    rows = summary.run_status(jobs, tmp_path, history)
    assert [row["status"] for row in rows] == ["success", "success", "failed"]
    bg.write_state(tmp_path, dict(pid=0, host=socket.gethostname(), start="2026-01-01", status="ok",
                                  step=1, titles=[j["title"] for j in jobs[:2]]))
    changed = [job("rnucleus", z="4"), *jobs[1:]]
    assert summary.run_status(changed, tmp_path, history)[0]["status"] == "unknown"
    bg.write_state(tmp_path, dict(pid=0, host=socket.gethostname(), start="2026-01-03", status="ok",
                                  step=0, titles=[jobs[0]["title"]]))
    assert summary.run_status([jobs[0], jobs[0]], tmp_path)[0]["status"] == "unknown"


def results(tmp_path):
    p = make_files(tmp_path, ncf=12, nev=2)
    raw = p.read_bytes()
    assert raw.count(struct.pack("<d", -7.4)) == 1
    for state, shift in (("z_first", -7.4), ("a_next", -7.401)):
        (tmp_path / (state + ".cm")).write_bytes(raw.replace(struct.pack("<d", -7.4), struct.pack("<d", shift)))
        (tmp_path / (state + ".c")).write_bytes(p.with_suffix(".c").read_bytes())
    jobs = [dict(job("rci", state=state), group="odd") for state in ("z_first", "a_next")]
    return summary.collect(tmp_path, config.export(jobs, dict(title="test")), jobs)


def test_levels_csf_counts_order_and_unmatched_symmetries(tmp_path):
    data = results(tmp_path)
    assert [layer["file"] for layer in data["levels"][:2]] == ["z_first.cm", "a_next.cm"]
    assert len(data["levels"][0]["levels"][0]["components"]) == 3
    assert data["csfs"][0]["blocks"][0]["count"] == 12
    changes = summary.layer_changes(data["levels"])
    assert changes[0]["layers"][1]["delta_cm"] == pytest.approx(-0.001 * mix.HARTREE_CM)
    data["levels"][1]["levels"].pop()
    assert summary.layer_changes(data["levels"])[1]["layers"][1]["matched"] is False
    data = summary.collect(tmp_path, data["configuration"], [dict(job("rci", state="missing"), group="odd")])
    assert data["levels"][0]["missing"]


def test_per_block_limit_parts_and_complete_json_configuration(tmp_path):
    data = results(tmp_path)
    text = summary.render(data, ["configuration", "layer_changes"])
    assert "## 当前完整配置" in text and "## 能级与主要成分" not in text
    blocks = text.split("```json\n")[1:]
    parsed = [json.loads(block.split("\n```", 1)[0]) for block in blocks]
    assert parsed[-1] == data["configuration"]          # 配置是附加部分，放在最后
    with pytest.raises(ValueError):
        summary.render(data, [])
    jobs = [job("rci", state="z_first")]
    limited = summary.collect(tmp_path, data["configuration"], jobs, per_block=1)
    assert len(limited["levels"][0]["levels"]) == 1 and limited["levels"][0]["omitted_levels"] == 1
    with pytest.raises(ValueError):
        summary.collect(tmp_path, data["configuration"], jobs, per_block=0)


def test_summary_byte_limit_components_then_earlier_layers(tmp_path):
    data = results(tmp_path)
    data["levels"] = data["levels"][:2]
    for layer in data["levels"]:
        for row in layer["levels"]:
            row["components"] = ["组态" * 1000]
    text = summary.render(data, ["levels"], max_bytes=5000)
    assert len(text.encode("utf-8")) <= 5000 and "省略主要成分" in text
    data["levels"] = [dict(copy.deepcopy(data["levels"][0]), file=f"layer{i}.cm") for i in range(30)]
    text = summary.render(data, ["levels", "layer_changes"], max_bytes=3000)
    assert len(text.encode("utf-8")) <= 3000 and "省略较早或未关联的结果" in text
    with pytest.raises(ValueError):
        summary.render(data, ["configuration"], max_bytes=100)


def test_large_summary_uses_bounded_source_reads(tmp_path, monkeypatch):
    import builtins

    path = make_files(tmp_path, ncf=200000, nev=4)
    requested = []
    original = builtins.open

    class Guard:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return self.stream.__exit__(*args)

        def __getattr__(self, name):
            return getattr(self.stream, name)

        def __iter__(self):
            return iter(self.stream)

        def read(self, count=-1):
            assert 0 <= count < path.stat().st_size
            requested.append(count)
            return self.stream.read(count)

    def guarded(name, *args, **kwargs):
        stream = original(name, *args, **kwargs)
        return Guard(stream) if str(name) in (str(path), str(path.with_suffix(".c"))) else stream

    monkeypatch.setattr(builtins, "open", guarded)
    monkeypatch.setattr(mix, "read_mix", lambda *args: pytest.fail("eager mixing reader"))
    monkeypatch.setattr(mix, "read_csfs", lambda *args: pytest.fail("eager CSF reader"))
    jobs = [job("rci", state="state")]
    cfg = config.export(jobs, dict(title="large"))
    data = summary.collect(tmp_path, cfg, jobs, per_block=2)
    assert len(data["levels"][0]["levels"]) == 2 and not data["warnings"]
    assert len(summary.render(data).encode("utf-8")) <= summary.MAX_BYTES
    assert requested and max(requested) < path.stat().st_size


def test_running_state_does_not_reuse_old_convergence(tmp_path):
    import os

    jobs = [job("rmcdhf", log="scf.txt")]
    (tmp_path / "scf.txt").write_bytes((DATA / "scf_converged.txt").read_bytes())
    bg.write_state(tmp_path, dict(pid=os.getpid(), host=socket.gethostname(), start="2026-01-01",
                                  status="running", step=0, titles=[jobs[0]["title"]]))
    data = summary.collect(tmp_path, config.export(jobs, dict(title="running")), jobs)
    assert data["scf"][0]["converged"] is None
    assert "仍在运行" in data["scf"][0]["reason"]


def test_runs_in_same_second_keep_distinct_statuses(tmp_path):
    jobs = [job("rnucleus"), job("rangular")]
    history = [dict(first=0, keys=[summary.job_key(jobs[0])], basis="session",
                    state=dict(start="2026-01-01", pid=1, titles=[jobs[0]["title"]], status="ok", step=0))]
    bg.write_state(tmp_path, dict(pid=2, host=socket.gethostname(), start="2026-01-01", status="failed", step=0,
                                  titles=[jobs[1]["title"]]))
    assert [r["status"] for r in summary.run_status(jobs, tmp_path, history)] == ["success", "failed"]


def test_summary_rejects_non_json_numbers(tmp_path):
    data = results(tmp_path)
    data["levels"][0]["levels"][0]["energy_hartree"] = float("nan")
    with pytest.raises(ValueError):
        summary.render(data, ["levels"])


def test_selected_duplicate_steps_keep_their_pipeline_position():
    a, b = job("rnucleus"), job("rnucleus")
    assert summary.locate([a, b], [b]) == 1
    assert summary.locate([a, b], [copy.deepcopy(b)]) is None


def test_omitted_layers_drop_corresponding_result_sections(tmp_path):
    data = results(tmp_path)
    source = data["levels"][0]
    data["levels"] = [dict(copy.deepcopy(source), file=f"layer{i}.cm", step=i + 1) for i in range(10)]
    data["scf"] = [dict(step=i + 1, converged=True, details="SCF" * 80) for i in range(10)]
    data["csfs"] = [dict(file=f"layer{i}.c", blocks=[dict(J_parity="1/2-", count=12)]) for i in range(10)]
    text = summary.render(data, ["levels", "scf", "csfs"], max_bytes=6000)
    parts = [json.loads(block.split("\n```", 1)[0]) for block in text.split("```json\n")[1:]]
    meta, levels, scf, csfs = parts
    assert len(levels) < 10 and meta["omissions"]
    assert {r["step"] for r in scf} == {r["step"] for r in levels}
    assert {Path(r["file"]).stem for r in csfs} == {Path(r["file"]).stem for r in levels}


def test_scf_distinguishes_block_averages_and_optimized_levels(tmp_path):
    p = tmp_path / "energies.txt"
    p.write_text("Iteration number 1\nAverage energy = -7.0D+00 Hartrees\n"
                 "Level  1    Energy = -7.5D+00    Weight = 1.00000D+00\n"
                 "Iteration number 2\nAverage energy = -7.1D+00 Hartrees\n"
                 "Level  1    Energy = -7.6D+00    Weight = 1.00000D+00\nRMCDHF: Execution complete.\n")
    last = summary.scf_log(p)["last_iterations"][-1]
    assert last["block_average_energies_hartree"] == [-7.1]
    assert last["optimized_levels"] == [dict(index=1, energy_hartree=-7.6, weight=1.0)]
    assert last["delta_optimized_energy_hartree"] == pytest.approx(-0.1)


def test_rmcdhf_sum_final_consistency_and_occupations():
    """endsum.f90 的两张表：每个轨道最终的自洽程度和广义占据数 (case1 even3 的真实输出)。"""
    data = summary.rmcdhf_sum(DATA / "rmcdhf_even3.sum")
    rows = {o["name"]: o for o in data["orbitals"]}
    assert list(rows)[:4] == ["1s", "2s", "2p-", "2p"] and "3d" in rows
    assert rows["1s"]["occupation"] == pytest.approx(1.99996)
    assert rows["3p"]["self_consistency"] == pytest.approx(2.931e-07)
    assert data["max_final_self_consistency"] == max(o["self_consistency"] for o in data["orbitals"])


def test_summary_marks_new_layer_orbitals_from_sum_files(tmp_path):
    """同一态组上一次 rmcdhf 之后新增的轨道标为 new；没有日志时仍给出 .sum 的最终自洽程度。"""
    (tmp_path / "even3.sum").write_bytes((DATA / "rmcdhf_even3.sum").read_bytes())
    (tmp_path / "even4.sum").write_bytes((DATA / "rmcdhf_even4.sum").read_bytes())
    jobs = [job("rmcdhf", save="even3", log=""), job("rmcdhf", save="even4", log=""), job("rmcdhf", save="even5")]
    for j in jobs:
        j["group"] = "even"
    data = summary.collect(tmp_path, None, jobs)
    first, second, third = data["scf"]
    assert not any("new" in o for o in first["sum"]["orbitals"])
    new = {o["name"] for o in second["sum"]["orbitals"] if o["new"]}
    assert new and all(name.startswith("4") for name in new)
    assert second["reason"] == core.T("无日志，迭代过程未知；最终自洽程度见 .sum")
    assert third["sum"] == dict(sum_file="even5.sum", missing=True)
    text = summary.render(data, ["scf"])
    assert "max_final_self_consistency" in text


def test_sum_without_orbital_tables_is_reported(tmp_path):
    (tmp_path / "odd2.sum").write_text("There are 5 electrons in the cloud\n")
    data = summary.collect(tmp_path, None, [job("rmcdhf", save="odd2", log="")])
    assert "error" in data["scf"][0]["sum"]
