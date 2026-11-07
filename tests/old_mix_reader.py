"""#21 的改动前读取器，仅作逐项对照，不用于程序运行。"""

import array
import re
import struct

JP_RE = re.compile(r"(\d+(?:/2)?)([+-])$")

class Level:
    def __init__(self, block, pos, energy, coeffs):
        self.block, self.pos, self.energy, self.coeffs = block, pos, energy, coeffs

    @property
    def J(self):
        return self.block.J

    @property
    def parity(self):
        return self.block.parity

    def components(self, min_weight=0.0):
        """[(CSF 在块内的序号 (从 1 开始), 系数)]，按权重从大到小。"""
        comps = [(i + 1, c) for i, c in enumerate(self.coeffs) if c * c >= min_weight]
        return sorted(comps, key=lambda t: -t[1] * t[1])

    def leading(self):
        return max(range(len(self.coeffs)), key=lambda i: abs(self.coeffs[i])) + 1


class Block:
    def __init__(self, nb, ncf, twoj1, parity):
        self.nb, self.ncf, self.twoj1, self.parity = nb, ncf, twoj1, parity
        self.levels = []

    @property
    def J(self):
        """J 的字符串，如 1/2、2"""
        twoj = self.twoj1 - 1
        return f"{twoj // 2}" if twoj % 2 == 0 else f"{twoj}/2"

    @property
    def label(self):
        return f"{self.J}{'+' if self.parity > 0 else '-'}"


def _records(data):
    pos = 0
    while pos < len(data):
        size, = struct.unpack_from("<i", data, pos)
        end = pos + 4 + size
        if size < 0 or end + 4 > len(data) or struct.unpack_from("<i", data, end)[0] != size:
            raise ValueError("not a Fortran unformatted file")
        yield data[pos + 4:end]
        pos = end + 4


def read_mix(path):
    """读取混合系数文件，返回 [Block]；各 Block.levels 为 [Level]。"""
    with open(path, "rb") as f:
        recs = _records(f.read())
        try:
            if next(recs)[:6] != b"G92MIX":
                raise ValueError("not a GRASP mixing coefficient file (G92MIX)")
            nelec, ncftot, nw, nvectot, nvecsiz, nblock = struct.unpack("<6i", next(recs)[:24])
            blocks = []
            for _ in range(nblock):
                nb, ncf, nev, twoj1, parity = struct.unpack("<5i", next(recs)[:20])
                block = Block(nb, ncf, twoj1, parity)
                if nev > 0:
                    ivec = struct.unpack(f"<{nev}i", next(recs)[:4 * nev])
                    e = struct.unpack(f"<{nev + 1}d", next(recs)[:8 * (nev + 1)])
                    vec = array.array("d")
                    vec.frombytes(next(recs)[:8 * ncf * nev])
                    for k in range(nev):
                        block.levels.append(Level(block, ivec[k], e[0] + e[k + 1], vec[k * ncf:(k + 1) * ncf]))
                blocks.append(block)
        except StopIteration:
            raise ValueError("truncated mixing coefficient file") from None
    return blocks


def _record(body):
    n = struct.pack("<i", len(body))
    return n + body + n


def write_mix_subset(src, dst, keep):
    """只保留 (2J, 宇称) 在 keep 中的块，写出新的混合系数文件；块号重新从 1 编起，
    文件头中的 CSF 总数、能级总数、混合系数总数 (Σ ncf·nev) 和块数随之更新。"""
    with open(src, "rb") as f:
        recs = list(_records(f.read()))
    nelec, ncftot, nw, nvectot, nvecsiz, nblock = struct.unpack("<6i", recs[1][:24])
    blocks, i = [], 2
    for _ in range(nblock):
        nb, ncf, nev, twoj1, parity = struct.unpack("<5i", recs[i][:20])
        n = 4 if nev > 0 else 1
        if (twoj1 - 1, parity) in keep:
            blocks.append((ncf, nev, recs[i:i + n]))
        i += n
    out = [recs[0], struct.pack("<6i", nelec, sum(b[0] for b in blocks), nw, sum(b[1] for b in blocks),
                                sum(b[0] * b[1] for b in blocks), len(blocks)) + recs[1][24:]]
    for nb, (_, _, rs) in enumerate(blocks, 1):
        out.append(struct.pack("<i", nb) + rs[0][4:])
        out += rs[1:]
    with open(dst, "wb") as f:
        f.write(b"".join(_record(r) for r in out))
    return len(blocks)


def write_csf_subset(src, dst, positions):
    """只保留 CSF 文件中第 positions 个块 (从 0 开始，与混合系数文件的块顺序一致)，块之间仍以 " *" 分隔。"""
    with open(src) as f:
        lines = f.read().splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith("CSF(s):")) + 1
    blocks, cur = [], []
    for ln in lines[start:]:
        if ln.strip() == "*":
            blocks.append(cur)
            cur = []
        else:
            cur.append(ln)
    blocks.append(cur)
    kept = [blocks[i] for i in sorted(positions)]
    body = []
    for k, b in enumerate(kept):
        if k:
            body.append(" *")
        body += b
    with open(dst, "w") as f:
        f.write("\n".join(lines[:start] + body) + "\n")


def read_csfs(path):
    """读取 CSF 文件，返回各块的 CSF 列表 [[(组态, 耦合)]]，与混合系数文件的块一一对应。"""
    with open(path) as f:
        lines = f.read().splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith("CSF(s):")) + 1
    blocks, current, i = [], [], start
    while i < len(lines):
        if lines[i].strip() == "*":
            blocks.append(current)
            current = []
            i += 1
            continue
        if not lines[i].strip():
            i += 1
            continue
        orbitals = re.sub(r"\s*\(\s*", "(", lines[i].strip())
        orbitals = re.sub(r"\s*\)\s*", ") ", orbitals).strip()
        coupling = " ".join((lines[i + 1].split() if i + 1 < len(lines) else []) +
                            (lines[i + 2].split() if i + 2 < len(lines) else []))
        current.append((orbitals, coupling))
        i += 3
    blocks.append(current)
    return blocks


def _mix_signature(path):
    """(轨道数, ((CSF 数, J, 宇称), ...))，只读文件头和各块的块头。"""
    with open(path, "rb") as f:
        recs = _records(f.read())
    next(recs)
    nelec, ncftot, nw, nvectot, nvecsiz, nblock = struct.unpack("<6i", next(recs)[:24])
    blocks = []
    for _ in range(nblock):
        nb, ncf, nev, twoj1, parity = struct.unpack("<5i", next(recs)[:20])
        b = Block(nb, ncf, twoj1, parity)
        blocks.append((ncf, b.J, "+" if parity > 0 else "-"))
        if nev > 0:
            next(recs), next(recs), next(recs)
    return nw, tuple(blocks)


def _csf_signature(path):
    """(轨道数, ((CSF 数, J, 宇称), ...))：轨道数为核心和外层轨道之和，J^π 取自每块第一个 CSF 的第 3 行。"""
    with open(path) as f:
        lines = f.read().splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith("CSF(s):"))
    nw = sum(len(ln.split()) for ln in lines[:start] if not ln.rstrip().endswith(":"))
    # 与 read_csfs 一样每个 CSF 占 3 行 (第 2 行可能是空行)
    lines = lines[start + 1:] + ["*"]
    blocks, ncf, jp, i = [], 0, None, 0
    while i < len(lines):
        if lines[i].strip() == "*":
            if ncf:
                blocks.append((ncf, *(jp.groups() if jp else ("?", "?"))))
            ncf, jp = 0, None
            i += 1
        elif not lines[i].strip():
            i += 1
        else:
            if not ncf:
                jp = JP_RE.search(lines[i + 2].strip()) if i + 2 < len(lines) else None
            ncf += 1
            i += 3
    return nw, tuple(blocks)
