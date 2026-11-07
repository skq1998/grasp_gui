"""
读取 GRASP 的原子态 (ASF) 结果，只用 Python 标准库：

  * 混合系数文件 name.m (rmcdhf)、name.cm (rci)、rmix.out —— Fortran 无格式顺序文件：
        'G92MIX'
        nelec, ncftot, nw, nvectot, nvecsiz, nblock
        对每个对称块:  nb, ncf, nev, 2J+1, parity
                       ivec(1..nev)                     能级在块内的序号
                       eav, eval(1..nev)                总能量 = eav + eval
                       evec(1..ncf, 1..nev)             混合系数
  * CSF 列表 name.c —— 文本，每个 CSF 三行，块之间以 "*" 分隔
  * jj2lsj 的 LSJ 组成 name.lsj.lbl —— 文本
"""

import array
import os
import re
import struct
import sys

import grasp_index as index

HARTREE_CM = 219474.6313632     # 1 a.u. 能量 = 219474.63 cm⁻¹
HARTREE_EV = 27.211386245988


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
        if hasattr(self, "_leading"):
            return self._leading
        return max(range(len(self.coeffs)), key=lambda i: abs(self.coeffs[i])) + 1

    def top_components(self, limit, min_weight=0.0):
        """界面只需前 limit 项；完整 components() 的语义不变。"""
        if hasattr(self, "_top") and (limit <= len(self._top) or len(self.coeffs) <= len(self._top)):
            return [(i, c) for i, c in self._top[:limit] if c * c >= min_weight]
        return self.components(min_weight)[:limit]


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


class Coefficients:
    """按能级访问系数；迭代分块读取，单个索引只读一个 double。"""

    def __init__(self, saved, rec, offset, count):
        self.saved, self.rec, self.offset, self.count = saved, rec, offset, count

    def __len__(self):
        return self.count

    def __iter__(self):
        index.unchanged(self.saved)
        with open(self.saved[0], "rb") as f:
            for values in index.doubles(f, self.rec, self.offset, self.count):
                yield from values
        index.unchanged(self.saved)

    def __getitem__(self, i):
        if isinstance(i, slice):
            return array.array("d", (self[j] for j in range(*i.indices(self.count))))
        i = i + self.count if i < 0 else i
        if not 0 <= i < self.count:
            raise IndexError(i)
        index.unchanged(self.saved)
        with open(self.saved[0], "rb") as f:
            return struct.unpack("<d", b"".join(index.chunks(f, self.rec, self.offset + i * 8, 8)))[0]


class Blocks(list):
    """open_mix / read_mix 的结果：[Block]；partial 为真时文件尚未写完 (只含完整的块)。"""
    partial = False


def open_mix(path, summary=False, workers=None, cancel=None, progress=None, k=index.TOP_K):
    """只读元数据，系数按需访问；summary 额外准备并缓存前 k 项。
    尚未写完的文件只取完整的块，并把结果的 partial 设为真；文件头不完整时抛出 ValueError。"""
    data = (index.prepare_mix(path, k, workers, cancel, progress) if summary else index.mix_index(path, cancel))
    blocks = Blocks()
    blocks.partial = bool(data.get("partial"))
    for meta in data["blocks"]:
        b = Block(meta["nb"], meta["ncf"], meta["twoj1"], meta["parity"])
        for i in range(meta["nev"]):
            coeffs = Coefficients(data["stamp"], meta["records"][-1], i * b.ncf * 8, b.ncf)
            lv = Level(b, meta["ids"][i], meta["energies"][i], coeffs)
            if data.get("top_k", 0):
                lv._top = meta["top"][i]
                lv._leading = meta["leading"][i]
                value = next((c for pos, c in lv._top if pos == lv._leading), None)
                lv._leading_coeff = value if value is not None else lv.coeffs[lv._leading - 1]
            b.levels.append(lv)
        blocks.append(b)
    return blocks


def read_mix(path):
    """完整读取的兼容接口；逐能级分块读取，不再保留整个原文件的副本。"""
    blocks = open_mix(path)
    for b in blocks:
        for lv in b.levels:
            lv.coeffs = array.array("d", lv.coeffs)
    return blocks


def mix_symmetries(path):
    """混合系数文件中各块的 (2J, 宇称)，按块的顺序。"""
    return [(b.twoj1 - 1, b.parity) for b in open_mix(path)]


def _record(body):
    n = struct.pack("<i", len(body))
    return n + body + n


def write_mix_subset(src, dst, keep):
    """重写总计数和块号；所选记录按原始字节流复制，支持续记录。"""
    if os.path.realpath(src) == os.path.realpath(dst) or (os.path.exists(dst) and os.path.samefile(src, dst)):
        raise ValueError("source and destination must differ")
    data = index.mix_index(src)
    selected = [b for b in data["blocks"] if (b["twoj1"] - 1, b["parity"]) in keep]
    counts = list(data["header"])
    counts[1] = sum(b["ncf"] for b in selected)
    counts[3] = sum(b["nev"] for b in selected)
    counts[4] = sum(b["ncf"] * b["nev"] for b in selected)
    counts[5] = len(selected)
    with open(src, "rb") as source, open(dst, "wb") as out:
        out.write(_record(index.body(source, data["records"][0])))
        header = index.body(source, data["records"][1])
        out.write(_record(struct.pack("<6i", *counts) + header[24:]))
        for nb, b in enumerate(selected, 1):
            rs = b["records"]
            header = index.body(source, rs[0])
            out.write(_record(struct.pack("<i", nb) + header[4:]))
            for rec in rs[1:]:
                source.seek(rec["start"])
                remaining = rec["end"] - rec["start"]
                while remaining:
                    raw = source.read(min(index.CHUNK, remaining))
                    if not raw:
                        raise ValueError("truncated mixing coefficient file")
                    out.write(raw)
                    remaining -= len(raw)
    index.unchanged(data["stamp"])
    return len(selected)


def write_csf_subset(src, dst, positions):
    """按索引跳到所选块，流式复制并保留旧写法的换行和分隔符。"""
    if os.path.realpath(src) == os.path.realpath(dst) or (os.path.exists(dst) and os.path.samefile(src, dst)):
        raise ValueError("source and destination must differ")
    data = index.csf_index(src)
    kept = [data["blocks"][i] for i in sorted(positions)]
    with open(src, "rb") as source, open(dst, "w") as out:
        for line in source:
            text = line.decode("utf-8").rstrip("\r\n")
            out.write(text + "\n")
            if text.startswith("CSF(s):"):
                break
        for i, b in enumerate(kept):
            if i:
                out.write(" *\n")
            source.seek(b["start"])
            while source.tell() < b["end"]:
                line = source.readline()
                if not line:
                    raise ValueError("truncated CSF file")
                out.write(line.decode("utf-8").rstrip("\r\n") + "\n")
    index.unchanged(data["stamp"])


class CsfBlock:
    """通过稀疏偏移查找三行 CSF；只缓存最近的一小部分。"""

    def __init__(self, saved, meta):
        import base64
        from collections import OrderedDict

        self.saved, self.meta = saved, meta
        self.offsets = array.array("Q")
        self.offsets.frombytes(base64.b64decode(meta["offsets"], validate=True))
        if sys.byteorder != "little":
            self.offsets.byteswap()
        self.cache = OrderedDict()

    def __len__(self):
        return self.meta["ncf"]

    def __iter__(self):
        index.unchanged(self.saved)
        with open(self.saved[0], "rb") as f:
            f.seek(self.meta["start"])
            for _ in range(len(self)):
                raw = f.readline()
                while raw and not raw.strip():
                    raw = f.readline()
                yield parse_csf(raw, f.readline(), f.readline())
        index.unchanged(self.saved)

    def __getitem__(self, i):
        if isinstance(i, slice):
            return [self[j] for j in range(*i.indices(len(self)))]
        i = i + len(self) if i < 0 else i
        return self.read_many([i])[i]

    def read_many(self, positions, cancel=None):
        """合并同一检查点内的请求，跳过未显示 CSF 的正则解析。"""
        index.unchanged(self.saved)
        out, groups = {}, {}
        for i in positions:
            if not 0 <= i < len(self):
                raise IndexError(i)
            if i in self.cache:
                out[i] = self.cache[i]
                self.cache.move_to_end(i)
            else:
                groups.setdefault(i // index.STRIDE, set()).add(i)
        with open(self.saved[0], "rb") as f:
            for group, wanted in sorted(groups.items()):
                index.checkpoint(cancel)
                f.seek(self.offsets[group])
                for i in range(group * index.STRIDE, max(wanted) + 1):
                    raw = f.readline()
                    while raw and not raw.strip():
                        raw = f.readline()
                    a, b = f.readline(), f.readline()
                    if i in wanted:
                        out[i] = parse_csf(raw, a, b)
                        self.cache[i] = out[i]
                        if len(self.cache) > 1024:
                            self.cache.popitem(last=False)
        index.unchanged(self.saved)
        return out


def open_csfs(path, cancel=None, progress=None):
    data = index.csf_index(path, cancel, progress)
    return [CsfBlock(data["stamp"], b) for b in data["blocks"]]


def parse_csf(raw, a, b):
    orbitals = re.sub(r"\s*\(\s*", "(", raw.decode("utf-8").strip())
    orbitals = re.sub(r"\s*\)\s*", ") ", orbitals).strip()
    return orbitals, " ".join(a.decode("utf-8").split() + b.decode("utf-8").split())


def read_csfs(path):
    """完整读取的兼容接口；界面用 open_csfs 按需读取。"""
    return [list(b) for b in open_csfs(path)]


LEVEL_RE = re.compile(r"^\s*(\d+)\s+(\d+(?:/2)?)\s+([+-])\s+(-?\d+\.\d+)\s+([\d.]+)%")
COMP_RE = re.compile(r"^\s+(-?\d+\.\d+)\s+(\d+\.\d+)\s+(\S+)\s*$")


def read_lsj(path):
    """读取 jj2lsj 的 name.lsj.lbl：返回 [(J, 宇称, 序号, 总能量, 组成百分比, [(系数, 权重, LSJ 标记)])]。"""
    levels = []
    with open(path) as f:
        for line in f:
            m = LEVEL_RE.match(line)
            if m:
                pos, J, par, energy, pct = m.groups()
                levels.append((J, par, int(pos), float(energy), float(pct), []))
                continue
            m = COMP_RE.match(line)
            if m and levels:
                levels[-1][5].append((float(m.group(1)), float(m.group(2)), m.group(3)))
    return levels


# ---------------------------------------------------------------------------
#  按内容识别文件、配对 CSF 列表和 LSJ 结果 (不依赖文件名)
# ---------------------------------------------------------------------------

_sig_cache = {}            # 路径 -> (mtime, 特征)


def _cached(path, fn):
    try:
        mtime = index.stamp(path)
    except OSError:
        return None
    hit = _sig_cache.get((path, fn))
    if hit and hit[0] == mtime:
        return hit[1]
    try:
        value = fn(path)
    except (OSError, ValueError, StopIteration, struct.error, UnicodeDecodeError, IndexError):
        value = None
    _sig_cache[(path, fn)] = (mtime, value)
    return value


def _head(path, n):
    try:
        with open(path, "rb") as f:
            return f.read(n)
    except OSError:
        return b""


def fortran_tag(path):
    """Fortran 无格式文件第一条记录的 6 字节标识，如 G92MIX、G92RWF；不是时返回 None。"""
    head = _head(path, 10)
    if len(head) == 10 and struct.unpack_from("<i", head)[0] == 6:
        return head[4:10].decode("ascii", "replace")
    return None


def is_csf_file(path):
    return _head(path, 15) == b"Core subshells:"


def _mix_signature(path):
    data = index.mix_index(path)
    blocks = [Block(b["nb"], b["ncf"], b["twoj1"], b["parity"]) for b in data["blocks"]]
    return data["header"][2], tuple((b.ncf, b.J, "+" if b.parity > 0 else "-") for b in blocks)


JP_RE = re.compile(r"(\d+(?:/2)?)([+-])$")


def _csf_signature(path, cancel=None, progress=None):
    data = index.csf_index(path, cancel, progress)
    return data["nw"], tuple((b["ncf"], *b["jp"]) for b in data["blocks"] if b["ncf"])


def csf_orbitals(path):
    """CSF 文件用到的全部轨道 (核心和外层)，如 ["1s", "2s", "2p-", "2p"]。"""
    with open(path) as f:
        lines = []
        for line in f:
            if line.startswith("CSF(s):"):
                break
            lines.append(line)
    return [t for ln in lines if not ln.rstrip().endswith(":") for t in ln.split()]


def mix_signature(path):
    return _cached(path, _mix_signature)


def csf_signature(path):
    return _cached(path, _csf_signature)


def find_csf_for(mix_path, cancel=None, progress=None):
    """找与混合系数文件对应的 CSF 文件：特征 (轨道数、各块的 CSF 数与 J^π) 必须一致。
    先试同名的 .c，再试 rcsf.inp / rcsf.out，最后在目录中所有 CSF 文件里找 (新的优先)。"""
    sig = mix_signature(mix_path)
    if not sig:
        return None
    d, base = os.path.split(mix_path)
    stem = os.path.splitext(base)[0]
    first = [os.path.join(d, stem + ".c"), os.path.join(d, "rcsf.inp"), os.path.join(d, "rcsf.out")]
    try:
        others = sorted((os.path.join(d, n) for n in os.listdir(d)), key=os.path.getmtime, reverse=True)
    except OSError:
        others = []
    seen = set()
    for p in first + others:
        index.checkpoint(cancel)
        if p in seen or not os.path.isfile(p):
            continue
        seen.add(p)
        if is_csf_file(p):
            try:
                if _csf_signature(p, cancel, progress) == sig:
                    return p
            except (OSError, ValueError, StopIteration, struct.error, UnicodeDecodeError, IndexError):
                continue
    return None


def find_lsj_for(mix_path, blocks):
    """找与混合系数文件对应的 jj2lsj 结果：按能级的 J^π 和能量匹配，先试同名的 .lsj.lbl，
    再试目录中其他 *.lsj.lbl；返回 (路径, {(块号, 序号): LSJ 条目})，匹配最多者优先。"""
    d, base = os.path.split(mix_path)
    stem = os.path.splitext(base)[0]
    same = os.path.join(d, stem + ".lsj.lbl")
    try:
        names = sorted(n for n in os.listdir(d) if n.endswith(".lsj.lbl"))
    except OSError:
        names = []
    nlev = sum(len(b.levels) for b in blocks)
    best = (None, {})
    for p in [same] + [os.path.join(d, n) for n in names if os.path.join(d, n) != same]:
        entries = _cached(p, read_lsj) if os.path.isfile(p) else None
        if not entries:
            continue
        matched = match_lsj(blocks, entries)
        if len(matched) > len(best[1]):
            best = (p, matched)
        if len(matched) == nlev:
            break
    return best


def match_lsj(blocks, lsj_levels, tol=1e-6):
    """把 LSJ 组成按 J、宇称和能量对应到能级上：返回 {(块号, 序号): LSJ 条目}。

    同名的 .m 与 .cm 共用一个 .lsj.lbl，只有能量一致时才对应。
    """
    out = {}
    for J, par, pos, energy, pct, comps in lsj_levels:
        for b in blocks:
            if b.J == J and (b.parity > 0) == (par == "+"):
                for lv in b.levels:
                    if lv.pos == pos and abs(lv.energy - energy) < tol * max(1.0, abs(energy)):
                        out[(b.nb, lv.pos)] = (pct, comps)
    return out


# ---------------------------------------------------------------------------
#  能级的组态和谱项 (用于导出)
# ---------------------------------------------------------------------------

L_LETTERS = "SPDFGHIKLMNOQRTUV"
LSJ_SEGMENT = re.compile(r"^(\d+[a-z])(?:\((\d+)\))?(?:\d[A-Z]\d?)?(?:_(\d+[A-Z]))?$")
JJ_SUBSHELL = re.compile(r"(\d+[a-z])-?\((\d+)\)")


def parse_lsj_label(label):
    """jj2lsj 的 LSJ 标记，如 2s(2).2p(6).3s_2S.3p_1P、1s(2).2s(2).2p(3)2D3_2D：
    返回 ([(nl, 占据数)], 总谱项如 "1P")；格式不认识时返回 (None, None)。
    各段之间以 "." 分隔：nl、(占据数)、子壳层谱项 (2S+1、L、辛弱数)、"_" 后为耦合到此的谱项。"""
    config, term = [], None
    for seg in label.split("."):
        m = LSJ_SEGMENT.match(seg)
        if not m:
            return None, None
        config.append((m.group(1), int(m.group(2) or 1)))
        term = m.group(3) or term
    return config, term


def nonrel_config(jj_orbitals):
    """jj 耦合的 CSF 组态 (如 "2s(2) 2p-(2) 2p(4) 3s(1)") 合并为非相对论组态 [(nl, 占据数)]，如 2p- 与 2p 相加。"""
    out = {}
    for nl, occ in JJ_SUBSHELL.findall(jj_orbitals):
        out[nl] = out.get(nl, 0) + int(occ)
    return [(nl, occ) for nl, occ in out.items() if occ > 0]


def csf_core(path):
    """CSF 文件中的核心轨道 (Core subshells，全部占满)，合并为非相对论组态 [(nl, 占据数)]：
    nl- (j = l - 1/2) 可容纳 2l 个电子，nl (j = l + 1/2) 可容纳 2l + 2 个。"""
    lines = []
    with open(path) as f:                    # 只读文件头 (CSF 列表可能很大)
        for ln in f:
            if ln.startswith("CSF(s):"):
                break
            lines.append(ln.rstrip())
    try:
        start = lines.index("Core subshells:") + 1
        end = next(i for i in range(start, len(lines)) if lines[i].endswith(":"))
    except (ValueError, StopIteration):
        return []
    out = {}
    for tok in " ".join(lines[start:end]).split():
        m = re.match(r"(\d+)([a-z])(-?)$", tok)
        if not m:
            continue
        l = "spdfghiklmnoqrtuv".index(m.group(2))
        nl = m.group(1) + m.group(2)
        out[nl] = out.get(nl, 0) + (2 * l if m.group(3) else 2 * l + 2)
    return list(out.items())


# 默认的组态缩写：惰性气体原子实
NOBLE_GAS_CORES = [
    ["[He]", "1s2"],
    ["[Ne]", "1s2.2s2.2p6"],
    ["[Ar]", "1s2.2s2.2p6.3s2.3p6"],
    ["[Kr]", "1s2.2s2.2p6.3s2.3p6.3d10.4s2.4p6"],
    ["[Xe]", "1s2.2s2.2p6.3s2.3p6.3d10.4s2.4p6.4d10.5s2.5p6"],
    ["[Rn]", "1s2.2s2.2p6.3s2.3p6.3d10.4s2.4p6.4d10.5s2.5p6.4f14.5d10.6s2.6p6"],
]


def parse_config(text):
    """组态文字 (如 1s2.2s2.2p6、1s2 2s2 2p6、1s(2)) -> [(nl, 占据数)]；看不懂时返回 None。"""
    out = []
    for tok in re.split(r"[\s.]+", text.strip()):
        if not tok:
            continue
        m = re.match(r"^(\d+[a-z])(?:\((\d+)\)|(\d*))$", tok)
        if not m:
            return None
        out.append((m.group(1), int(m.group(2) or m.group(3) or 1)))
    return out


def abbreviate(config, rules):
    """按规则 [[缩写, 完整组态], ...] 缩写组态：组态中含有规则的全部子壳层 (占据数相同) 时，
    把它们换成缩写写在最前面 (与子壳层的书写顺序无关)；缩写可以为空，表示省略。
    子壳层多的规则先用，用过的子壳层不再参与。返回 (缩写文字, 剩余组态)。"""
    rest = list(config)
    prefix = ""
    parsed = [(abbr, parse_config(full)) for abbr, full in rules]
    for abbr, need in sorted((r for r in parsed if r[1]), key=lambda r: -len(r[1])):
        if all(rest.count(x) >= need.count(x) for x in need):
            for x in need:
                rest.remove(x)
            prefix += abbr
    return prefix, rest


def config_ascii(config):
    """NIST 原子光谱数据库的写法：2s2.2p6.3s"""
    return ".".join(f"{nl}{occ if occ > 1 else ''}" for nl, occ in config)


def term_ascii(term, parity):
    """NIST 的写法：奇宇称的谱项加 *，如 3P*"""
    return f"{term}{'*' if parity < 0 else ''}" if term else ""


def natural_key(name):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", name)]


def is_mix_file(path):
    """文件头是否为 G92MIX (与文件名无关；scatterplot.m 等同扩展名的脚本不是)。"""
    return fortran_tag(path) == "G92MIX"


def is_biorthogonal(name):
    """rbiotransform 写出的 name.bm / name.cbm：双正交变换后的系数，不是 (归一化的) 物理态。
    这两个后缀由 rbiotransform 决定，与用户起的名字无关。"""
    return name.endswith(".bm") or name.endswith(".cbm")


def find_mix_files(directory):
    """目录中所有混合系数文件 (按文件头识别，不看扩展名)，不含双正交变换的 .bm / .cbm。"""
    try:
        names = os.listdir(directory)
    except OSError:
        return []
    files = [n for n in names if not is_biorthogonal(n) and os.path.isfile(os.path.join(directory, n))
             and is_mix_file(os.path.join(directory, n))]
    return sorted(files, key=natural_key)


def load_result_files(paths, limit, directory=None, cancel=None, progress=None):
    """在协调线程完成索引、配对和主要标签，返回前核对源文件未改变。"""
    data = {}
    if directory or paths:
        index.clean_cache(directory or os.path.dirname(paths[0]))
    for path in paths:
        index.checkpoint(cancel)
        try:
            saved = index.stamp(path)
            blocks = open_mix(path, summary=True, cancel=cancel, progress=progress, k=limit)
            csf_path = find_csf_for(path, cancel, progress)
            csfs, csf_saved = None, None
            if csf_path:
                csf_saved = index.stamp(csf_path)
                csfs = open_csfs(csf_path, cancel, progress)
            lsj_path, lsj = find_lsj_for(path, blocks)
            for b in blocks:
                leads = csfs[b.nb - 1].read_many([lv.leading() - 1 for lv in b.levels], cancel) if csfs else {}
                for lv in b.levels:
                    index.checkpoint(cancel)
                    lv._leading_csf = leads[lv.leading() - 1] if csfs else ("", "")
            index.unchanged(saved)
            if csf_saved:
                index.unchanged(csf_saved)
            data[path] = blocks, csfs, csf_path, lsj, lsj_path
        except (OSError, ValueError, StopIteration, struct.error, IndexError) as e:
            data[path] = e
    return data


def load_composition(lv, csfs, limit, cancel=None, progress=None):
    """只解析当前能级实际可能显示的 CSF，阈值切换无需重新读取。"""
    comps = lv.top_components(limit)
    names = csfs[lv.block.nb - 1].read_many([i - 1 for i, c in comps], cancel) if csfs else {}
    return [(i, c, *names.get(i - 1, ("", ""))) for i, c in comps]
