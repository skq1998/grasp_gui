"""
读取 GRASP 径向波函数文件 (name.w、rwfn.out) 并计算轨道性质。只用 Python 标准库。

文件为 Fortran 无格式顺序文件 (每条记录前后各有 4 字节长度)：
    'G92RWF'
    对每个轨道:  n, kappa, energy, npts
                 a0, P(1..npts), Q(1..npts)
                 r(1..npts)
"""

import math
import os
import re
import struct

L_LETTERS = "spdfghiklmnoqrtuv"


class Orbital:
    def __init__(self, n, kappa, energy, a0, r, P, Q):
        self.n, self.kappa, self.energy, self.a0 = n, kappa, energy, a0
        self.r, self.P, self.Q = r, P, Q
        self._grid = exp_grid(r)

    @property
    def l(self):
        return self.kappa if self.kappa > 0 else -self.kappa - 1

    @property
    def name(self):
        """GRASP 写法：2p- 为 j = l - 1/2，2p 为 j = l + 1/2。"""
        return f"{self.n}{L_LETTERS[self.l]}{'-' if self.kappa > 0 else ''}"

    @property
    def sort_key(self):
        return (self.n, self.l, 0 if self.kappa > 0 else 1)

    def density(self):
        return [p * p + q * q for p, q in zip(self.P, self.Q)]

    def integrate(self, values):
        return integrate(values, self.r, self._grid)

    def norm(self):
        return self.integrate(self.density())

    def mean_r(self, k=1):
        """<r^k> = ∫ r^k (P² + Q²) dr"""
        return self.integrate([r ** k * d for r, d in zip(self.r, self.density())])

    def r_peak(self):
        """|P| 最大处的 r"""
        i = max(range(len(self.P)), key=lambda i: abs(self.P[i]))
        return self.r[i]

    def nodes(self, thresh=0.05):
        """大分量 P(r) 的节点数，与 GRASP 的 COUNT (lib9290/count.f90) 相同：
        按变号分成若干段，去掉幅度小于最大幅度 thresh 倍的段 (rmcdhf 默认 0.05)，再数剩余各段之间的变号。"""
        lobes = []                 # 每段的极值
        for p in self.P[1:]:
            if lobes and (p > 0) == (lobes[-1] > 0) or (lobes and p == 0):
                if abs(p) > abs(lobes[-1]):
                    lobes[-1] = p
            else:
                lobes.append(p)
        big = max((abs(x) for x in lobes), default=0.0)
        kept = [x for x in lobes if abs(x) >= thresh * big]
        return sum(1 for a, b in zip(kept, kept[1:]) if (a > 0) != (b > 0))

    def expected_nodes(self):
        return self.n - self.l - 1


def exp_grid(r):
    """GRASP 网格 r_i = RNT (exp((i-1) h) - 1)：返回 (RNT, h)；不是这种网格时返回 None。"""
    if len(r) < 4 or r[1] <= 0 or r[2] / r[1] <= 1:
        return None
    h = math.log(r[2] / r[1] - 1)
    if h <= 0:
        return None
    rnt = r[1] / (math.exp(h) - 1)
    for i in (len(r) // 2, len(r) - 1):
        if abs(rnt * (math.exp(i * h) - 1) - r[i]) > 1e-8 * max(r[i], 1e-30):
            return None
    return rnt, h


def integrate(values, r, grid=None):
    """∫ f dr。指数网格上用均匀变量的 Simpson 公式 (与 GRASP 的精度一致)，否则用梯形公式。"""
    n = len(values)
    if grid:
        rnt, h = grid
        g = [v * (x + rnt) * h for v, x in zip(values, r)]
        m = n - 1 if (n - 1) % 2 == 0 else n - 2
        s = (g[0] + g[m] + 4 * sum(g[1:m:2]) + 2 * sum(g[2:m - 1:2])) / 3
        if m != n - 1:
            s += (g[m] + g[m + 1]) / 2
        return s
    return sum((values[i] + values[i + 1]) / 2 * (r[i + 1] - r[i]) for i in range(n - 1))


def read_w(path):
    """读取 .w 文件，返回按 n、l、κ 排序的 Orbital 列表。格式不对时抛出 ValueError。"""
    with open(path, "rb") as f:
        data = f.read()
    pos = 0

    def record():
        nonlocal pos
        if pos + 4 > len(data):
            raise ValueError("truncated file")
        size, = struct.unpack_from("<i", data, pos)
        end = pos + 4 + size
        if size < 0 or end + 4 > len(data) or struct.unpack_from("<i", data, end)[0] != size:
            raise ValueError("not a Fortran unformatted file")
        body = data[pos + 4:end]
        pos = end + 4
        return body

    if record()[:6] != b"G92RWF":
        raise ValueError("not a GRASP radial wave function file (G92RWF)")
    orbitals = []
    while pos < len(data):
        n, kappa, energy, npts = struct.unpack("<iidi", record())
        pq = record()
        a0, = struct.unpack_from("<d", pq)
        P = struct.unpack_from(f"<{npts}d", pq, 8)
        Q = struct.unpack_from(f"<{npts}d", pq, 8 + 8 * npts)
        r = struct.unpack(f"<{npts}d", record())
        orbitals.append(Orbital(n, kappa, energy, a0, r, P, Q))
    return sorted(orbitals, key=lambda o: o.sort_key)


def orbital_names(path):
    """文件中各轨道的名称 (如 2p-)，只读每个轨道的头记录，不解析 P、Q 数组。"""
    names = []
    with open(path, "rb") as f:
        def record(keep):
            head = f.read(4)
            if len(head) < 4:
                return None
            size, = struct.unpack("<i", head)
            if size < 0:
                raise ValueError("not a Fortran unformatted file")
            body = f.read(size) if keep else f.seek(size, 1)
            f.read(4)
            return body
        if (record(True) or b"")[:6] != b"G92RWF":
            raise ValueError("not a GRASP radial wave function file (G92RWF)")
        while True:
            head = record(True)
            if not head:
                break
            n, kappa = struct.unpack_from("<ii", head)
            l = kappa if kappa > 0 else -kappa - 1
            names.append(f"{n}{L_LETTERS[l]}{'-' if kappa > 0 else ''}")
            record(False)
            record(False)
    return names


def natural_key(name):
    """odd2.w < odd10.w"""
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", name)]


def is_wfn_file(path):
    """文件头是否为 G92RWF (与文件名无关)。"""
    try:
        with open(path, "rb") as f:
            head = f.read(10)
    except OSError:
        return False
    return len(head) == 10 and struct.unpack_from("<i", head)[0] == 6 and head[4:10] == b"G92RWF"


def find_w_files(directory):
    """目录中所有径向波函数文件 (按文件头识别：name.w、rwfn.inp、rwfn.out 以及改过名的文件)。"""
    try:
        names = os.listdir(directory)
    except OSError:
        return []
    files = [n for n in names if os.path.isfile(os.path.join(directory, n)) and is_wfn_file(os.path.join(directory, n))]
    return sorted(files, key=natural_key)


def indexed_w(path):
    """逐记录定位波函数；记录范围、点数和尾标记均检查，不保留整个文件。"""
    rows = []
    with open(path, "rb") as f:
        end = os.fstat(f.fileno()).st_size

        def record():
            head = f.read(4)
            if len(head) != 4:
                raise ValueError("truncated wavefunction record")
            size, = struct.unpack("<i", head)
            start = f.tell()
            if size < 0 or start + size + 4 > end:
                raise ValueError("invalid wavefunction record")
            f.seek(size, 1)
            if f.read(4) != head:
                raise ValueError("invalid wavefunction record marker")
            return start, size

        start, size = record()
        f.seek(start)
        if size < 6 or f.read(6) != b"G92RWF":
            raise ValueError("not a GRASP radial wave function file (G92RWF)")
        f.seek(start + size + 4)
        while f.tell() < end:
            start, size = record()
            if size != 20:
                raise ValueError("invalid orbital header")
            f.seek(start)
            n, kappa, energy, count = struct.unpack("<iidi", f.read(size))
            if count < 1 or not 0 <= (kappa if kappa > 0 else -kappa - 1) < len(L_LETTERS):
                raise ValueError("invalid orbital quantum numbers or grid")
            f.seek(start + size + 4)
            pq, size = record()
            if size != 8 + 16 * count:
                raise ValueError("invalid orbital radial arrays")
            grid, size = record()
            if size != 8 * count:
                raise ValueError("invalid orbital grid")
            orbital = Orbital(n, kappa, energy, 0, [], [], [])
            rows.append(dict(n=n, kappa=kappa, energy=energy, points=count, name=orbital.name,
                             P=pq + 8, Q=pq + 8 + 8 * count, r=grid))
    return rows


def _values(f, offset, positions):
    out = []
    for i in positions:
        f.seek(offset + i * 8)
        raw = f.read(8)
        if len(raw) != 8:
            raise ValueError("wavefunction changed while reading")
        out.append(struct.unpack("<d", raw)[0])
    return out


def sampled_w(path, names=None, points=1000):
    """在原网格参数上作线性重采样；每条曲线最多请求点数，不传输原文件。"""
    if type(points) is not int or not 2 <= points <= 10000:
        raise ValueError("points must be between 2 and 10000")
    result = []
    with open(path, "rb") as f:
        for row in indexed_w(path):
            if names is not None and row["name"] not in names:
                continue
            count = row["points"]
            number = min(points, count)
            targets = [i * (count - 1) / max(number - 1, 1) for i in range(number)]
            needed = sorted({j for x in targets for j in (int(x), min(int(x) + 1, count - 1))})
            out = {key: row[key] for key in ("name", "n", "kappa", "energy", "points")}
            for key in ("r", "P", "Q"):
                values = dict(zip(needed, _values(f, row[key], needed)))
                out[key] = [(1 - (x - int(x))) * values[int(x)] +
                            (x - int(x)) * values[min(int(x) + 1, count - 1)] for x in targets]
            result.append(out)
    return result


def orbital_properties(path):
    """分块积分与两遍节点统计；使用与 Orbital 相同的网格、阈值和 Simpson 规则。"""
    with open(path, "rb") as f:
        for row in indexed_w(path):
            count = row["points"]
            positions = sorted({0, min(1, count - 1), min(2, count - 1), min(3, count - 1), count // 2, count - 1})
            samples = dict(zip(positions, _values(f, row["r"], positions)))
            grid = None
            if count >= 4 and samples[1] > 0 and samples[2] / samples[1] > 1:
                h = math.log(samples[2] / samples[1] - 1)
                if h > 0:
                    rnt = samples[1] / math.expm1(h)
                    if all(abs(rnt * math.expm1(i * h) - samples[i]) <= 1e-8 * max(samples[i], 1e-30)
                           for i in (count // 2, count - 1)):
                        grid = rnt, h
            norm = mean = maximum = node_maximum = 0.0
            peak = samples[0]
            previous = None
            m = count - 1 if (count - 1) % 2 == 0 else count - 2
            for start in range(0, count, 8192):
                amount = min(8192, count - start)
                arrays = []
                for key in ("r", "P", "Q"):
                    f.seek(row[key] + start * 8)
                    arrays.append(struct.unpack(f"<{amount}d", f.read(amount * 8)))
                for i, (r, p, q) in enumerate(zip(*arrays), start):
                    density = p * p + q * q
                    if abs(p) > maximum:
                        maximum, peak = abs(p), r
                    if i > 0:
                        node_maximum = max(node_maximum, abs(p))
                    if grid:
                        factor = (1 if i in (0, m) else 4 if i % 2 else 2) / 3 if i <= m else 0
                        if count - 1 != m and i >= m:
                            factor += 0.5
                        factor *= (r + grid[0]) * grid[1]
                        norm += density * factor
                        mean += r * density * factor
                    elif previous is not None:
                        old_r, old_density = previous
                        norm += (r - old_r) * (density + old_density) / 2
                        mean += (r - old_r) * (r * density + old_r * old_density) / 2
                    previous = r, density
            nodes, kept, sign, amplitude = 0, None, None, 0.0
            for start in range(1, count, 8192):
                amount = min(8192, count - start)
                f.seek(row["P"] + start * 8)
                for p in struct.unpack(f"<{amount}d", f.read(amount * 8)):
                    current = p > 0
                    if sign is None:
                        sign = current
                    if p and current != sign:
                        if amplitude >= node_maximum * 0.05:
                            nodes += int(kept is not None and kept != sign)
                            kept = sign
                        sign, amplitude = current, abs(p)
                    else:
                        amplitude = max(amplitude, abs(p))
            if amplitude >= node_maximum * 0.05:
                nodes += int(kept is not None and kept != sign)
            l = row["kappa"] if row["kappa"] > 0 else -row["kappa"] - 1
            yield dict((k, row[k]) for k in ("name", "n", "kappa", "energy", "points")), dict(
                norm=norm, mean_r=mean, r_peak=peak, nodes=nodes, expected_nodes=row["n"] - l - 1)
