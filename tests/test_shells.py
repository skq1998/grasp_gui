"""壳层模型用离线 NIST 数据，生成既有 rcsfgenerate 字段。"""

import re
import subprocess
from pathlib import Path

import pytest

from conftest import DEMOS
import grasp_config as config
import grasp_core as core
import grasp_shells as shells


@pytest.mark.parametrize("element,charge,expected,term", [
    ("Cr", 0, {"3d": 5, "4s": 1}, "7S<3>"),
    ("Cu", 0, {"3d": 10, "4s": 1}, "2S<1/2>"),
    ("Gd", 0, {"4f": 7, "5d": 1, "6s": 2}, "9D*<2>"),
    ("Fe", 2, {"3d": 6, "4s": 0}, "5D<4>"),
    (26, 14, {"3s": 2, "3p": 0}, "1S0"),
])
def test_nist_ground_states(element, charge, expected, term):
    info = shells.ground(element, charge)
    counts = {shells.label(shell): occupied for shell, occupied in info.occupations}
    assert {name: counts.get(name, 0) for name in expected} == expected
    assert sum(counts.values()) == shells.element(element) - charge
    assert info.term == term and info.ionization_ev and not info.estimated


def test_snapshot_coverage_estimates_and_source():
    metadata, records = shells.snapshot()
    assert len(records) == 6019 and metadata["source"].startswith("https://physics.nist.gov/")
    for z, charge in records:
        ground = shells.ground(z, charge)
        assert sum(count for _, count in ground.occupations) == z - charge
        assert all(count <= shells.capacity(shell) for shell, count in ground.occupations)
    og = shells.ground("Og", 0)
    assert sum(count for _, count in og.occupations) == 118 and og.estimated
    assert not og.term and not og.ionization_ev
    assert not shells.ground("Li", 3).occupations
    with pytest.raises(ValueError):
        shells.ground("Fe", 27)


@pytest.mark.parametrize("raw,expected", [
    ("9D*<2>", "9D°2"), ("7S<3>", "7S3"), ("2S<1/2>", "2S1/2"), ("1S0", "1S0"),
    ("10D*<5/2>", "10D°5/2"), ("(0,3/2)*<3/2>", "(0,3/2)°3/2"),
    ("<3/2>", "J = 3/2"), ("0", "J = 0"), ("*", "°"), ("", ""),
])
def test_format_ground_term(raw, expected):
    assert shells.format_term(raw) == expected


@pytest.mark.parametrize("lang,theory,semi", [
    ("zh", "理论值", "半经验值"), ("en", "theoretical", "semi-empirical"),
])
def test_format_ionization_preserves_values_and_provenance(lang, theory, semi):
    core.set_lang(lang)
    hydrogen = shells.ground("H", 0)
    neodymium = shells.ground("Nd", 3)
    assert hydrogen.ionization_ev == "(13.598434599702)"
    assert neodymium.ionization_ev == "[40.60]"
    assert shells.format_ionization(hydrogen.ionization_ev, hydrogen.uncertainty_ev) == (
        f"13.598434599702 ± 0.000000000012 eV · {theory}")
    assert shells.format_ionization(neodymium.ionization_ev, neodymium.uncertainty_ev) == (
        f"40.60 ± 0.04 eV · {semi}")
    assert shells.format_ionization("7.726380", "0.000004") == "7.726380 ± 0.000004 eV"
    assert shells.format_ionization("[42]", "") == f"42 eV · {semi}"
    assert shells.format_ionization("", "") == "—"


@pytest.mark.gui
@pytest.mark.parametrize("lang,source", [("zh", "半经验值"), ("en", "semi-empirical")])
def test_shell_dialog_displays_formatted_nist_data(qapp, lang, source):
    from shell_model_panel import ShellModelDialog

    core.set_lang(lang)
    step = core.RcsfGenerate()
    dialog = ShellModelDialog(None, step, step.defaults(), "Nd")
    dialog.value.setValue(3)
    dialog.load_ground()
    text = dialog.origin.text()
    assert "4I°9/2" in text and f"40.60 ± 0.04 eV · {source}" in text
    dialog.close()


def references(core_id, block):
    """由手册参考行建立占据，方便逐字对照模型的输出。"""
    configs = []
    for line in block["configs"].splitlines():
        occ, marks = shells.closed_core(core_id), {}
        parts = re.findall(r"(\d+)([a-z])\((\d+),([i*0-9]+)\)", line)
        assert parts and len(parts) == line.count("(")
        for n, letter, count, mark in parts:
            shell = (int(n), shells.ORBITALS.index(letter))
            occ[shell], marks[shell] = int(count), mark
        configs.append((occ, marks))
    return configs


@pytest.mark.parametrize("name", ["example1", "example2", "example3", "example4", "example5", "case1"])
def test_build_matches_handbook_reference_lists(name):
    cfg, _ = config.load(Path(DEMOS) / (name + ".json"))
    vals = next(job["vals"] for job in cfg["jobs"] if job["step"] == "rcsfgenerate")
    expected = vals["lists"][0]
    refs = references(vals["core"], expected)
    count = sum(refs[0][0].values())
    generated = shells.build_fields(refs, count, vals["core"], expected["active"],
                                    int(expected["jmin"]), int(expected["jmax"]), int(expected["nexc"]))
    assert generated == {"core": vals["core"], "lists": [expected]}


def test_transfer_caps_core_and_parities():
    source = dict(shells.ground("Li", 0).occupations)
    moved = shells.transfer(source, (2, 0), (2, 1))
    assert sum(source.values()) == sum(moved.values()) == 3
    assert (2, 0) not in moved and moved[(2, 1)] == 1
    assert shells.relativistic((2, 1), 3) == [("2p-", 2, 2), ("2p", 1, 4)]
    assert shells.core_suggestion([source, moved]) == "1"
    with pytest.raises(ValueError):
        shells.transfer(source, (2, 0), (1, 0))
    with pytest.raises(ValueError):
        shells.build_fields([(source, {(2, 0): "2"})], 3, "1", "3s,3p", 1, 3, 0)
    result = shells.build_fields([(source, {}), (moved, {})], 3, "1", "3s,3p", 1, 3, 0)
    assert [part["configs"] for part in result["lists"]] == ["2s(1,i)", "2p(1,i)"]


@pytest.mark.grasp
def test_model_and_handbook_produce_same_real_csf_list(tmp_path, grasp_bindir):
    step = core.RcsfGenerate()
    cfg, _ = config.load(Path(DEMOS) / "example1.json")
    original = next(job["vals"] for job in cfg["jobs"] if job["step"] == "rcsfgenerate")
    original = dict(original, lists=original["lists"][:1])
    ref = dict(shells.ground("Li", 0).occupations)
    build = shells.build_fields([(ref, {})], 3, "0", "2s,2p", 1, 1, 0)
    expected = step.stdin(original)
    actual = step.stdin(dict(original, **build))
    assert actual == expected
    for name, text in (("handbook", expected), ("model", actual)):
        root = tmp_path / name
        root.mkdir()
        proc = subprocess.run([str(Path(grasp_bindir) / "rcsfgenerate")], cwd=root, input=text,
                              text=True, capture_output=True, timeout=30)
        assert proc.returncode == 0, proc.stderr
    original_csf = (tmp_path / "handbook/rcsf.out").read_bytes()
    assert original_csf and (tmp_path / "model/rcsf.out").read_bytes() == original_csf


@pytest.mark.gui
@pytest.mark.parametrize("append", [False, True])
def test_shell_dialog_fills_only_core_and_lists(qapp, monkeypatch, append):
    from calc_panel import StepForm
    from shell_model_panel import ShellModelDialog

    form = StepForm(core.RcsfGenerate(), "", lambda: "3")
    form.set_values({"order": "s", "saveas": "original.c"})
    original = form.values()
    preview = []

    def interact(dialog):
        assert dialog.element.text() == "Li" and dialog.electrons == 3
        dialog.element.setText("Fe")
        dialog.value.setValue(14)
        dialog.load_ground()
        assert dialog.electrons == 12 and "Fe" in dialog.origin.text()
        dialog.element.setText("Li")
        dialog.value.setValue(0)
        dialog.load_button.click()
        dialog.copy_ref.click()
        by_name = {dialog.shells.item(i, 0).text(): i for i in range(dialog.shells.rowCount())}
        dialog.select_shell(by_name["2s"], 0)
        dialog.select_shell(by_name["2p"], 0)
        assert {item.text().split()[0] for item in
                (dialog.reference_list.item(0), dialog.reference_list.item(1))} == {"+", "-"}
        assert not dialog.parity_note.isHidden()
        dialog.relativistic.setChecked(True)
        assert dialog.shells.isColumnHidden(3)
        dialog.relativistic.setChecked(False)
        if append:
            dialog.mode.setCurrentIndex(dialog.mode.findData("append"))
        dialog.suggest_active()
        assert dialog.apply_button.isEnabled(), dialog.error.text()
        preview.append(dialog.preview.toPlainText())
        dialog.apply_button.click()
        return dialog.result()

    monkeypatch.setattr(ShellModelDialog, "exec", interact)
    form.build_from_shell_model()
    fields = form.values()
    assert fields["order"] == "s" and fields["saveas"] == "original.c"
    assert len(fields["lists"]) == 2 + (len(original["lists"]) if append else 0)
    assert fields["core"] == (original["core"] if append else "1")
    assert preview == [form.step.stdin(fields)]
