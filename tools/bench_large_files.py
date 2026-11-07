#!/usr/bin/env python3
"""只读基准：比较 1/2/4 个进程的首次索引、缓存打开、切换能级和进程树峰值 RSS。

python3 tools/bench_large_files.py DIR
只在索引缓存目录创建临时基准缓存，不改源文件，也不清除用户已有的索引。
"""

import argparse
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import grasp_index as index
import grasp_mix as mix


def tree_rss(pid):
    """Linux 累加进程树 RSS；其他系统用 ps 获取相同指标。"""
    if sys.platform.startswith("linux"):
        total, pending = 0, [pid]
        while pending:
            current = pending.pop()
            try:
                status = Path(f"/proc/{current}/status").read_text()
                total += next(int(line.split()[1]) for line in status.splitlines() if line.startswith("VmRSS:"))
                children = Path(f"/proc/{current}/task/{current}/children").read_text()
                pending.extend(int(x) for x in children.split())
            except (OSError, StopIteration):
                continue
        return total * 1024
    lines = subprocess.check_output(["ps", "-axo", "pid=,ppid=,rss="], text=True).splitlines()
    rows = [tuple(map(int, line.split())) for line in lines]
    wanted = {pid}
    while True:
        new = {p for p, parent, rss in rows if parent in wanted} - wanted
        if not new:
            break
        wanted |= new
    return sum(rss for p, parent, rss in rows if p in wanted) * 1024


def timed(function):
    peak, stop = [tree_rss(os.getpid())], threading.Event()

    def sample():
        while not stop.wait(0.02):
            peak[0] = max(peak[0], tree_rss(os.getpid()))

    thread = threading.Thread(target=sample)
    thread.start()
    started = time.perf_counter()
    try:
        result = function()
    finally:
        elapsed = time.perf_counter() - started
        peak[0] = max(peak[0], tree_rss(os.getpid()))
        stop.set()
        thread.join()
    return result, dict(seconds=round(elapsed, 4), tree_peak_rss_mib=round(peak[0] / 2**20, 2))


def probe(directory, workers, cache, force_parallel=False):
    if force_parallel:
        index.PARALLEL_MIN_COEFFICIENTS = 0
    # 每次使用独立的缓存命名空间，避免删除真实用户的索引，也保证首次读入无摘要缓存。
    index.cache_dirs = lambda path: [Path(cache) / hashlib.sha256(os.fsencode(os.path.dirname(path))).hexdigest()]
    paths = [os.path.join(directory, n) for n in mix.find_mix_files(directory)]
    if not paths:
        raise ValueError("no mixing files found")

    def read():
        rows = []
        for path in paths:
            blocks = mix.open_mix(path, summary=True, workers=workers)
            csf_path = mix.find_csf_for(path)
            csfs = mix.open_csfs(csf_path) if csf_path else None
            lsj = mix.find_lsj_for(path, blocks)
            for b in blocks:
                for lv in b.levels:
                    if csfs:
                        csfs[b.nb - 1][lv.leading() - 1]
                    rows.append((lv, csfs))
        return rows

    rows, first = timed(read)
    rows, second = timed(read)

    def switch():
        count = 0
        for lv, csfs in rows:
            count += len(mix.load_composition(lv, csfs, index.TOP_K))
        return count

    count, composition = timed(switch)
    size = sum(p.stat().st_size for p in Path(cache).rglob("*.json"))
    print(json.dumps(dict(workers=workers, parallel_min_coefficients=index.PARALLEL_MIN_COEFFICIENTS,
                          levels=len(rows), components=count, first=first, cached=second,
                          all_level_compositions=composition, cache_bytes=size), ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory")
    parser.add_argument("--workers", type=int, choices=(1, 2, 4), help="只测这个进程数")
    parser.add_argument("--force-parallel", action="store_true", help="测量启动开销：忽略小文件串行阈值")
    parser.add_argument("--probe-cache", help=argparse.SUPPRESS)
    args = parser.parse_args()
    directory = os.path.abspath(args.directory)
    if args.probe_cache:
        probe(directory, args.workers, args.probe_cache, args.force_parallel)
        return
    candidates = index.cache_dirs(os.path.join(directory, "_"))
    base = None
    for folder in candidates:
        try:
            folder.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="bench-probe-", dir=folder):
                pass
            base = folder
            break
        except OSError:
            continue
    if base is None:
        parser.error("neither index cache directory is writable")
    for workers in [args.workers] if args.workers else (1, 2, 4):
        with tempfile.TemporaryDirectory(prefix="bench-", dir=base) as cache:
            command = [sys.executable, __file__, directory, "--workers", str(workers), "--probe-cache", cache]
            if args.force_parallel:
                command.append("--force-parallel")
            result = subprocess.run(command, text=True, capture_output=True)
            if result.returncode:
                raise RuntimeError(result.stderr)
            print(result.stdout.strip(), flush=True)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
