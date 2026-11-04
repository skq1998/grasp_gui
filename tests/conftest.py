"""
测试的公共设置。运行：python3 -m pytest tests  (在仓库根目录)

    不需要 GRASP 的测试总是运行；需要 GRASP 的测试 (标记 grasp) 在找不到 GRASP 程序时跳过：
    用 GRASP_BINDIR=/path/to/grasp/bin 指定程序目录。GRASP_SLOW=1 时也运行需要几分钟以上的算例。
    GRASPG 用 GRASPG_BINDIR 指定目录，GRASPG_NP 指定 MPI 进程数 (默认 2)。
    界面测试 (标记 gui) 在 offscreen 模式下运行，没有 PySide6 时跳过。
"""

import os
import shutil
import sys
import tempfile

# 在导入程序模块之前：用临时的 HOME，测试不读写用户的 ~/.config/grasp_gui.json；界面不需要显示设备
_HOME = tempfile.mkdtemp(prefix="grasp_gui_test_home_")
os.environ["HOME"] = _HOME
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ.setdefault("XKB_LOG_LEVEL", "critical")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pytest                # noqa: E402

import grasp_core as core    # noqa: E402

DEMOS = os.path.join(ROOT, "demos")
GOLDEN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden")


def pytest_configure(config):
    config.addinivalue_line("markers", "grasp: 需要 GRASP2018 程序 (GRASP_BINDIR)")
    config.addinivalue_line("markers", "gui: 需要 PySide6 的界面测试")


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_HOME, ignore_errors=True)


@pytest.fixture(autouse=True)
def lang():
    """每个测试从中文界面开始，结束后恢复。"""
    core.set_lang("zh")
    yield
    core.set_lang("zh")


@pytest.fixture(scope="session")
def grasp_bindir():
    """GRASP 程序目录；没有时跳过测试。"""
    d = os.environ.get("GRASP_BINDIR") or os.path.dirname(shutil.which("rmcdhf") or "")
    if not d or not os.path.isfile(os.path.join(d, "rmcdhf")):
        if os.environ.get("GRASP_REQUIRED"):          # CI：编译出问题时报错，而不是跳过
            pytest.fail(f"GRASP2018 not found in GRASP_BINDIR={d!r}")
        pytest.skip("GRASP2018 not found: set GRASP_BINDIR")
    return d


@pytest.fixture(scope="session")
def graspg_bindir():
    """GRASPG 单独指定目录；没有时跳过，不受 GRASP_REQUIRED 影响。"""
    d = os.environ.get("GRASPG_BINDIR", "")
    if not d or not os.path.isfile(os.path.join(d, "rcsfggenerate_csfg")):
        pytest.skip("GRASPG not found: set GRASPG_BINDIR")
    return d


@pytest.fixture(scope="session")
def qapp():
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield app
