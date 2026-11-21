"""能级收敛按文本跟踪，覆盖对称性、主成分变化和受限 MCP。"""

from dataclasses import replace
import json
import struct

import pytest

import grasp_converge as converge
import grasp_core as core
import grasp_mix as mix
import grasp_service as service
from test_large_files import record
from test_mcp import Client, data, settings  # noqa: F401


def state(name, energy, *, j="1/2", twoj=1, parity=-1, position=1, purity=95, lsj="", term="", other=None):
    primary = (name, "1/2")
    return converge.State(j, parity, twoj, 1, position, energy, primary,
                          (primary, (other, "1/2")) if other else (primary,), purity, lsj, term, (("2p", 1),))


def layer(name, *states):
    return converge.Layer(name, states, dict(missing=True))


def test_increment_ratio_remainder_status_and_reference():
    layers = [layer(str(i), state("2p", -8 + i / 100, position=1),
                    state("3p", -8 + i / 100 + (0.04 - delta / 64), position=2))
              for i, delta in enumerate((4, 2, 1, .5))]
    result = converge.analyze(layers)
    row = result["rows"][1]
    last = row["points"][-1]
    assert last["ratio"] == pytest.approx(.5)
    assert last["delta_cm"] == pytest.approx(mix.HARTREE_CM / 128)
    assert last["remainder_cm"] == pytest.approx(last["delta_cm"])
    assert row["status"] == "converging"
    assert last["total_delta_cm"] != last["delta_cm"]
    assert converge.analyze(layers, threshold=2000)["rows"][1]["status"] == "converged"
    chosen = converge.analyze(layers, reference=row["id"])
    assert all(point["excitation_cm"] == 0 for point in chosen["rows"][1]["points"])
    assert chosen["rows"][0]["points"][-1]["excitation_cm"] < 0
    assert core.T("阈值 {n} cm⁻¹，参考能级 {ref}", n="1", ref=row["id"]) in converge.markdown(chosen)
    assert f"1.0,{row['id']},False" in converge.csv_text(chosen)
    limited = converge.analyze(layers, limit=1)
    assert limited["truncated"]
    assert core.T("受数据或行数上限影响，部分结果未列出。") in converge.markdown(limited)


@pytest.mark.parametrize("energies,reason", [
    ([1, 2], "insufficient"), ([1, 2, 3], "increasing"), ([1, 2, 1], "alternating"),
    ([1, 1, 2], "zero_previous"), ([1, 2, 2], "zero_current"),
])
def test_ratio_is_only_used_when_supported(energies, reason):
    layers = [layer(str(i), state("2p", 0), state("3p", energy, position=2)) for i, energy in enumerate(energies)]
    last = converge.analyze(layers)["rows"][1]["points"][-1]
    assert last["reason"] == reason and last["remainder_cm"] is None


def test_ambiguous_and_unmatched_and_changed_composition():
    initial = layer("a", state("2p", 0, position=1, other="3p"), state("4p", 1, position=2),
                    state("4p", 2, position=3), state("8p", 9, position=4))
    final = layer("b", state("3p", 0.1, position=1, other="2p"), state("4p", 2, position=2),
                  state("4p", 3, position=3), state("9p", 10, position=4))
    result = converge.analyze([initial, final])
    changed, tie_one, tie_two, missing, appeared = result["rows"]
    assert changed["status"] == "changed" and changed["points"][-1]["primary"][0] == "3p"
    assert tie_one["ambiguous"] and tie_two["ambiguous"]
    assert missing["points"][-1]["total_hartree"] is None
    assert appeared["points"][0]["total_hartree"] is None
    assert missing["status"] == appeared["status"] == "unmatched"


def test_lsj_evidence_disambiguates_an_exchange_of_main_csf():
    a = state("2p", 0, position=1, lsj="2p_2P")
    b = state("3p", 1, position=2, lsj="3p_2P")
    change = [replace(b, primary=a.primary, energy=.9), replace(a, primary=b.primary, energy=.2)]
    rows = converge.analyze([layer("a", a, b), layer("b", *change)])["rows"]
    assert [row["status"] for row in rows] == ["changed", "changed"]
    assert rows[0]["points"][-1]["total_hartree"] == .2


def test_fine_structure_splittings():
    results = converge.analyze([layer(str(i), state("2p", -7, term="2P"),
                                     state("2p", -7 + delta, j="3/2", twoj=3, position=2, term="2P"))
                               for i, delta in enumerate((.1, .05, .025))])
    splitting, = results["splittings"]
    assert splitting["lower_j"] == "1/2" and splitting["higher_j"] == "3/2"
    assert splitting["splitting_cm"] == pytest.approx([v * mix.HARTREE_CM for v in (.1, .05, .025)])
    assert splitting["points"][-1]["ratio"] == pytest.approx(.5)
    assert "Splitting (cm^-1)" in converge.csv_text(results)
    text = converge.markdown(results)
    assert core.T("精细结构分裂及增量 (cm⁻¹)") in text
    assert "1/2- → 3/2-" in text and "2p^1" in text


def write_layer(path, orbitals, energies, columns):
    ncf, nev = len(orbitals), len(energies)
    body = [record(b"G92MIX"), record(struct.pack("<6i", 3, ncf, 3, nev, ncf * nev, 1)),
            record(struct.pack("<5i", 1, ncf, nev, 2, -1)),
            record(struct.pack(f"<{nev}i", *range(1, nev + 1))),
            record(struct.pack(f"<{nev + 1}d", -7, *energies)),
            record(struct.pack(f"<{ncf * nev}d", *[c for column in columns for c in column]))]
    path.write_bytes(b"".join(body))
    path.with_suffix(".c").write_text("Core subshells:\n 1s\nPeel subshells:\n 2p- 3p-\nCSF(s):\n" +
                                     "".join(f" {orb}( 1)\n\n 1/2-\n" for orb in orbitals))


def test_zero_coefficients_do_not_match_different_states(tmp_path):
    write_layer(tmp_path / "first.m", ("2p-", "3p-"), (0.,), ((1., 0.),))
    write_layer(tmp_path / "second.m", ("2p-", "3p-"), (.05,), ((0., 1.),))
    layers = converge.read_layers(tmp_path, ["first.m", "second.m"])

    first, second = converge.analyze(layers)["rows"]
    assert first["status"] == second["status"] == "unmatched"
    assert first["points"][1]["total_hartree"] is None
    assert second["points"][0]["total_hartree"] is None


def test_read_csf_identifiers_in_visible_order_and_scf_log(tmp_path):
    write_layer(tmp_path / "layer10.m", ("2p-", "3p-"), (0, .1), ((1., 0.), (0., 1.)))
    write_layer(tmp_path / "layer3.m", ("3p-", "2p-"), (-.01, .08), ((1., 0.), (0., 1.)))
    (tmp_path / "scf.txt").write_text(
        "Iteration number 1\nWeighted average energy of these levels = -6.5\n"
        "Iteration number 2\nWeighted average energy of these levels = -7.0\nRMCDHF: Execution complete.\n")
    step = core.RMcdhf()
    first = step.job(dict(step.defaults(), save="layer10", log="scf.txt"))
    second = step.job(dict(step.defaults(), save="layer3", log=""))
    jobs = [dict(first, group="odd"), dict(second, group="odd")]
    names = converge.ordered_files(tmp_path, jobs, "odd")
    assert names == ["layer10.m", "layer3.m"]
    result = converge.analyze(converge.read_layers(tmp_path, names, jobs=jobs))
    row = result["rows"][0]
    assert row["primary"][0] == "2p-(1)"
    assert row["points"][1]["total_hartree"] == pytest.approx(-6.92)
    assert row["points"][1]["primary"] == row["primary"] and not row["ambiguous"]
    assert result["layers"][0]["scf"]["converged"] and result["layers"][0]["scf"]["iterations"] == 2
    assert result["layers"][1]["scf"]["missing"]
    text = converge.markdown(result)
    comma_separated = converge.csv_text(result)
    assert "layer10.m | scf.txt | " + core.T("已收敛") + " | 2 | 2 | -0.5" in text
    assert "layer3.m | — | " + core.T("无日志") in text
    assert "layer10.m,scf.txt,converged,2,2,-0.5," in comma_separated
    assert "layer3.m,,missing,,,," in comma_separated


def test_mcp_matches_module_and_refuses_paths_outside_root(tmp_path, settings):
    root = tmp_path / "calc"
    root.mkdir()
    for name, energy in (("first.cm", 0), ("second.cm", .1)):
        write_layer(root / name, ("2p-", "3p-"), (0, energy), ((1., 0.), (0., 1.)))
    files = ["first.cm", "second.cm"]
    client = Client(root, settings, no_qt=True)
    try:
        client.initialize()
        reply = client.call("convergence", directory=".", files=files)
        expected = service.Service(str(root)).convergence(".", files=files)
        assert data(reply) == json.loads(json.dumps(expected))
        reference = expected["rows"][-1]["id"]
        result = data(client.call("convergence", directory=".", files=files, threshold=1.25, reference=reference))
        assert result["threshold_cm"] == 1.25 and result["reference"] == reference
        assert client.call("convergence", directory=".", files=files, threshold=0)["isError"]
        assert client.call("convergence", directory=".", files=files, threshold=True)["isError"]
        assert client.call("convergence", directory=".", files=["../outside.cm"])["isError"]
        (root / "escape.cm").symlink_to(tmp_path / "outside.cm")
        assert client.call("convergence", directory=".", files=["escape.cm"])["isError"]
        step = core.RMcdhf()
        job = dict(step.job(dict(step.defaults(), save="outside")), group="odd")
        write_layer(tmp_path / "outside.m", ("2p-", "3p-"), (0, .1), ((1., 0.), (0., 1.)))
        (root / "outside.m").symlink_to(tmp_path / "outside.m")
        record_path = root / core.SESSION_FILE
        record_path.write_text(json.dumps(dict(pipeline=[job])))
        result = client.call("convergence", directory=".", group="odd")
        assert result["isError"] and result["content"][0]["text"] == core.T("路径必须位于允许的根目录中")
        record_path.unlink()
        outside = tmp_path / core.SESSION_FILE
        outside.write_text(json.dumps(dict(pipeline=[])))
        record_path.symlink_to(outside)
        result = client.call("convergence", directory=".", files=files)
        assert result["isError"] and result["content"][0]["text"] == core.T("路径必须位于允许的根目录中")
    finally:
        client.close()
