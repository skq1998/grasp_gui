"""真实 example1：DC / Breit+QED 的偶奇宇称共用各自零点，与 csum 本征值表核对。"""

import copy
from pathlib import Path
import re
import shutil
import subprocess

import pytest

import grasp_config as config
import grasp_core as core
import grasp_mix as mix

pytestmark = pytest.mark.grasp


def test_example1_hamiltonians_and_parity_splittings(tmp_path, grasp_bindir):
    cfg, _ = config.load(Path(__file__).parents[1] / "demos/example1.json")
    def execute(jobs):
        script = core.shell_script(jobs, grasp_bindir, str(tmp_path))
        result = subprocess.run(["sh", "-c", script], cwd=tmp_path, env=core.child_env(),
                                capture_output=True, timeout=180)
        assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]
    execute(config.session(dict(cfg, jobs=cfg["jobs"][:16]))["pipeline"])
    def save(prefix):
        for state in ("2s_3", "2p_3"):
            for suffix in (".cm", ".csum"):
                shutil.copyfile(tmp_path / (state + suffix), tmp_path / (prefix + state + suffix))
    save("qed_")
    jobs = []
    for original in cfg["jobs"][:16]:
        if original.get("step") == "rci":
            job = copy.deepcopy(original)
            job["vals"].update(trans=False, vp=False, se=False, nms=False, sms=False)
            jobs.append(config.make_job(job))
    execute(jobs)
    save("dc_")
    rows = []
    for path in sorted(tmp_path.glob("*_2?_3.cm")):
        group = mix.hamiltonian(path)["zero_group"]
        assert group != "unknown"
        # matrix.f90:233-246：SE 关闭时额外打印估算，不写进 cm；取每个 J/宇称首次本征值表。
        first = {}
        for section in path.with_suffix(".csum").read_text().split("Eigenenergies:")[1:]:
            started = False
            for line in section.splitlines():
                found = re.match(r"^\s*(\d+)\s+(\d+(?:/\d+)?)\s+([+-])\s+([+-]?\d\.\d+D[+-]\d+)\s", line)
                if found:
                    started = True
                    first.setdefault(tuple(found.group(i) for i in (1, 2, 3)), float(found[4].replace("D", "E")))
                elif started:
                    break
        energies = [lv.energy for b in mix.read_mix(path) for lv in b.levels]
        assert energies == pytest.approx(list(first.values()), abs=1e-11)
        rows.extend((path.name, group, energy, printed) for energy, printed in zip(energies, first.values()))
    zeros = mix.energy_zeros((group, energy) for _, group, energy, _ in rows)
    printed_zeros = mix.energy_zeros((group, printed) for _, group, _, printed in rows)
    assert set(zeros) == {"DC", "DC+Breit(f=0.000001)+VP+SE"}
    for name, group, energy, printed in rows:
        assert (energy - zeros[group]) * mix.HARTREE_CM == pytest.approx(
            (printed - printed_zeros[group]) * mix.HARTREE_CM, abs=1e-5)
    for prefix in ("dc_", "qed_"):
        even = min(e for name, _, e, _ in rows if name == prefix + "2s_3.cm")
        odd = min(e for name, _, e, _ in rows if name == prefix + "2p_3.cm")
        group = next(g for name, g, _, _ in rows if name == prefix + "2p_3.cm")
        assert zeros[group] == even and odd - even > 0
