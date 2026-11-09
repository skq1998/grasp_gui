"""按 serial/MPI strsum 的格式识别哈密顿量，并核对同组跨宇称的零点。"""

import json
import os
from pathlib import Path
import struct

import pytest

import grasp_core as core
import grasp_mix as mix
import grasp_summary as summary


def csum(terms=(), se=False, frequency="1.00000000D+00", speed="1.370359991390D+02"):
    text = f"Speed of light = {speed} atomic units.\n\n"
    if terms:
        text += " To H (Dirac Coulomb) is added\n"
        for term in terms:
            if term == "Breit":
                text += "  H (Transverse) --- factor multiplying the photon frequency: " + frequency + ";\n"
            else:
                text += " H (" + {"VP": "Vacuum Polarisation", "NMS": "Normal Mass Shift",
                                     "SMS": "Specific Mass Shift"}[term] + ");\n"
        text += " the total will be diagonalised.\n"
    else:
        text += " H (Dirac Coulomb) will be diagonalised by itself.\n"
    if se:
        text += (" Diagonal contributions from H (Self Energy) will be estimated\n"
                 " from a screened hydrogenic approximation.\n")
    return text + "\nRadial grid: R(I) = RNT*(exp((I-1)*H)-1), I = 1, ..., N;\n"


def write_mix(path, energies, parity=1):
    def record(body):
        return struct.pack("<i", len(body)) + body + struct.pack("<i", len(body))
    n = len(energies)
    path = Path(path)
    path.write_bytes(b"".join(record(body) for body in [
        b"G92MIX", struct.pack("<6i", 3, 1, 2, n, n, 1), struct.pack("<5i", 1, 1, n, 2, parity),
        struct.pack(f"<{n}i", *range(1, n + 1)),
        struct.pack(f"<{n + 1}d", energies[0], *(e - energies[0] for e in energies)),
        struct.pack(f"<{n}d", *([1.0] * n))]))
    return path


@pytest.mark.parametrize("terms,se,group", [
    ((), False, "DC"), ((), True, "DC+SE"), (("Breit", "VP"), True, "DC+Breit+VP+SE"),
    (("Breit", "VP", "NMS", "SMS"), True, "DC+Breit+VP+NMS+SMS+SE"),
    (("SMS",), False, "DC+SMS")])
def test_serial_and_mpi_hamiltonian_headers(terms, se, group):
    parsed = mix.parse_hamiltonian(csum(terms, se))
    assert parsed == dict(zero_group=group, zero_warning="")
    assert mix.parse_hamiltonian(csum(terms, se).replace("D+", "E+")) == parsed


def test_speed_and_breit_frequency_are_part_of_group():
    assert mix.parse_hamiltonian(csum(("Breit",), frequency="1.D0"))["zero_group"] == "DC+Breit"
    assert mix.parse_hamiltonian(csum(("Breit",), frequency="0.D0"))["zero_group"] == "DC+Breit(f=0)"
    assert mix.parse_hamiltonian(csum(speed="1.37D+02"))["zero_group"] == "DC(c=137)"
    assert mix.parse_hamiltonian(csum(("VP", "NMS"))) == mix.parse_hamiltonian(csum(("NMS", "VP")))


@pytest.mark.parametrize("failure", ["missing", "corrupt", "stale", "truncated", "unreadable", "overflow"])
def test_uncertain_summary_is_unknown_with_warning(tmp_path, monkeypatch, failure):
    path = write_mix(tmp_path / "state.cm", [-7.4])
    summed = path.with_suffix(".csum")
    if failure != "missing":
        text = "garbage" if failure == "corrupt" else csum()
        if failure == "truncated":
            text = text.split("Radial grid:")[0]
        if failure == "overflow":
            text = csum(speed="1D999999999")
        summed.write_text(text)
    if failure == "stale":
        stamp = path.stat().st_mtime - 301
        os.utime(summed, (stamp, stamp))
    if failure == "unreadable":
        original = os.open
        def open_file(name, *args, **kwargs):
            if Path(name) == summed:
                raise PermissionError("fixture unreadable")
            return original(name, *args, **kwargs)
        monkeypatch.setattr(os, "open", open_file)
    result = mix.hamiltonian(path)
    assert result["zero_group"] == "unknown" and "无法确定" in result["zero_warning"]


def test_unknown_terms_are_not_silently_treated_as_dc():
    for text in (csum(("VP",)).replace("Vacuum Polarisation", "Unrecognised operator"),
                 csum(("VP",)).replace(";", ""),
                 csum().replace("Speed of light", "Missing speed")):
        with pytest.raises(ValueError):
            mix.parse_hamiltonian(text)


def test_dc_saved_m_and_rci_cm_share_zero_across_parities(tmp_path):
    paths = [write_mix(tmp_path / "even.m", [-100, -99]),
             write_mix(tmp_path / "odd.cm", [-99.8, -99.6], -1),
             write_mix(tmp_path / "qed.cm", [-158, -157.5])]
    paths[1].with_suffix(".csum").write_text(csum())
    paths[2].with_suffix(".csum").write_text(csum(("Breit", "VP"), True))
    values = [(mix.hamiltonian(p)["zero_group"], lv.energy) for p in paths for b in mix.read_mix(p) for lv in b.levels]
    zeros = mix.energy_zeros(values)
    assert zeros == {"DC": -100, "DC+Breit+VP+SE": -158}
    assert (-99.8 - zeros["DC"]) * mix.HARTREE_CM == pytest.approx(0.2 * mix.HARTREE_CM)
    assert mix.energy_zeros(values, unified=True) == {group: min(e for _, e in values) for group, _ in values}
    assert mix.energy_zeros([]) == {}


def test_summary_collect_and_render_keep_hamiltonian_zeros(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "CONFIG_FILE", str(tmp_path / "preferences.json"))
    for name, energies, terms, se in (("even", [-100, -99], (), False),
                                     ("odd", [-99.8], (), False),
                                     ("qed", [-158, -157.5], ("Breit", "VP"), True)):
        write_mix(tmp_path / (name + ".cm"), energies)
        (tmp_path / (name + ".csum")).write_text(csum(terms, se))
    data = summary.collect(tmp_path, [], [])
    assert data["relative_zero_hartree"] == {"DC": -100, "DC+Breit+VP+SE": -158}
    odd = next(layer for layer in data["levels"] if layer["file"] == "odd.cm")["levels"][0]
    assert odd["zero_group"] == "DC" and odd["relative_cm"] == pytest.approx(0.2 * mix.HARTREE_CM)
    text = summary.render(data)
    header = json.loads(text.split("```json\n")[1].split("```", 1)[0])
    assert header["relative_zero_hartree"] == data["relative_zero_hartree"]
    assert "DC+Breit+VP+SE" in text


def test_summary_unknown_group_is_shared_and_warned(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "CONFIG_FILE", str(tmp_path / "preferences.json"))
    write_mix(tmp_path / "one.cm", [-7.4])
    write_mix(tmp_path / "two.cm", [-7.3])
    data = summary.collect(tmp_path, [], [])
    assert data["relative_zero_hartree"] == {"unknown": -7.4}
    assert len(data["warnings"]) == 2 and all("无法确定" in w["error"] for w in data["warnings"])


def test_remote_fields_include_group_and_invalidate_csum_signature(tmp_path):
    import grasp_remote
    path = write_mix(tmp_path / "state.cm", [-7.4, -7.3])
    path.with_suffix(".csum").write_text(csum())
    operation = grasp_remote.Operations()
    initial = operation.view(str(tmp_path), "levels")
    assert {row["zero_group"] for row in initial["data"]["levels"]} == {"DC"}
    assert all(row["zero_warning"] == "" for row in initial["data"]["levels"])
    path.with_suffix(".csum").write_text(csum(("Breit",), frequency="0.D0"))
    changed = operation.view(str(tmp_path), "levels", known_signature=initial["signature"])
    assert not changed.get("unchanged")
    assert {row["zero_group"] for row in changed["data"]["levels"]} == {"DC+Breit(f=0)"}
    brief = operation.levels(str(tmp_path), brief=True)
    assert brief["levels"][0]["zero_group"] == "DC+Breit(f=0)"


def test_summary_path_obeys_service_root(tmp_path):
    from grasp_service import Service
    root = tmp_path / "allowed"
    root.mkdir()
    path = write_mix(root / "state.cm", [-7.4])
    outside = tmp_path / "private.csum"
    outside.write_text(csum())
    path.with_suffix(".csum").symlink_to(outside)
    service = Service(str(root))
    assert mix.hamiltonian(path, service.path)["zero_group"] == "unknown"
    with pytest.raises(ValueError, match="根目录"):
        service.levels(str(root))
