"""以旧读取器为参照，验证按需读取、续记录、摘要顺序和缓存边界。"""

import importlib.util
import json
import multiprocessing
import os
from pathlib import Path
import struct
import threading
import time

import pytest

import grasp_index as index
import grasp_mix as mix

spec = importlib.util.spec_from_file_location("old_mix_reader", Path(__file__).with_name("old_mix_reader.py"))
old = importlib.util.module_from_spec(spec)
spec.loader.exec_module(old)


def record(body, split=0):
    pieces = [body[i:i + split] for i in range(0, len(body), split)] if split else [body]
    return b"".join(struct.pack("<i", -len(p) if i + 1 < len(pieces) else len(p)) + p +
                    struct.pack("<i", -len(p) if i else len(p)) for i, p in enumerate(pieces))


def make_files(directory, ncf=1100, nev=3, split=0):
    directory.mkdir(exist_ok=True)
    path = directory / "state.cm"
    values = [(-1 if i % 2 else 1) * (1 + i % 537) / 1000 for i in range(ncf)]
    data = [record(b"G92MIX"), record(struct.pack("<6i", 3, ncf, 2, nev, ncf * nev, 1)),
            record(struct.pack("<5i", 1, ncf, nev, 2, -1))]
    if nev:
        data += [record(struct.pack(f"<{nev}i", *range(1, nev + 1))),
                 record(struct.pack(f"<{nev + 1}d", -7.4, *[i / 1000 for i in range(nev)])),
                 record(struct.pack(f"<{ncf * nev}d", *(values * nev)), split)]
    path.write_bytes(b"".join(data))
    csf = path.with_suffix(".c")
    with csf.open("w") as f:
        f.write("Core subshells:\n 1s\nPeel subshells:\n 2p-\nCSF(s):\n")
        for i in range(ncf):
            f.write(f" 2p-( 1)  {i}\n\n 1/2-\n")
    return path


def compare(path):
    a, b, lazy = old.read_mix(path), mix.read_mix(path), mix.open_mix(path, summary=True, workers=1)
    assert old._mix_signature(path) == mix._mix_signature(path)
    assert old._csf_signature(path.with_suffix(".c")) == mix._csf_signature(path.with_suffix(".c"))
    assert old.read_csfs(path.with_suffix(".c")) == mix.read_csfs(path.with_suffix(".c"))
    for ba, bb, bc in zip(a, b, lazy):
        assert (ba.nb, ba.ncf, ba.twoj1, ba.parity) == (bb.nb, bb.ncf, bb.twoj1, bb.parity)
        for va, vb, vc in zip(ba.levels, bb.levels, bc.levels):
            assert va.energy == vb.energy == vc.energy
            assert va.coeffs == vb.coeffs
            assert list(va.coeffs) == list(vc.coeffs)
            assert va.leading() == vc.leading()
            for minimum in (0.0, 0.001, 0.08, 0.2, 0.288369):
                assert va.components(minimum) == vc.components(minimum)
                assert va.components(minimum)[:500] == vc.top_components(500, minimum)
    for keep in ({(1, -1)}, set()):
        p, q = path.parent / "old.cm", path.parent / "new.cm"
        old.write_mix_subset(path, p, keep)
        mix.write_mix_subset(path, q, keep)
        assert p.read_bytes() == q.read_bytes()
    p, q = path.parent / "old.c", path.parent / "new.c"
    old.write_csf_subset(path.with_suffix(".c"), p, {0})
    mix.write_csf_subset(path.with_suffix(".c"), q, {0})
    assert p.read_bytes() == q.read_bytes()


@pytest.mark.parametrize("ncf,nev", [(3, 2), (499, 3), (500, 1), (501, 2), (1100, 3), (0, 0)])
def test_old_and_new(tmp_path, ncf, nev):
    compare(make_files(tmp_path, ncf, nev))


def test_threshold_and_ties(tmp_path):
    path = make_files(tmp_path, 1100, 1)
    lv = mix.open_mix(path, summary=True, workers=1)[0].levels[0]
    expected = old.read_mix(path)[0].levels[0]
    boundary = expected.components()[499][1] ** 2
    for threshold in (boundary, boundary - 1e-12, boundary + 1e-12):
        assert lv.top_components(500, threshold) == expected.components(threshold)[:500]
    top = lv.top_components(500)
    for (i, c), (j, d) in zip(top, top[1:]):
        if c * c == d * d:
            assert i < j


@pytest.mark.parametrize("split", [7, 15, 104])
def test_continued_records(tmp_path, split):
    path = make_files(tmp_path, 31, 2, split)
    lv = mix.open_mix(path, summary=True, workers=1)[0].levels[0]
    assert len(lv.coeffs) == 31
    assert list(lv.coeffs) == [(-1 if i % 2 else 1) * (1 + i % 537) / 1000 for i in range(31)]
    assert lv.coeffs[-1] == list(lv.coeffs)[-1]
    assert list(lv.coeffs[2:6]) == list(lv.coeffs)[2:6]
    out = tmp_path / "subset.cm"
    mix.write_mix_subset(path, out, {(1, -1)})
    assert out.read_bytes() == path.read_bytes()


@pytest.mark.parametrize("workers", [1, 2, 4])
def test_parallel_matches_serial(tmp_path, workers, monkeypatch):
    monkeypatch.setattr(index, "PARALLEL_MIN_COEFFICIENTS", 0)
    path = make_files(tmp_path, 20000, 4)
    got = mix.open_mix(path, summary=True, workers=workers)
    expected = old.read_mix(path)
    for a, b in zip(got[0].levels, expected[0].levels):
        assert a.leading() == b.leading()
        assert a.top_components(500) == b.components()[:500]


def test_cache_rebuild_and_changed_source(tmp_path, monkeypatch):
    path = make_files(tmp_path)
    mix.open_mix(path, summary=True, workers=1)
    cache = index.cache_dirs(path)[0] / index.cache_name(path, "mix")
    original = cache.read_bytes()
    for bad in (b"{broken", b"{}", original.replace(b'"blocks":[', b'"blocks":[{},')):
        cache.write_bytes(bad)
        assert len(mix.open_mix(path, summary=True, workers=1)[0].levels) == 3
    block = mix.open_csfs(path.with_suffix(".c"))[0]
    saved = block.saved
    with path.with_suffix(".c").open("a") as f:
        f.write("\n")
    with pytest.raises(ValueError, match="changed"):
        index.unchanged(saved)
    assert len(mix.open_csfs(path.with_suffix(".c"))[0]) == 1100
    path.unlink()
    index.clean_cache(tmp_path)
    assert not cache.exists()


def test_user_cache_and_memory_fallback(tmp_path, monkeypatch):
    path = make_files(tmp_path)
    local, fallback = tmp_path / "readonly", tmp_path / "user-cache"
    local.write_text("not a directory")
    monkeypatch.setattr(index, "cache_dirs", lambda p: [local, fallback])
    assert len(mix.open_csfs(path.with_suffix(".c"))[0]) == 1100
    assert list(fallback.glob("*.json"))
    other = tmp_path / "also-readonly"
    other.write_text("not a directory")
    monkeypatch.setattr(index, "cache_dirs", lambda p: [local, other])
    assert len(mix.open_csfs(path.with_suffix(".c"))[0]) == 1100


def test_cancel_and_worker_replacement(tmp_path):
    path = make_files(tmp_path, 200000, 4)
    stopped = threading.Event()
    stopped.set()
    with pytest.raises(index.Cancelled):
        mix.open_mix(path, summary=True, workers=2, cancel=stopped)
    worker = index.ReadWorker()
    worker.submit(1, index.prepare_mix, path)
    time.sleep(0.15)
    worker.submit(2, index.prepare_mix, path, 20, 1)
    deadline = time.monotonic() + 15
    try:
        while time.monotonic() < deadline:
            kind, token, result = worker.events.get(timeout=15)
            if kind == "result" and token == 2:
                assert result["top_k"] >= 20
                break
            assert kind != "error", result
        else:
            pytest.fail("reader did not finish")
    finally:
        worker.close()
    assert not worker.thread.is_alive()
    assert not multiprocessing.active_children()


def test_csf_empty_blocks_and_blank_coupling(tmp_path):
    path = make_files(tmp_path, 3).with_suffix(".c")
    path.write_text(path.read_text() + " *\n\n *\n 2p-( 1)\n\n 1/2-\n *\n")
    assert old.read_csfs(path) == mix.read_csfs(path)
    assert old._csf_signature(path) == mix._csf_signature(path)
    for positions in ({0}, {1}, {2}, {3}, {0, 1, 2, 3}):
        a, b = tmp_path / "old.c", tmp_path / "new.c"
        old.write_csf_subset(path, a, positions)
        mix.write_csf_subset(path, b, positions)
        assert a.read_bytes() == b.read_bytes()


def test_cancel_running_processes(tmp_path, monkeypatch):
    monkeypatch.setattr(index, "PARALLEL_MIN_COEFFICIENTS", 0)
    path = make_files(tmp_path, 300000, 4)
    stop = threading.Event()
    seen = []

    def progress(path, fraction):
        if fraction > 0:
            seen.append(len(multiprocessing.active_children()))
            stop.set()

    with pytest.raises(index.Cancelled):
        mix.open_mix(path, summary=True, workers=2, cancel=stop, progress=progress)
    assert max(seen) == 2
    assert not multiprocessing.active_children()


def test_pairing_priority_and_candidate_changes(tmp_path):
    path = make_files(tmp_path, 31, 1)
    csf = path.with_suffix(".c")
    good = csf.read_bytes()
    fallback = tmp_path / "fallback.c"
    fallback.write_bytes(good)
    assert mix.find_csf_for(str(path)) == str(csf)
    csf.write_text(csf.read_text() + " 2p-( 1)\n\n 1/2-\n")
    assert mix.find_csf_for(str(path)) == str(fallback)
    preferred = tmp_path / "rcsf.inp"
    preferred.write_bytes(good)
    assert mix.find_csf_for(str(path)) == str(preferred)


def test_bounded_source_reads(tmp_path, monkeypatch):
    import builtins

    path = make_files(tmp_path, 200000, 2)
    original = builtins.open
    sizes = []

    class Guard:
        def __init__(self, stream):
            self.stream = stream

        def __getattr__(self, key):
            return getattr(self.stream, key)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.stream.close()

        def read(self, n=-1):
            assert 0 <= n <= index.CHUNK
            sizes.append(n)
            return self.stream.read(n)

    def guarded(name, *args, **kwargs):
        stream = original(name, *args, **kwargs)
        return Guard(stream) if os.fspath(name) == str(path) else stream

    monkeypatch.setattr(builtins, "open", guarded)
    blocks = mix.open_mix(path, summary=True, workers=1)
    assert len(blocks[0].levels) == 2
    assert max(sizes) == index.CHUNK


def test_subset_cannot_overwrite_source(tmp_path):
    path = make_files(tmp_path)
    for source, writer, keep in [(path, mix.write_mix_subset, {(1, -1)}),
                                 (path.with_suffix(".c"), mix.write_csf_subset, {0})]:
        before = source.read_bytes()
        with pytest.raises(ValueError):
            writer(source, source, keep)
        assert source.read_bytes() == before



@pytest.mark.parametrize("workers", [None, 2, 4])
def test_small_files_do_not_start_process_pool(tmp_path, monkeypatch, workers):
    path = make_files(tmp_path, 50, 4)

    def unexpected_pool(*args, **kwargs):
        pytest.fail("small files must not create a process pool")

    monkeypatch.setattr(index, "ProcessPoolExecutor", unexpected_pool)
    got = mix.open_mix(path, summary=True, workers=workers)
    expected = old.read_mix(path)
    for a, b in zip(got[0].levels, expected[0].levels):
        assert a.leading() == b.leading()
        assert a.top_components(500) == b.components()[:500]


@pytest.mark.parametrize("ncf,expected_pools", [(3, 0), (4, 1), (5, 1)])
def test_parallel_threshold_boundary(tmp_path, monkeypatch, ncf, expected_pools):
    path = make_files(tmp_path, ncf, 3)
    monkeypatch.setattr(index, "PARALLEL_MIN_COEFFICIENTS", 12)
    pool = index.ProcessPoolExecutor
    started = []

    def observe(*args, **kwargs):
        started.append(args[0])
        assert kwargs["mp_context"].get_start_method() == "spawn"
        return pool(*args, **kwargs)

    monkeypatch.setattr(index, "ProcessPoolExecutor", observe)
    result = mix.open_mix(path, summary=True, workers=2)
    assert len(started) == expected_pools
    assert result[0].levels[0].top_components(500) == old.read_mix(path)[0].levels[0].components()[:500]
    assert not multiprocessing.active_children()


def test_partially_written_mixing_file(tmp_path):
    """程序正在逐块写出的文件：只取完整的块并标记 partial；文件头不完整时报错。"""
    ncf, nev = 5, 2
    block = [record(struct.pack("<5i", 1, ncf, nev, 2, -1)), record(struct.pack(f"<{nev}i", 1, 2)),
             record(struct.pack(f"<{nev + 1}d", -7.4, 0.0, 0.001)), record(struct.pack(f"<{ncf * nev}d", *[0.5] * 10))]
    head = [record(b"G92MIX"), record(struct.pack("<6i", 3, 2 * ncf, 2, 2 * nev, ncf * nev, 2))]
    second = b"".join(block)
    path = tmp_path / "state.cm"
    path.write_bytes(b"".join(head + block) + second[:len(second) - 20])
    for blocks in (mix.open_mix(path), mix.open_mix(path, summary=True, workers=1), mix.read_mix(path)):
        assert blocks.partial and len(blocks) == 1 and len(blocks[0].levels) == nev
    path.write_bytes(b"".join(head + block) + second)
    blocks = mix.open_mix(path)
    assert not blocks.partial and len(blocks) == 2
    path.write_bytes(head[0] + head[1][:10])
    with pytest.raises(ValueError):
        mix.open_mix(path)
