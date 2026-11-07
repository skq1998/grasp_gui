"""用真实的 GRASP2018 运行算例：用 `grasp_gui script` 生成的脚本从头计算，能级能量与配置中的参考能量一致。
需要 GRASP_BINDIR；默认只跑几秒钟的算例，GRASP_SLOW=1 时跑全部算例 (case1 单进程约 50 分钟)。
GRASPG 用 GRASPG_BINDIR，GRASPG_NP 指定 MPI 进程数 (默认 2)，慢算例也需 GRASP_SLOW=1。"""

import os
import subprocess

import pytest

import grasp_config as config
import grasp_core as core
import grasp_mix
from conftest import DEMOS

pytestmark = pytest.mark.grasp
FAST = ["example1", "example2", "example8", "example9", "manual_tools"]
SLOW = ["case4", "example3", "example4", "example5", "case1", "case2", "case3", "manual_zf", "manual_asfsplit", "manual_accumulate"]
TOLERANCE = 1e-7          # Hartree：不同编译器 / BLAS 的舍入差别


def test_manual_csfs(grasp_bindir, tmp_path):
    """只有 CSF 的教学例按手册的块大小核对，不能用能量作参考。"""
    cfg, _ = config.load(os.path.join(DEMOS, "manual_csfs.json"))
    script = core.shell_script(config.session(cfg)["pipeline"], grasp_bindir, ".")
    r = subprocess.run(["sh", "-c", script], cwd=tmp_path, capture_output=True, text=True,
                       env=core.child_env(), timeout=300)
    assert r.returncode == 0, r.stderr[-3000:]
    expected = {"51": [361], "52": [1912, 5210, 7122], "53": [10743, 29589, 41500],
                "54": [14351, 38928, 53645, 56147, 48973, 36562],
                "55": [7765, 24492, 33925, 29299, 17134, 7542],
                "56": [21399, 59512, 85284], "57": [2408, 4174], "58": [1463, 1454, 2478]}
    for section, counts in expected.items():
        assert [len(b) for b in grasp_mix.read_csfs(tmp_path / f"section{section}.c")] == counts


@pytest.mark.parametrize("name", FAST + SLOW)
def test_demo_reproduces_reference(name, grasp_bindir, tmp_path):
    if name in SLOW and not os.environ.get("GRASP_SLOW"):
        pytest.skip("slow demo: set GRASP_SLOW=1")
    run_demo(name, grasp_bindir, tmp_path)


@pytest.mark.parametrize("name", ["graspg1", "graspg_zf"])
def test_graspg_reproduces_reference(name, graspg_bindir, tmp_path):
    if name == "graspg1" and not os.environ.get("GRASP_SLOW"):
        pytest.skip("slow demo: set GRASP_SLOW=1")
    np = int(os.environ.get("GRASPG_NP", "2"))
    assert np > 0, "GRASPG_NP must be a positive integer"
    # 同一脚本内角向、SCF、CI 使用相同进程数和独立的 MPI 临时目录。
    run_demo(name, graspg_bindir, tmp_path, mpi_prefix=f"mpirun -np {np}")


def run_demo(name, bindir, tmp_path, mpi_prefix=""):
    """从配置在空目录重算；不能只凭程序退出码判断成功。"""
    cfg, _ = config.load(os.path.join(DEMOS, name + ".json"))
    wd = tmp_path / name
    config.install(cfg, str(wd))
    script = core.shell_script(config.session(cfg)["pipeline"], bindir, ".", mpi_prefix=mpi_prefix)
    (wd / "run.sh").write_text(script)
    r = subprocess.run(["sh", "run.sh"], cwd=wd, capture_output=True, text=True,
                       env=core.child_env(), timeout=6 * 3600)
    assert r.returncode == 0, r.stderr[-3000:]
    assert cfg["reference"], f"{name}: no reference energies"
    outputs = {"manual_tools": ["extracted.c", "accumulated.c", "2p_3.rmx", "hfs.tex"],
               "manual_asfsplit": ["2s22p3_2p5_3_odd1.cm", "2s22p3_2p5_3_odd2.cm", "2s22p3_2p5_3_odd3.cm"],
               "manual_accumulate": ["zero_order_0.9999.c", "zerofirst_0.9999.cm"]}
    for filename in outputs.get(name, []):
        assert (wd / filename).stat().st_size > 0
    for ref in cfg["reference"]:
        path = wd / ref["file"]
        assert path.exists(), f"{ref['file']} not produced; see {wd}"
        got = config.reference_energies(str(path))
        assert len(got) == len(ref["energies"])
        assert max(abs(a - b) for a, b in zip(got, ref["energies"])) < TOLERANCE, (got, ref["energies"])


@pytest.mark.parametrize("name,state,backup", [
    ("example2_coupling", "2s2p_DF", ".lsj.lbl_LSJ"),
    ("example3_coupling", "2s22p3_2p5_3", ".lsj.lbl_SAVE"),
])
def test_coupling_reproduces_reference(name, state, backup, coupling_bindir, grasp_bindir, tmp_path, monkeypatch):
    """COUPLING_BINDIR 或 PATH 指定外部程序，不在 CI 中额外编译。"""
    monkeypatch.setenv("PATH", coupling_bindir + os.pathsep + os.environ.get("PATH", ""))
    test_demo_reproduces_reference(name, grasp_bindir, tmp_path)
    wd = tmp_path / name
    for scheme in ("LK3", "JK3"):
        assert (wd / f"{state}.coup3.{scheme}.lbl").stat().st_size > 0
    assert (wd / (state + backup)).is_file()


def test_hfszeeman_matches_hand_input(grasp_bindir, tmp_path):
    """同一批 Li I 轨道下比较四个默认分支；另存结果后用 rhfs 核对常数。"""
    import shutil

    cfg, _ = config.load(os.path.join(DEMOS, "example1.json"))
    source = tmp_path / "source"
    config.install(cfg, str(source))
    script = core.shell_script(config.session(cfg)["pipeline"], grasp_bindir, ".")
    result = subprocess.run(["sh", "-c", script], cwd=source, capture_output=True, text=True,
                            env=core.child_env(), timeout=300)
    assert result.returncode == 0, result.stderr[-3000:]
    step = core.RHfsZeeman95()
    for ci in (False, True):
        suffix = ".ch" if ci else ".h"
        for offdiag in (False, True):
            outputs = []
            for mode in ("form", "hand"):
                wd = tmp_path / f"{mode}-{ci}-{offdiag}"
                wd.mkdir()
                for name in ("isodata", "2p_3.c", "2p_3.w", "2p_3.cm" if ci else "2p_3.m"):
                    shutil.copy2(source / name, wd / name)
                if mode == "form":
                    vals = dict(step.defaults(), state="2p_3", ci=ci, offdiag=offdiag, saveas="constants.txt")
                    script = core.shell_script([step.job(vals)], grasp_bindir, ".")
                    result = subprocess.run(["sh", "-c", script], cwd=wd, capture_output=True, text=True,
                                            env=core.child_env(), timeout=120)
                else:
                    stdin = f"y\n2p_3\n{'y' if ci else 'n'}\n{'y' if offdiag else 'n'}\n"
                    result = subprocess.run([os.path.join(grasp_bindir, "rhfszeeman95")], cwd=wd, input=stdin,
                                            capture_output=True, text=True, env=core.child_env(), timeout=120)
                assert result.returncode == 0, result.stderr[-3000:]
                constants = (wd / ("2p_3" + suffix)).read_bytes()
                assert b"Interaction constants:" in constants
                matrix = wd / ("2p_3" + (".cgjhfs" if ci else ".gjhfs"))
                assert matrix.exists() == offdiag
                outputs.append((constants, matrix.read_bytes() if offdiag else None))
                if mode == "form":
                    assert (wd / "constants.txt").read_bytes() == constants
                    script = core.shell_script([core.RHfs().job(dict(core.RHfs.defaults(), state="2p_3", ci=ci))],
                                               grasp_bindir, ".")
                    result = subprocess.run(["sh", "-c", script], cwd=wd, capture_output=True, text=True,
                                            env=core.child_env(), timeout=120)
                    assert result.returncode == 0, result.stderr[-3000:]
                    rhfs = (wd / ("2p_3" + suffix)).read_bytes()
                    assert rhfs != constants
                    assert rhfs.split(b"Interaction constants:")[1] == constants.split(b"Interaction constants:")[1]
                    assert (wd / "constants.txt").read_bytes() == constants
            assert outputs[0] == outputs[1]
