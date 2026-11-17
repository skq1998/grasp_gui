"""真实 GRASP 空块 / 缺块文件的读取、配对和跃迁子集回归。"""

import json
import shutil
from pathlib import Path

import pytest

import grasp_config as config
import grasp_index as index
import grasp_mix as mix
import grasp_service as service
import grasp_summary as summary
import grasp_trans as trans

DATA = Path(__file__).parent / "data/skipped_blocks"
EXPECTED = {
    "skip1.m": [(2, "1/2-", -7.365771698), (3, "3/2-", -7.365769150)],
    "ci.cm": [(2, "1/2-", -7.365771698), (3, "3/2-", -7.365769150)],
    "mid.cm": [(1, "1/2+", -7.408498886), (3, "3/2-", -7.365769150)],
}


@pytest.fixture
def files(tmp_path):
    for source in DATA.iterdir():
        shutil.copy2(source, tmp_path / source.name)
    return tmp_path


@pytest.mark.parametrize("name", EXPECTED)
@pytest.mark.parametrize("summary", [False, True])
def test_reads_all_present_levels_and_matches_csf_by_block_number(files, name, summary):
    path = files / name
    blocks = mix.open_mix(path, summary=summary, workers=1)
    assert not blocks.partial
    assert [(b.nb, b.label) for b in blocks] == [(nb, label) for nb, label, energy in EXPECTED[name]]
    energies = [lv.energy for b in blocks for lv in b.levels]
    assert energies == pytest.approx([row[2] for row in EXPECTED[name]], abs=5e-10)
    assert mix.find_csf_for(str(path)) == str(files / "skip1.c")
    csfs = mix.open_csfs(files / "skip1.c")
    assert all(csfs[b.nb - 1][lv.leading() - 1][0] for b in blocks for lv in b.levels)
    assert config.reference_energies(str(path)) == [round(lv.energy, 10) for b in blocks for lv in b.levels]


@pytest.mark.parametrize("cut", [90, 98, 120, -1])
def test_incomplete_record_or_missing_final_block_is_still_partial(files, cut):
    raw = (files / "mid.cm").read_bytes()
    # 分别在完整块边界和下一条记录内部截断。
    with open(files / "mid.cm", "rb") as stream:
        records = list(index.record_index(stream))
    end_first = records[5]["end"]
    stop = end_first if cut == 90 else end_first + cut - 90 if cut > 0 else len(raw) - 1
    path = files / "partial.cm"
    path.write_bytes(raw[:stop])
    blocks = mix.open_mix(path)
    assert blocks.partial


def test_running_scf_header_with_zero_level_counts_is_partial(files):
    path = files / "running.m"
    raw = bytearray((files / "skip1.m").read_bytes())
    import struct
    struct.pack_into("<2i", raw, 14 + 4 + 12, 0, 0)
    path.write_bytes(raw[:46])
    assert mix.open_mix(path).partial
    result = service.Service("/").levels(str(files), files=[path.name])
    assert result["levels"] == [] and result["partial_files"] == [path.name]


@pytest.mark.parametrize("name", EXPECTED)
def test_cache_accepts_complete_missing_blocks_and_invalidates_old_version(files, name):
    path = files / name
    data = index.mix_index(path)
    assert index.CACHE_VERSION > 1
    assert index.valid_cache(data, "mix", index.stamp(path))
    assert not data["partial"]
    assert index.cached(path, "mix") is not None
    cache = index.cache_dirs(path)[0] / index.cache_name(path, "mix")
    old = json.loads(cache.read_text())
    old["version"] = 1
    cache.write_text(json.dumps(old))
    assert index.cached(path, "mix") is None
    assert not index.mix_index(path)["partial"]


@pytest.mark.parametrize("name", EXPECTED)
@pytest.mark.parametrize("keep", [{(3, -1)}, {(1, 1), (1, -1), (3, -1)}])
def test_written_mix_and_csf_subsets_use_matching_original_block_numbers(files, name, keep):
    path = files / name
    sym = mix.mix_symmetries(path)
    selected = [nb for nb, jp in sym.items() if jp in keep]
    output, csf = files / "subset.cm", files / "subset.c"
    mix.write_mix_subset(path, output, keep)
    mix.write_csf_subset(files / "skip1.c", csf, [nb - 1 for nb in selected])
    blocks = mix.open_mix(output)
    assert not blocks.partial and len(blocks) == len(selected)
    assert [b.nb for b in blocks] == list(range(1, len(selected) + 1))
    assert mix.find_csf_for(str(output)) == str(csf)
    assert [b.label for b in blocks] == [EXPECTED[name][i][1] for i, nb in enumerate(sym) if nb in selected]


@pytest.mark.parametrize("name", EXPECTED)
@pytest.mark.parametrize("selected", [False, True])
def test_workspace_repacks_all_j_and_selected_j_when_blocks_are_missing(files, monkeypatch, name, selected):
    wave = files / "state.w"
    wave.write_bytes(b"not read during preparation")
    monkeypatch.setattr(trans, "find_w_for", lambda *args: (str(wave), [str(wave)]))
    workspace = trans.Workspace(str(files), str(files / name), str(files / name))
    subset = dict(a=[(3, -1)]) if selected else None
    rid = workspace.prepare(subset)
    directory = Path(workspace.run_dir(rid))
    blocks = mix.open_mix(directory / "a.cm")
    assert len(blocks) == (1 if selected else 2)
    assert mix.find_csf_for(str(directory / "a.cm")) == str(directory / "a.c")
    assert [b.nb for b in blocks] == list(range(1, len(blocks) + 1))


def test_summary_keeps_file_partial_metadata(files):
    path = files / "mid.cm"
    with path.open("rb") as stream:
        end = list(index.record_index(stream))[5]["end"]
    path.write_bytes(path.read_bytes()[:end])
    result = summary.collect(files, None, [])
    values = {row["file"]: row for row in result["levels"]}
    assert values["mid.cm"]["partial"] and len(values["mid.cm"]["levels"]) == 1
    assert not values["ci.cm"]["partial"] and len(values["ci.cm"]["levels"]) == 2
