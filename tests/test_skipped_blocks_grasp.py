"""重新生成空块文件，并用独立子集和真实 GRASP 核对跃迁。"""

from pathlib import Path
import re
import shutil
import struct
import subprocess

import pytest

import grasp_config as config
import grasp_core as core
import grasp_mix as mix
import grasp_trans as trans
from test_large_files import record

pytestmark = pytest.mark.grasp


def execute(jobs, directory, bindir):
    script = core.shell_script(jobs, bindir, str(directory))
    result = subprocess.run(["sh", "-ec", script], cwd=directory, env=core.child_env(),
                            capture_output=True, timeout=120)
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-3000:]


def job(key, **changes):
    step = config.STEPS[key]
    return step.job(dict(step.defaults(), **changes))


@pytest.fixture(scope="module")
def calculated(tmp_path_factory, grasp_bindir):
    root = tmp_path_factory.mktemp("skipped-grasp")
    lists = [dict(configs="1s(2,i)2s(1,i)\n1s(2,i)2p(1,i)", active="2s,2p", jmin="1", jmax="3", nexc="0")]
    setup = [job("rnucleus", z="3", a="7"), job("rcsfgenerate", lists=lists, saveas="base.c"),
             job("rangular", csf="base.c"), job("rwfnestimate")]
    execute(setup, root, grasp_bindir)
    scf = job("rmcdhf", levels="1\n1\n1", nscf="20", save="base", log="scf.log")
    execute([scf], root, grasp_bindir)
    for state, skip in (("ci", 0), ("mid", 1)):
        ci = job("rci", state=state, copyfrom="base", levels="1\n1\n1", trans=False,
                 modfreq=False, vp=False, se=False, log=state + ".log")
        lines = ci["stdin"].splitlines()
        # 对照在终端直接给每个块的回答。
        lines[-3 + skip] = ""
        level_rows = ["1", "1", "1"]
        level_rows[skip] = "" if skip == 0 else "-"
        generated = job("rci", **dict(ci["vals"], levels="\n".join(level_rows)))
        assert generated["stdin"] == "\n".join(lines) + "\n"
        execute([generated], root, grasp_bindir)
    # rmcdhf 的默认模式在 y 后逐块读能级；跳过第一块时仍有两个能级，权重回答保持不变。
    # 跳过 2s 态后 2s 不再占据，只变分本次仍占据的 2p 轨道。
    skipped = job("rmcdhf", levels="1\n1\n1", vary="2p*", nscf="20", save="skip1", log="skip1.log")
    lines = skipped["stdin"].splitlines()
    lines[1] = ""
    generated = job("rmcdhf", **dict(skipped["vals"], levels="\n1\n1"))
    assert generated["stdin"] == "\n".join(lines) + "\n"
    execute([job("rwfnestimate", use_file=True, wfile="base.w"), generated], root, grasp_bindir)
    for name in ("skip1.m", "ci.cm", "mid.cm"):
        assert (root / name).is_file(), (root / name).read_bytes() if (root / name).exists() else name
    return root


@pytest.mark.parametrize("name,numbers", [("skip1.m", [2, 3]), ("ci.cm", [2, 3]), ("mid.cm", [1, 3])])
def test_regenerated_files_have_all_requested_levels(calculated, tmp_path, grasp_bindir, name, numbers):
    blocks = mix.open_mix(calculated / name)
    assert not blocks.partial and [b.nb for b in blocks] == numbers
    assert all(len(b.levels) == 1 for b in blocks)
    assert mix.find_csf_for(str(calculated / name))
    path = calculated / name
    if name.endswith(".m"):
        # rlevels.f90:137 要求物理块号连续；先独立重编号，能量和系数记录原样保留。
        path = tmp_path / "reference.cm"
        independent_subset(calculated / name, path, {(b.twoj1 - 1, b.parity) for b in blocks})
    result = subprocess.run([str(Path(grasp_bindir) / "rlevels"), str(path)],
                            capture_output=True, text=True, timeout=15)
    printed = {}
    for line in result.stdout.splitlines():
        row = re.match(r"^\s*\d+\s+(\d+)\s+(\d+(?:/2)?)\s+([+-])\s+(-?\d+\.\d+)", line)
        if row:
            printed[(int(row[1]), row[2], row[3])] = float(row[4])
    expected = {(lv.pos, b.J, "+" if b.parity > 0 else "-"): lv.energy for b in blocks for lv in b.levels}
    assert printed == pytest.approx(expected, abs=5.1e-8)


def independent_subset(source, output, keep):
    """按公开的小文件记录布局独立重写，不使用被测索引 / 子集函数。"""
    raw, position, records = source.read_bytes(), 0, []
    while position < len(raw):
        size, = struct.unpack_from("<i", raw, position)
        records.append(raw[position + 4:position + 4 + size])
        position += size + 8
    counts = list(struct.unpack("<6i", records[1]))
    selected, position = [], 2
    while position < len(records):
        number, ncf, nev, twoj1, parity = struct.unpack("<5i", records[position])
        size = 4 if nev else 1
        if nev and (twoj1 - 1, parity) in keep:
            selected.append((number, ncf, nev, records[position:position + size]))
        position += size
    counts[1] = sum(row[1] for row in selected)
    counts[3] = sum(row[2] for row in selected)
    counts[4] = sum(row[1] * row[2] for row in selected)
    counts[5] = len(selected)
    csf = source.with_suffix(".c").read_text()
    header, body = csf.split("CSF(s):\n", 1)
    blocks = body.split(" *\n")
    body = " *\n".join(blocks[row[0] - 1] for row in selected)
    core_header, peel = header.split("Peel subshells:\n", 1)
    used = set(re.findall(r"(\d+[a-z]-?)\s*\(\s*[1-9]\d*\s*\)", body))
    orbitals = [name for name in peel.split() if name in used]
    core_orbitals = core_header.split("Core subshells:\n", 1)[1].split()
    counts[2] = len(core_orbitals) + len(orbitals)
    peel = "".join(f" {int(n):2d}{l}{sign or ' '}" for n, l, sign in
                   (re.fullmatch(r"(\d+)([a-z])(-?)", name).groups() for name in orbitals))
    pieces = [record(records[0]), record(struct.pack("<6i", *counts))]
    for number, row in enumerate(selected, 1):
        pieces.append(record(struct.pack("<i", number) + row[3][0][4:]))
        pieces.extend(record(body) for body in row[3][1:])
    output.write_bytes(b"".join(pieces))
    output.with_suffix(".c").write_text(core_header + "Peel subshells:\n" + peel + "\nCSF(s):\n" + body)


@pytest.mark.parametrize("selected", [False, True])
def test_real_biotransform_and_transition_match_independent_subsets(calculated, tmp_path, grasp_bindir, selected):
    for name in ("isodata", "mid.cm", "mid.c", "mid.w"):
        shutil.copyfile(calculated / name, tmp_path / name)
    for suffix in ("cm", "c", "w"):
        shutil.copyfile(tmp_path / ("mid." + suffix), tmp_path / ("other." + suffix))
    workspace = trans.Workspace(str(tmp_path), str(tmp_path / "mid.cm"), str(tmp_path / "other.cm"))
    assert not workspace.problems(), workspace.problems()
    subset = dict(a=[(1, 1)], b=[(3, -1)]) if selected else None
    rid = workspace.prepare(subset)
    execute(workspace.jobs(rid, ["E1"], config.STEPS), Path(workspace.run_dir(rid)), grasp_bindir)
    workspace.finish(rid, ["E1"])
    actual = Path(workspace.run_dir(rid)) / "E1.ct"
    assert trans.parse_ct(str(actual))
    manual = tmp_path / "manual"
    manual.mkdir()
    shutil.copyfile(tmp_path / "isodata", manual / "isodata")
    for role, side in workspace.sides.items():
        keep = set(map(tuple, subset[role])) if subset else {(1, 1), (3, -1)}
        independent_subset(Path(side.mix), manual / (role + ".cm"), keep)
        shutil.copyfile(side.w, manual / (role + ".w"))
    execute([job("rbiotransform", ci=True, init="a", final="b"),
             job("rtransition", ci=True, init="a", final="b", kind="E1", m1_diffj=False, e2_diffj=False)],
            manual, grasp_bindir)
    assert actual.read_bytes() == (manual / "a.b.ct").read_bytes()
