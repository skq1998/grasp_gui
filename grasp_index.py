"""大结果文件的流式记录、稀疏索引和摘要缓存；服务器与界面共用，不导入 Qt。"""

import array
import base64
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
import hashlib
import heapq
import json
import multiprocessing
import os
from pathlib import Path
import re
import struct
import sys
import tempfile
import warnings

STRIDE = 256
TOP_K = 500
CACHE_VERSION = 1
CHUNK = 1024 * 1024
# 小文件的 spawn 开销高于系数分析；阈值依据源码和 onefile 的实测。
PARALLEL_MIN_COEFFICIENTS = 4_000_000
_worker_stop = None
_worker_progress = None


class Cancelled(Exception):
    """用户取消读入；不作为文件错误显示。"""


def checkpoint(cancel):
    if cancel is not None and cancel.is_set():
        raise Cancelled()


def stamp(path):
    path = os.path.abspath(path)
    st = os.stat(path)
    return [path, st.st_size, st.st_mtime_ns]


def unchanged(saved):
    if stamp(saved[0]) != saved:
        raise ValueError("source file changed during reading")


def record_index(f, cancel=None):
    """跳读逻辑记录；支持 gfortran 用负长度标记连接的 >2 GB 记录。"""
    endfile = os.fstat(f.fileno()).st_size
    while f.tell() < endfile:
        checkpoint(cancel)
        start, parts, previous = f.tell(), [], False
        while True:
            raw = f.read(4)
            if len(raw) != 4:
                raise ValueError("truncated Fortran record marker")
            head, = struct.unpack("<i", raw)
            n = abs(head)
            offset = f.tell()
            if offset + n + 4 > endfile:
                raise ValueError("truncated Fortran record")
            f.seek(n, 1)
            tail, = struct.unpack("<i", f.read(4))
            if abs(tail) != n or (tail < 0) != previous:
                raise ValueError("invalid Fortran record markers")
            parts.append([offset, n])
            if head >= 0:
                break
            previous = True
        end = f.tell()
        yield dict(start=start, end=end, size=sum(n for _, n in parts), parts=parts)
        f.seek(end)


def chunks(f, rec, offset=0, size=None, cancel=None):
    """在逻辑记录内取指定区间，单次最多分配 CHUNK 字节。"""
    size = rec["size"] - offset if size is None else size
    if offset < 0 or size < 0 or offset + size > rec["size"]:
        raise ValueError("invalid record range")
    for pos, n in rec["parts"]:
        if offset >= n:
            offset -= n
            continue
        f.seek(pos + offset)
        take = min(n - offset, size)
        while take:
            checkpoint(cancel)
            data = f.read(min(CHUNK, take))
            if not data:
                raise ValueError("truncated Fortran record")
            yield data
            take -= len(data)
            size -= len(data)
        offset = 0
        if not size:
            return


def body(f, rec, size=None):
    return b"".join(chunks(f, rec, size=size))


def doubles(f, rec, offset, count, cancel=None):
    rest = b""
    for raw in chunks(f, rec, offset, count * 8, cancel):
        raw = rest + raw
        n = len(raw) // 8 * 8
        values = array.array("d")
        values.frombytes(raw[:n])
        if sys.byteorder != "little":
            values.byteswap()
        yield values
        rest = raw[n:]
    if rest:
        raise ValueError("unaligned mixing coefficients")


def cache_dirs(path):
    parent = os.path.dirname(os.path.abspath(path))
    digest = hashlib.sha256(os.fsencode(parent)).hexdigest()
    return [Path(parent) / ".grasp_gui" / "index",
            Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "grasp_gui" / "index" / digest]


def cache_name(path, kind):
    key = os.fsencode(os.path.abspath(path)) + kind.encode("ascii")
    return hashlib.sha256(key).hexdigest() + ".json"


def cached(path, kind):
    expected = stamp(path)
    for directory in cache_dirs(path):
        try:
            with open(directory / cache_name(path, kind), encoding="utf-8") as f:
                doc = json.load(f)
            encoded = json.dumps(doc["data"], separators=(",", ":"), sort_keys=True).encode("utf-8")
            if (doc["digest"] == hashlib.sha256(encoded).hexdigest()
                    and doc["version"] == CACHE_VERSION and doc["stamp"] == expected
                    and doc["stride"] == STRIDE and doc["kind"] == kind):
                data = doc["data"]
                if valid_cache(data, kind, expected):
                    return data
        except (OSError, ValueError, KeyError, TypeError):
            # 缓存不是计算结果；损坏或旧格式可以由源文件重建。
            pass
    return None


def valid_cache(data, kind, saved):
    """合法 JSON 也可能被截改，使用偏移和计数之前先检查结构。"""
    try:
        if data["stamp"] != saved or not isinstance(data["blocks"], list):
            return False
        for b in data["blocks"]:
            if not isinstance(b["ncf"], int) or not 0 <= b["ncf"] <= saved[1]:
                return False
            if kind == "csf":
                if not isinstance(data["nw"], int) or data["nw"] < 0 or "label" not in b:
                    return False
                raw = base64.b64decode(b["offsets"], validate=True)
                if len(raw) != ((b["ncf"] + STRIDE - 1) // STRIDE) * 8:
                    return False
                offsets = struct.unpack(f"<{len(raw) // 8}Q", raw)
                if any(not b["start"] <= p < b["end"] <= saved[1] for p in offsets):
                    return False
                if b["ncf"] and (not isinstance(b["jp"], list) or len(b["jp"]) != 2):
                    return False
            else:
                if not 0 <= b["nev"] <= saved[1] or len(b["records"]) != (4 if b["nev"] else 1):
                    return False
                if b["nev"] and (len(b["ids"]) != b["nev"] or len(b["energies"]) != b["nev"]):
                    return False
                if data.get("top_k", 0) and b["nev"]:
                    if len(b["top"]) != b["nev"] or len(b["leading"]) != b["nev"]:
                        return False
                    for top, lead in zip(b["top"], b["leading"]):
                        if (len(top) != min(b["ncf"], data["top_k"]) or not 1 <= lead <= b["ncf"]
                                or any(not 1 <= i <= b["ncf"] for i, c in top)):
                            return False
                for rec in b["records"]:
                    if (not 0 <= rec["start"] < rec["end"] <= saved[1]
                            or sum(n for _, n in rec["parts"]) != rec["size"]
                            or any(p < 0 or n < 0 or p + n > saved[1] for p, n in rec["parts"])):
                        return False
        if kind == "mix" and (len(data["header"]) != 6 or len(data["blocks"]) != data["header"][5]):
            return False
        return True
    except (ValueError, KeyError, TypeError, IndexError, struct.error):
        return False


def save_cache(saved, kind, data):
    unchanged(saved)
    doc = dict(version=CACHE_VERSION, stamp=saved, stride=STRIDE, kind=kind, data=data)
    encoded = json.dumps(data, separators=(",", ":"), sort_keys=True).encode("utf-8")
    doc["digest"] = hashlib.sha256(encoded).hexdigest()
    for directory in cache_dirs(saved[0]):
        temp = None
        try:
            directory.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory,
                                             prefix=".index-", delete=False) as f:
                temp = f.name
                json.dump(doc, f, separators=(",", ":"), allow_nan=False)
            unchanged(saved)
            os.replace(temp, directory / cache_name(saved[0], kind))
            return
        except OSError:
            # 查看别人的只读目录时改用用户缓存；两处都不可写才只用内存。
            continue
        finally:
            if temp and os.path.exists(temp):
                os.unlink(temp)


def clean_cache(directory):
    """只清理本目录对应且源文件已经删除的索引，不碰其他工作目录。"""
    directory = os.path.abspath(directory)
    for folder in cache_dirs(os.path.join(directory, "_")):
        try:
            names = list(folder.glob("*.json"))
        except OSError:
            continue
        for path in names:
            try:
                with path.open(encoding="utf-8") as f:
                    # stamp 位于摘要之前；清理时无需解析其他文件的全部摘要。
                    prefix = f.read(16384)
                start = prefix.index('"stamp":') + len('"stamp":')
                source = json.JSONDecoder().raw_decode(prefix[start:].lstrip())[0][0]
                if os.path.dirname(source) != directory:
                    continue
                try:
                    os.stat(source)
                except FileNotFoundError:
                    path.unlink()
            except (OSError, ValueError, KeyError, TypeError, IndexError):
                # 无法识别的缓存由对应文件下次打开时重建。
                continue


def mix_index(path, cancel=None):
    checkpoint(cancel)
    hit = cached(path, "mix")
    if isinstance(hit, dict) and isinstance(hit.get("blocks"), list):
        return hit
    saved = stamp(path)
    with open(path, "rb") as f:
        recs = record_index(f, cancel)
        try:
            tag = next(recs)
            if body(f, tag, 6) != b"G92MIX":
                raise ValueError("not a GRASP mixing coefficient file (G92MIX)")
            header = next(recs)
            counts = struct.unpack("<6i", body(f, header, 24))
            if min(counts) < 0:
                raise ValueError("invalid mixing file header")
        except StopIteration:
            raise ValueError("truncated mixing coefficient file") from None
        blocks, partial = [], False
        for _ in range(counts[5]):
            checkpoint(cancel)
            try:
                r = next(recs)
                nb, ncf, nev, twoj1, parity = struct.unpack("<5i", body(f, r, 20))
                if ncf < 0 or nev < 0 or twoj1 < 1 or parity not in (-1, 1):
                    raise ValueError("invalid mixing block header")
                block = dict(nb=nb, ncf=ncf, nev=nev, twoj1=twoj1, parity=parity, records=[r])
                if nev:
                    ids, energies, coeffs = next(recs), next(recs), next(recs)
                    if coeffs["size"] != 8 * ncf * nev:
                        raise ValueError("invalid mixing coefficient count")
                    block["ids"] = list(struct.unpack(f"<{nev}i", body(f, ids, 4 * nev)))
                    e = struct.unpack(f"<{nev + 1}d", body(f, energies, 8 * (nev + 1)))
                    block["energies"] = [e[0] + x for x in e[1:]]
                    block["records"] += [ids, energies, coeffs]
            except (StopIteration, ValueError, struct.error):
                # 文件尚未写完 (程序正在逐块写出)：只取完整的块。缓存按大小和修改时间核对，文件写完后会重建。
                partial = True
                break
            blocks.append(block)
            # 读记录正文会移动文件位置；恢复到当前块的结尾再跳下一块。
            f.seek(block["records"][-1]["end"])
    data = dict(header=counts, records=[tag, header], blocks=blocks, stamp=saved, top_k=0, partial=partial)
    save_cache(saved, "mix", data)
    return data


def _init_worker(stop, progress):
    global _worker_stop, _worker_progress
    _worker_stop = stop
    _worker_progress = progress


def _top_task(task, cancel=None, on_chunk=None):
    path, saved, rec, offset, count, k, number = task
    cancel = cancel if cancel is not None else _worker_stop
    unchanged(saved)
    heap, leading, maximum, i = [], 1, -1.0, 0
    with open(path, "rb") as f:
        for values in doubles(f, rec, offset, count, cancel):
            for c in values:
                i += 1
                weight = c * c
                if abs(c) > maximum:
                    maximum, leading = abs(c), i
                if len(heap) < k:
                    heapq.heappush(heap, (weight, -i, c))
                elif weight > heap[0][0]:
                    heapq.heapreplace(heap, (weight, -i, c))
            if _worker_progress is not None:
                _worker_progress[number] = i
            if on_chunk:
                on_chunk(i)
    unchanged(saved)
    return [(int(-i), c) for _, i, c in sorted(heap, reverse=True)], leading


def prepare_mix(path, k=TOP_K, workers=None, cancel=None, progress=None):
    """分块计算各能级前 k 项；进程间只传偏移、计数和摘要。"""
    if k < 1:
        raise ValueError("component count must be positive")
    data = mix_index(path, cancel)
    if data.get("top_k", 0) >= k:
        checkpoint(cancel)
        return data
    tasks, targets = [], []
    for b in data["blocks"]:
        if b["nev"]:
            rec = b["records"][-1]
            for i in range(b["nev"]):
                tasks.append((str(path), data["stamp"], rec, i * b["ncf"] * 8, b["ncf"], k, len(tasks)))
                targets.append((b, i))
            b["top"] = [None] * b["nev"]
            b["leading"] = [None] * b["nev"]
    total = max(1, sum(t[4] for t in tasks))
    if total < PARALLEL_MIN_COEFFICIENTS:
        workers = 1
    elif workers is None:
        from grasp_core import physical_cpu_count
        workers = min(physical_cpu_count() or 1, 4)
    workers = max(1, min(workers, len(tasks)))
    if progress:
        progress(str(path), 0)

    def accept(i, result):
        b, row = targets[i]
        b["top"][row], b["leading"][row] = result

    if workers == 1:
        completed = 0
        for i, task in enumerate(tasks):
            checkpoint(cancel)
            report = (lambda n: progress(str(path), (completed + n) / total)) if progress else None
            accept(i, _top_task(task, cancel, report))
            completed += task[4]
    else:
        ctx = multiprocessing.get_context("spawn")
        stop = ctx.Event()
        completed = ctx.Array("q", len(tasks), lock=False)
        pool = None
        try:
            pool = ProcessPoolExecutor(workers, mp_context=ctx, initializer=_init_worker, initargs=(stop, completed))
            pending, next_task = {}, 0
            while pending or next_task < len(tasks):
                checkpoint(cancel)
                while next_task < len(tasks) and len(pending) < 2 * workers:
                    pending[pool.submit(_top_task, tasks[next_task])] = next_task
                    next_task += 1
                ready, _ = wait(pending, timeout=0.1, return_when=FIRST_COMPLETED)
                if progress:
                    progress(str(path), sum(completed) / total)
                for future in ready:
                    accept(pending.pop(future), future.result())
        except (OSError, RuntimeError) as e:
            stop.set()
            if pool:
                pool.shutdown(wait=True, cancel_futures=True)
                pool = None
            checkpoint(cancel)
            warnings.warn(f"parallel analysis unavailable; retrying serially: {e}")
            for i, task in enumerate(tasks):
                accept(i, _top_task(task, cancel))
        finally:
            stop.set()
            if pool:
                pool.shutdown(wait=True, cancel_futures=True)
    data["top_k"] = k
    checkpoint(cancel)
    save_cache(data["stamp"], "mix", data)
    return data


def csf_index(path, cancel=None, progress=None):
    checkpoint(cancel)
    hit = cached(path, "csf")
    if isinstance(hit, dict) and isinstance(hit.get("blocks"), list):
        return hit
    saved = stamp(path)
    blocks, nw, count, offsets, jp, lines, label = [], 0, 0, [], None, 0, ""
    with open(path, "rb") as f:
        for raw in f:
            if raw.startswith(b"CSF(s):"):
                break
            if not raw.rstrip().endswith(b":"):
                nw += len(raw.split())
        else:
            raise ValueError("not a GRASP CSF file")
        start = f.tell()
        while True:
            checkpoint(cancel)
            pos, raw = f.tell(), f.readline()
            if not raw or raw.strip() == b"*":
                packed = array.array("Q", offsets)
                if sys.byteorder != "little":
                    packed.byteswap()
                blocks.append(dict(ncf=count, jp=jp, start=start, end=pos, label=label,
                                   offsets=base64.b64encode(packed.tobytes()).decode("ascii")))
                count, offsets, jp, start, label = 0, [], None, f.tell(), ""
                if not raw:
                    break
            elif raw.strip():
                if count % STRIDE == 0:
                    offsets.append(pos)
                second, third = f.readline(), f.readline()
                if not count:
                    second = second.decode("utf-8")
                    coupling = third.strip().decode("utf-8")
                    tokens = second.split() + coupling.split()
                    label = tokens[-1] if tokens else ""
                    match = re.search(r"(\d+(?:/2)?)([+-])$", coupling)
                    jp = list(match.groups()) if match else ["?", "?"]
                count += 1
            lines += 1
            if progress and lines % 8192 == 0:
                progress(str(path), f.tell() / max(1, saved[1]))
    data = dict(nw=nw, blocks=blocks, stamp=saved)
    save_cache(saved, "csf", data)
    return data


class ReadWorker:
    """单个协调线程，只保留最新请求；旧请求通过分块检查退出。"""

    def __init__(self):
        import queue
        import threading

        self.requests = queue.Queue(maxsize=1)
        self.events = queue.Queue()
        self.cancel = threading.Event()
        self.closed = False
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def submit(self, token, function, *args):
        import queue
        import threading

        self.cancel.set()
        self.cancel = threading.Event()
        try:
            self.requests.get_nowait()
        except queue.Empty:
            pass
        self.requests.put_nowait((token, function, args, self.cancel))

    def _run(self):
        import time

        while True:
            request = self.requests.get()
            if request is None:
                return
            token, function, args, cancel = request
            last = 0.0

            def progress(path, fraction):
                nonlocal last
                now = time.monotonic()
                if now - last >= 0.1 or fraction == 1:
                    self.events.put(("progress", token, (path, fraction)))
                    last = now

            try:
                checkpoint(cancel)
                result = function(*args, cancel=cancel, progress=progress)
                checkpoint(cancel)
                self.events.put(("result", token, result))
            except Cancelled:
                continue
            except Exception as e:
                # 在线程边界交给主线程显示错误，不能无声终止读入线程。
                self.events.put(("error", token, e))

    def close(self):
        import queue

        if self.closed:
            return
        self.closed = True
        self.cancel.set()
        try:
            self.requests.get_nowait()
        except queue.Empty:
            pass
        self.requests.put_nowait(None)
        self.thread.join()
