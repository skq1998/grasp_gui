"""NIST 数据解析、匹配、缓存和网络边界；测试不连接外部主机。"""

from concurrent.futures import ThreadPoolExecutor
from email.message import Message
from io import BytesIO
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse
from urllib.request import HTTPSHandler
from urllib.response import addinfourl

import pytest

import grasp_nist as nist

DATA = Path(__file__).with_name("data") / "nist"


@pytest.mark.parametrize("value,expected", [
    ("fe xv", "Fe XV"), ("26 14+", "Fe XV"), ("26 15", "Fe XV"), ("Li", "Li I")])
def test_spectra_and_fixed_endpoint(value, expected):
    assert nist.normalize_spectrum(value) == expected
    url = urlparse(nist.query_url(value))
    assert (url.scheme, url.netloc, url.path) == ("https", "physics.nist.gov", "/cgi-bin/ASD/energy1.pl")
    assert parse_qs(url.query)["spectrum"] == [expected]


@pytest.mark.parametrize("value", ["http://example.com", "Li I &format=0", "Li IV", "Fe IIII", "E I", "500 I"])
def test_invalid_spectrum_does_not_create_url(value):
    with pytest.raises(ValueError):
        nist.query_url(value)


def test_fetch_uses_https_proxy_and_rejects_redirect(monkeypatch):
    url = nist.query_url("Li I")
    raw = (DATA / "li-i.csv").read_bytes()
    requests = []
    monkeypatch.setenv("https_proxy", "http://proxy.invalid:8080")
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.delenv("NO_PROXY", raising=False)

    def https_open(_self, request):
        assert request.full_url == url and request.host == "proxy.invalid:8080"
        assert request._tunnel_host == "physics.nist.gov"
        assert _self._context.check_hostname
        requests.append(request.full_url)
        headers = Message()
        if len(requests) == 1:
            headers["Location"] = "https://redirect.invalid/levels"
            response = addinfourl(BytesIO(), headers, url, 302)
        else:
            response = addinfourl(BytesIO(raw), headers, url, 200)
        response.msg = "Fake NIST response"
        return response

    monkeypatch.setattr(HTTPSHandler, "https_open", https_open)
    with pytest.raises(HTTPError) as error:
        nist.fetch("Li I")
    assert error.value.code == 302
    error.value.close()
    # 失败后应能重新发起请求。
    assert nist.fetch("Li I") == raw
    assert requests == [url, url]


def test_fetch_allows_only_one_request_at_a_time(monkeypatch):
    raw = (DATA / "li-i.csv").read_bytes()
    started, release = Event(), Event()
    requests = []

    def https_open(_self, request):
        requests.append(request.full_url)
        assert request.full_url == nist.query_url("Li I")
        started.set()
        assert release.wait(5)
        response = addinfourl(BytesIO(raw), Message(), request.full_url, 200)
        response.msg = "Fake NIST response"
        return response

    monkeypatch.setattr(HTTPSHandler, "https_open", https_open)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(nist.fetch, "Li I")
        try:
            assert started.wait(5)
            with pytest.raises(ValueError, match="已有一次 NIST 请求在进行"):
                nist.fetch("Fe XV")
        finally:
            release.set()
        assert first.result(timeout=5) == raw
    assert requests == [nist.query_url("Li I")]


def test_read_official_csv_and_uncertain_tsv():
    _, li = nist.read_data(DATA / "li-i.csv")
    assert [(row.twoj, row.parity) for row in li[:3]] == [(1, 1), (1, -1), (3, -1)]
    assert li[1].energy == 14903.66 and li[1].configuration == "1s2.2p"
    _, rows = nist.read_data(DATA / "uncertain.tsv")
    assert rows[1].reported == "(239660)?" and rows[1].energy == 239660
    assert rows[2].j == "3/2" and rows[2].energy == 125828.1
    assert not rows[2].term and not rows[2].configuration and rows[2].parity is None
    assert rows[3].energy is None
    with pytest.raises(ValueError):
        nist.parse(b"J,Level (eV)\n0,3\n")


def test_cache_import_and_corruption(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    raw, rows = nist.read_data(DATA / "li-i.csv")
    metadata, cached = nist.save_cache("Li I", raw, imported=True)
    assert cached == rows and metadata["imported"]
    assert nist.load_cache("Li I") == (metadata, rows)
    path = tmp_path / "grasp_gui/nist/Li-I.json"
    path.write_text("corrupt")
    assert nist.load_cache("Li I") is None and not path.exists()


def test_match_configuration_term_ambiguous_manual_and_reference():
    _, li = nist.read_data(DATA / "li-i.csv")
    ground = dict(id="ground", energy=-7.5, twoj=1, parity=1, full=[("1s", 2), ("2s", 1)], term="2S")
    upper = dict(id="upper", energy=-7.5 + 15000 / nist.HARTREE_CM, twoj=1, parity=-1,
                 full=[("1s", 2), ("2p", 1)], term="2P")
    vague = dict(id="vague", energy=upper["energy"], twoj=1, parity=-1)
    no_match = dict(id="not_found", energy=upper["energy"], twoj=25, parity=-1)
    rows = nist.compare([ground, upper, vague, no_match], li)
    assert [row["status"] for row in rows] == ["automatic", "automatic", "ambiguous", "unmatched"]
    assert rows[1]["delta_cm"] == pytest.approx(96.34)
    assert rows[1]["relative_error"] == pytest.approx(96.34 / 14903.66)
    manual = nist.compare([ground, upper, vague], li, manual={"vague": li[1].key}, reference="upper")
    assert manual[1]["status"] == "ambiguous"
    assert manual[2]["status"] == "manual" and manual[2]["experimental"] == li[1]
    assert manual[2]["calculated_cm"] == 0
    duplicate = nist.compare([upper, dict(upper, id="other")], li)
    assert [row["status"] for row in duplicate] == ["ambiguous", "ambiguous"]


def test_nist_omitted_filled_inner_shells_match():
    _, fe = nist.read_data(DATA / "uncertain.tsv")
    calc = dict(id="ground", energy=-300, twoj=0, parity=1,
                full=[("1s", 2), ("2s", 2), ("2p", 6), ("3s", 2)], term="1S")
    match = nist.compare([calc], fe)[0]
    assert match["experimental"] == fe[0] and match["status"] == "automatic"


@pytest.mark.parametrize("changes", [{"energy": -7.44}, {"lsj_label": "3p_2P"}, {}])
def test_saved_match_needs_review_after_calculation_or_nist_changes(tmp_path, changes):
    _, li = nist.read_data(DATA / "li-i.csv")
    experimental = li[1]
    row = dict(pos=1, twoj=experimental.twoj, parity=experimental.parity, energy=-7.45, lsj_label="2p_2P")
    row["id"] = nist.calculated_id("results/state.cm", row)
    assert nist.load_matches(tmp_path) == {}
    assert not (tmp_path / ".grasp_gui").exists()
    nist.save_match(tmp_path, "Li I", row, experimental.key)
    pairs = nist.load_matches(tmp_path)["Li I"]
    assert nist.compare([row], li, manual=nist.match_overrides([row], pairs))[0]["status"] == "manual"
    updated = dict(row, **changes)
    assert nist.calculated_id("results/state.cm", updated) == row["id"]
    levels = li if changes else []
    pending = nist.compare([updated], levels, manual=nist.match_overrides([updated], pairs))[0]
    assert pending["status"] == "ambiguous" and pending["experimental"] is None
    assert nist.load_matches(tmp_path)["Li I"] == pairs


def test_saved_matches_merge_independent_edits_and_keep_corrupt_file(tmp_path):
    upper = dict(id="upper", energy=-7.45, jj="2p-(1)")
    lower = dict(id="lower", energy=-7.5, jj="2s(1)")
    with ThreadPoolExecutor(2) as pool:
        jobs = [pool.submit(nist.save_match, tmp_path, "Li I", upper, "first"),
                pool.submit(nist.save_match, tmp_path, "Fe XV", lower, "second")]
        for job in jobs:
            job.result(timeout=5)
    spectra = nist.load_matches(tmp_path)
    assert spectra["Li I"]["upper"]["component"] == "2p-(1)"
    assert spectra["Fe XV"]["lower"]["experimental"] == "second"
    with pytest.raises(ValueError, match="已被手动配对"):
        nist.save_match(tmp_path, "Li I", lower, "first")
    assert nist.save_match(tmp_path, "Fe XV", lower, None) == {}
    assert nist.load_matches(tmp_path) == {"Li I": spectra["Li I"]}
    path = tmp_path / ".grasp_gui/nist-matches.json"
    raw = b'{"version":1,"spectra":{"Li I":{"upper":{}}}}'
    path.write_bytes(raw)
    with pytest.raises(ValueError, match="已保留原件"):
        nist.save_match(tmp_path, "Li I", upper, "first")
    assert path.read_bytes() == raw


def test_non_gui_entry_points_do_not_load_nist_modules():
    import subprocess
    import sys

    code = """
import sys
class Guard:
    def find_spec(self, name, *args):
        if name in ("grasp_nist", "nist_panel"):
            raise AssertionError(name)
sys.meta_path.insert(0, Guard())
import grasp_config
import grasp_remote
import grasp_mcp
assert not any("nist" in name.casefold() for name in grasp_mcp.TOOLS)
assert grasp_remote.Operations().hello(1)["protocol"] == 1
assert grasp_config.cli(["steps"]) == 0
"""
    run = subprocess.run([sys.executable, "-c", code], text=True, capture_output=True, timeout=10)
    assert run.returncode == 0, run.stderr


@pytest.mark.gui
def test_spectrum_inference_counts_core_and_active_csf(qapp, tmp_path, monkeypatch):
    from level_panel import LevelPanel

    panel = LevelPanel(str(tmp_path))
    mixing = str(tmp_path / "even.cm")
    csf = tmp_path / "even.c"
    csf.write_text("Core subshells:\n  1s  2s  2p-  2p\nPeel subshells:\n  3s\nCSF(s):\n")
    level = SimpleNamespace(_leading_csf=("3s(2)", ""))
    lsj = (None, [(1.0, 100.0, "3s_2S")])
    panel.levels = {("even", 1): ("even.cm", mixing, level, object(), lsj, str(csf), "")}
    panel.summary_source = lambda _: ([dict(step="rnucleus", vals=dict(z=26))],)
    monkeypatch.setattr(panel, "checked_paths", lambda: [mixing])
    assert panel.nist_ion(str(tmp_path)) == "Fe XV"
    monkeypatch.setattr(panel, "checked_paths", lambda: [mixing, str(tmp_path / "odd.cm")])
    assert panel.nist_ion(str(tmp_path)) == ""
    panel.shutdown()


@pytest.mark.gui
def test_nist_dialog_only_fetches_on_click_and_exports_manual_pair(qapp, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    from nist_panel import NistDialog
    from test_gui import spin

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    raw = (DATA / "li-i.csv").read_bytes()
    downloads = []
    monkeypatch.setattr(nist, "fetch", lambda ion: downloads.append(ion) or raw)
    calc = dict(id="upper", file="state.cm", J="1/2", pos=1, energy=-7.45, twoj=1, parity=-1,
                jj="2p-(1)", lsj_label="2p_2P")
    dialog = NistDialog(None, [calc], "Li I", tmp_path)
    assert not downloads and not dialog.rows
    dialog.fetch_button.click()
    spin(qapp, lambda: bool(dialog.rows))
    assert downloads == ["Li I"] and dialog.rows[0]["status"] == "ambiguous"
    assert len(dialog.diagram.columns) == 2 and len(dialog.diagram.columns[1]["levels"]) > 1
    assert dialog.table.item(0, 3).text() == "2p-(1)"
    assert dialog.table.item(0, 4).text() == "2p_2P"
    dialog.choices.setCurrentIndex(1)
    dialog.apply_pair.click()
    assert dialog.rows[0]["status"] == "manual"
    saved = nist.load_matches(tmp_path)["Li I"]["upper"]
    assert saved["experimental"] == dialog.rows[0]["experimental"].key
    # 单个计算态归零后，实验配对仍应出现在同一张图上。
    low, high = dialog.diagram.yr
    assert low < 0 < dialog.rows[0]["experimental_cm"] < high
    assert high < max(level["y"] for level in dialog.diagram.columns[1]["levels"])
    target = tmp_path / "compare.csv"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a: (str(target), "CSV"))
    dialog.export_button.click()
    assert target.exists() and "14903.66" in target.read_text(encoding="utf-8-sig")
    dialog.fetch_button.click()
    assert downloads == ["Li I"]
    dialog.refresh.setChecked(True)
    dialog.fetch_button.click()
    spin(qapp, lambda: dialog.fetch_button.isEnabled())
    assert downloads == ["Li I", "Li I"]
    dialog.close()
    updated = dict(calc, energy=-7.44)
    reopened = NistDialog(None, [updated], "Li I", tmp_path)
    assert reopened.rows[0]["status"] == "ambiguous"
    assert reopened.choices.currentData() == saved["experimental"]
    reopened.apply_pair.click()
    assert reopened.rows[0]["status"] == "manual"
    reopened.choices.setCurrentIndex(0)
    reopened.apply_pair.click()
    assert reopened.rows[0]["status"] == "ambiguous" and nist.load_matches(tmp_path) == {}
    reopened.close()


@pytest.mark.gui
def test_nist_import_can_run_without_network(qapp, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    from nist_panel import NistDialog

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.setattr(nist, "fetch", lambda _: pytest.fail("offline import must not fetch"))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a: (str(DATA / "uncertain.tsv"), "TSV"))
    calc = dict(id="ground", file="state.cm", J="0", pos=1, energy=-300, twoj=0, parity=1)
    dialog = NistDialog(None, [calc], "Fe XV", tmp_path)
    dialog.import_button.click()
    assert dialog.current[0]["imported"] and nist.load_cache("Fe XV")
    assert dialog.rows[0]["experimental_cm"] == 0
    dialog.close()


@pytest.mark.gui
def test_failed_nist_request_reenables_import(qapp, tmp_path, monkeypatch):
    import grasp_core as core
    from nist_panel import NistDialog
    from test_gui import spin

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))

    def unavailable(ion):
        raise OSError("network unavailable")

    monkeypatch.setattr(nist, "fetch", unavailable)
    dialog = NistDialog(None, [], "Fe XV", tmp_path)
    dialog.fetch_button.click()
    spin(qapp, lambda: dialog.import_button.isEnabled())
    assert core.T("无法从 NIST 获取数据：{e}。请检查代理设置或改用导入。",
                  e="network unavailable") in dialog.info.text()
    assert nist.load_cache("Fe XV") is None
    dialog.close()
