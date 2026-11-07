"""物理核数、MPI 可用性及首次默认值的规则。"""

import shlex
import subprocess
from unittest.mock import mock_open

import pytest

import grasp_core as core


def cpuinfo(packages, cores, threads):
    rows = []
    for package in range(packages):
        for physical in range(cores):
            for _ in range(threads):
                rows.append(f"processor : {len(rows)}\nphysical id : {package}\ncore id : {physical}\n")
    return "\n".join(rows)


@pytest.mark.parametrize("packages,cores,threads", [(1, 2, 1), (1, 2, 2), (2, 3, 2), (2, 3, 1)])
def test_linux_physical_cores(monkeypatch, packages, cores, threads):
    monkeypatch.setattr(core.sys, "platform", "linux")
    data = cpuinfo(packages, cores, threads)
    monkeypatch.setattr("builtins.open", mock_open(read_data=data))
    assert core.physical_cpu_count() == packages * cores


@pytest.mark.parametrize("data", ["", "processor: 0\n", "processor: 0\nphysical id: x\ncore id: 1\n",
                                 cpuinfo(1, 2, 1) + "\nprocessor: 2\n"])
def test_linux_incomplete_topology_falls_back(monkeypatch, data):
    monkeypatch.setattr(core.sys, "platform", "linux")
    monkeypatch.setattr(core.os, "cpu_count", lambda: 8)
    monkeypatch.setattr("builtins.open", mock_open(read_data=data))
    assert core.physical_cpu_count() == 4


@pytest.mark.parametrize("logical,expected", [(None, 1), (0, 1), (1, 1), (2, 1), (3, 1), (8, 4)])
def test_unreadable_topology_falls_back(monkeypatch, logical, expected):
    monkeypatch.setattr(core.sys, "platform", "linux")
    monkeypatch.setattr(core.os, "cpu_count", lambda: logical)

    def unavailable(*args, **kwargs):
        raise OSError("unavailable")

    monkeypatch.setattr("builtins.open", unavailable)
    assert core.physical_cpu_count() == expected


def test_macos_physical_cores(monkeypatch):
    monkeypatch.setattr(core.sys, "platform", "darwin")
    calls = []

    def sysctl(command, **options):
        calls.append((command, options))
        return "6\n"

    monkeypatch.setattr(core.subprocess, "check_output", sysctl)
    assert core.physical_cpu_count() == 6
    assert calls[0][0] == ["sysctl", "-n", "hw.physicalcpu"]
    assert calls[0][1]["timeout"] == 2


@pytest.mark.parametrize("result", ["0", "unknown", OSError("missing"), subprocess.TimeoutExpired("sysctl", 2)])
def test_macos_failure_falls_back(monkeypatch, result):
    monkeypatch.setattr(core.sys, "platform", "darwin")
    monkeypatch.setattr(core.os, "cpu_count", lambda: 10)

    def sysctl(*args, **kwargs):
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(core.subprocess, "check_output", sysctl)
    assert core.physical_cpu_count() == 5


@pytest.fixture
def mpi_bin(tmp_path, monkeypatch):
    bindir = tmp_path / "mpi tools"
    bindir.mkdir()
    for name in ["mpirun", "custom-launcher", "rangular_mpi", "rmcdhf_mpi", "rci_mpi",
                 "rbiotransform_mpi", "rtransition_mpi"]:
        path = bindir / name
        path.write_text("#!/bin/sh\nexit 99\n")
        path.chmod(0o755)
    monkeypatch.setenv("PATH", str(bindir))
    monkeypatch.setattr(core, "physical_cpu_count", lambda: 6)
    return bindir


@pytest.mark.parametrize("cores,expected", [(1, 1), (2, 2), (3, 3), (16, 4)])
def test_mpi_default_uses_physical_cores(mpi_bin, monkeypatch, cores, expected):
    monkeypatch.setattr(core, "physical_cpu_count", lambda: cores)
    launcher = shlex.quote(str(mpi_bin / "custom-launcher")) + " --bind-to core"
    assert core.default_mpi_np(str(mpi_bin), launcher) == expected


@pytest.mark.parametrize("launcher", ["not-installed", "'unfinished", "   "])
def test_missing_or_invalid_launcher_is_serial(mpi_bin, launcher):
    assert core.default_mpi_np(str(mpi_bin), launcher) == 1


def test_missing_mpi_program_is_serial(mpi_bin):
    (mpi_bin / "rci_mpi").unlink()
    assert core.default_mpi_np(str(mpi_bin), "mpirun") == 1


def test_program_lookup_retains_path_fallback(mpi_bin, tmp_path):
    assert core.default_mpi_np(str(tmp_path), "mpirun") == 4
