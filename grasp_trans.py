"""
跃迁计算：在能级查看器中选定两个能级后，自动准备并运行 rbiotransform + rtransition，读取结果。

GRASP 的做法：rtransition 读取 isodata 和初、末态各自的 name.c、name.bw、name.(c)bm，输出
name1.name2.(c)t。两组轨道来自不同的计算时互不正交，先用 rbiotransform 做双正交变换
(由 name.c、name.w、name.(c)m 生成 name.bw、name.(c)bm)；两个能级来自同一个混合系数文件时
轨道相同，不需要变换，直接把 name.w、name.(c)m 当作 name.bw、name.(c)bm。

为了不依赖文件名 (改过名的文件、.m 与 .cm 混用) 也不覆盖用户的文件，计算在工作目录下的
.grasp_gui/tr/<编号>/ 中进行 (目录名尽量短：GRASP 的 MPI 版本只能处理 128 个字符以内的路径)：输入文件以软链接 a.* (和 b.*) 放入，混合系数一律按
CI 结果 (.cm) 处理 (两者格式相同)。默认只算选中的两个能级所在的 J^π 块 (把这些块写成新的 .c 和 .cm)，也可以算两个文件的全部 J^π；
rtransition 一次算出范围内所有能级之间的跃迁，结果按 (文件对, 范围, 跃迁类型) 缓存，
输入文件改变后自动重算。只用 Python 标准库。
"""

import hashlib
import json
import os
import re
import shutil

import grasp_mix as mix
import grasp_wfn as wfn

CACHE_DIR = os.path.join(".grasp_gui", "tr")
MANIFEST_VERSION = 1        # 缓存格式的版本：格式改变时加 1，旧缓存即视为失效并重算
MPI_PATH_MAX = 128          # GRASP 的 MPI 库 (lib/mpi90) 中目录和文件路径的字符长度
KINDS = ["E1", "M1", "E2", "M2"]
GAUGES = {"B": "Babushkin", "C": "Coulomb", "M": ""}


def two_j(block):
    return block.twoj1 - 1


def multipoles(twoj1, p1, twoj2, p2):
    """两个能级之间要计算的跃迁类型：宇称改变时为 E1 (ΔJ ≤ 1)，否则 M2；
    宇称不变时为 M1 和 E2 中允许的。J = 0 → 0 时返回 []。"""
    def allowed(kind):
        k = int(kind[1])
        if not (abs(twoj1 - twoj2) <= 2 * k <= twoj1 + twoj2):
            return False
        change = (-1) ** k if kind[0] == "E" else (-1) ** (k + 1)    # Ek: (-1)^k；Mk: (-1)^(k+1)
        return p1 * p2 == change
    if p1 != p2:
        return ["E1"] if allowed("E1") else [k for k in ["M2"] if allowed(k)]
    return [k for k in ["M1", "E2"] if allowed(k)]


# ---------------------------------------------------------------------------
#  找到混合系数文件对应的 CSF 文件和波函数文件
# ---------------------------------------------------------------------------

def find_w_for(mix_path, csf_path):
    """与混合系数文件对应的波函数文件：(路径或 None, 候选列表)。文件必须包含 CSF 文件用到的全部轨道。
    同名的 .w 优先；没有时只在候选唯一时采用 (轨道集合完全相同的优先)。不同计算的 .w 常有相同的轨道
    (如 odd4.w 与 even4.w)，猜错会算出错误的结果而不报错，因此有多个候选时不猜。"""
    need = set(mix.csf_orbitals(csf_path))
    d, base = os.path.split(mix_path)
    same = os.path.join(d, os.path.splitext(base)[0] + ".w")
    exact, superset = [], []
    for p in [same] + [os.path.join(d, n) for n in wfn.find_w_files(d) if os.path.join(d, n) != same]:
        if not os.path.isfile(p):
            continue
        try:
            have = set(wfn.orbital_names(p))
        except (OSError, ValueError):
            continue
        if not need <= have:
            continue
        if p == same:
            return p, [p]
        (exact if have == need else superset).append(p)
    for group in (exact, superset):
        if group:
            return (group[0] if len(group) == 1 else None), group
    return None, []


class Side:
    """一个混合系数文件及其 CSF 文件、波函数文件。"""

    def __init__(self, mix_path):
        self.mix = os.path.abspath(mix_path)
        csf = mix.find_csf_for(self.mix)
        self.csf = os.path.abspath(csf) if csf else None
        w, self.w_candidates = find_w_for(self.mix, self.csf) if self.csf else (None, [])
        self.w = os.path.abspath(w) if w else None

    def missing(self):
        """缺少的文件：None (齐全)、"CSF"、"w" (没有) 或 "w?" (有多个候选，见 w_candidates)。"""
        if not self.csf:
            return "CSF"
        if not self.w:
            return "w?" if self.w_candidates else "w"
        return None

    def stamp(self):
        return {k: [p, os.path.getmtime(p), os.path.getsize(p)] for k, p in
                (("mix", self.mix), ("csf", self.csf), ("w", self.w))}


# ---------------------------------------------------------------------------
#  计算目录
# ---------------------------------------------------------------------------

class Workspace:
    """两个混合系数文件 (可以是同一个) 之间的跃迁计算目录。角色 a、b 按文件名排序，
    因此 (A, B) 与 (B, A) 共用一个目录。

    每次计算 (run) 有自己的子目录和对称性范围 subset：{角色: [[2J, 宇称], ...]}，为 None 时包含
    全部 J^π。只算部分 J^π 时把 .c 和 .cm 中的对应块写成新文件再计算 (双正交变换按块进行，
    结果与全部计算时相同)；rbiotransform 自带的 J 选项不能用：它把没选的块原样写进 .cbm，
    rtransition 仍会全部计算。"""

    def __init__(self, workdir, path1, path2):
        self.workdir = os.path.abspath(workdir)
        paths = sorted({os.path.abspath(path1), os.path.abspath(path2)}, key=os.path.basename)
        self.same = len(paths) == 1
        self.sides = {"a": Side(paths[0])}
        if not self.same:
            self.sides["b"] = Side(paths[1])
        self.name = " / ".join(os.path.basename(p) for p in paths)
        self.dir = os.path.join(self.workdir, CACHE_DIR, hashlib.sha1("\0".join(paths).encode()).hexdigest()[:10])
        self.manifest_path = os.path.join(self.dir, "manifest.json")

    @property
    def final(self):
        return "a" if self.same else "b"

    def role_of(self, mix_path):
        for role, side in self.sides.items():
            if side.mix == os.path.abspath(mix_path):
                return role
        return None

    def problems(self):
        """不能计算的原因 (列表)；空列表表示可以计算。"""
        out = []
        if not os.path.isfile(os.path.join(self.workdir, "isodata")):
            out.append(("isodata", self.workdir))
        for side in self.sides.values():
            m = side.missing()
            if m:
                out.append((m, side.mix if m != "w?" else (side.mix, side.w_candidates)))
        return out

    def stamp(self):
        return {role: side.stamp() for role, side in self.sides.items()}

    def manifest(self):
        """读取记录；输入文件改变 (或记录不存在) 时返回空记录。"""
        try:
            with open(self.manifest_path) as f:
                m = json.load(f)
        except (OSError, ValueError):
            return {}
        try:
            current = self.stamp()
        except OSError:
            return {}
        return m if m.get("inputs") == current and m.get("version") == MANIFEST_VERSION else {}

    def save_manifest(self, m):
        m["version"] = MANIFEST_VERSION
        m["inputs"] = self.stamp()
        with open(self.manifest_path, "w") as f:
            json.dump(m, f, indent=1)

    # ---- 对称性范围 ----
    def subset_for(self, levels):
        """只包含给定能级所在块的范围；levels 为 [(角色, 2J, 宇称)]。"""
        sub = {role: set() for role in self.sides}
        for role, twoj, parity in levels:
            sub[role].add((twoj, parity))
        return {role: sorted([list(x) for x in v]) for role, v in sub.items()}

    @staticmethod
    def run_id(subset):
        if subset is None:
            return "all"
        return hashlib.sha1(json.dumps(subset, sort_keys=True).encode()).hexdigest()[:6]

    def missing_kinds(self, upper, lower, kinds):
        """upper / lower 为 (角色, 2J, 宇称)：返回这对能级还没有算的跃迁类型。"""
        runs = self.manifest().get("runs", {})

        def covers(run, level):
            sub = run["subset"]
            return sub is None or [level[1], level[2]] in sub.get(level[0], [])
        return [k for k in kinds if not any(k in r["kinds"] and covers(r, upper) and covers(r, lower)
                                            for r in runs.values())]

    def mpi_ok(self, mpitmp, subset=None):
        """MPI 版本能否处理这里的路径 (计算目录、MPI_TMP 下各进程的子目录和其中的文件名)。"""
        d = os.path.join(self.dir, self.run_id(subset))
        longest = max(len(d), len(os.path.join(d, mpitmp)) + 4) + len("/a.cbm")
        return longest < MPI_PATH_MAX

    # ---- 计算 ----
    def prepare(self, subset=None):
        """建立计算子目录；缺块或选定 J^π 时同步重写 .c / .cm 的块序。
        输入文件改变后先清空旧结果。返回子目录名。"""
        m = self.manifest()
        if not m:
            if os.path.isdir(self.dir):
                shutil.rmtree(self.dir)      # 只删除本程序自己的计算目录
            m = {"runs": {}}
        rid = self.run_id(subset)
        rdir = os.path.join(self.dir, rid)
        os.makedirs(rdir, exist_ok=True)
        link(os.path.join(self.workdir, "isodata"), os.path.join(rdir, "isodata"))
        for role, side in self.sides.items():
            link(side.w, os.path.join(rdir, f"{role}.w"))
            keep = {tuple(x) for x in subset[role]} if subset else None
            sym = mix.mix_symmetries(side.mix)
            selected = [nb for nb, jp in sym.items() if keep is None or jp in keep]
            if selected == list(range(1, len(mix.open_csfs(side.csf)) + 1)):
                link(side.csf, os.path.join(rdir, f"{role}.c"))
                link(side.mix, os.path.join(rdir, f"{role}.cm"))
            else:
                for name in (f"{role}.c", f"{role}.cm"):
                    if os.path.lexists(os.path.join(rdir, name)):
                        os.remove(os.path.join(rdir, name))
                chosen = {sym[nb] for nb in selected}
                nw = mix.write_csf_subset(side.csf, os.path.join(rdir, f"{role}.c"),
                                          [nb - 1 for nb in selected], prune=True)
                mix.write_mix_subset(side.mix, os.path.join(rdir, f"{role}.cm"), chosen, nw=nw)
        if self.same:                        # 同一组轨道：不需要变换
            link(self.sides["a"].w, os.path.join(rdir, "a.bw"))
            link(os.path.join(rdir, "a.cm"), os.path.join(rdir, "a.cbm"))
        m["runs"].setdefault(rid, {"subset": subset, "bio": self.same, "kinds": {}})
        self.save_manifest(m)
        return rid

    def jobs(self, rid, kinds, steps):
        """运行所需的任务 (grasp_core 的任务格式)；steps 为 {键: Step}。"""
        out = []
        if not self.manifest()["runs"][rid]["bio"]:
            bio = steps["rbiotransform"]
            out.append(bio.job(dict(bio.defaults(), ci=True, init="a", final="b", log="rbiotransform.log")))
        tr = steps["rtransition"]
        out.append(tr.job(dict(tr.defaults(), ci=True, init="a", final=self.final, kind=",".join(kinds),
                               m1_diffj=False, e2_diffj=False, log="rtransition.log")))
        return out

    def run_dir(self, rid):
        return os.path.join(self.dir, rid)

    def finish(self, rid, kinds):
        """计算成功后：rtransition 的输出改名为 <类型>.ct 并记录。"""
        m = self.manifest()
        run = m["runs"][rid]
        rdir = self.run_dir(rid)
        # GRASP 出错停止时退出码也可能是 0 (如 MPI 版本)，因此检查输出文件是否真的生成
        bio = [f"{r}.{x}" for r in self.sides for x in ("bw", "cbm")]
        missing = [f for f in bio if not os.path.isfile(os.path.join(rdir, f))]
        if missing:
            raise RuntimeError(f"rbiotransform: {', '.join(missing)} not written; see rbiotransform.log")
        run["bio"] = True
        out = os.path.join(rdir, f"a.{self.final}.ct")
        if not os.path.isfile(out):
            self.save_manifest(m)
            raise RuntimeError(f"rtransition: {os.path.basename(out)} not written; see rtransition.log")
        name = "_".join(kinds) + ".ct"
        os.replace(out, os.path.join(rdir, name))
        for k in kinds:
            run["kinds"][k] = name
        self.save_manifest(m)

    def results(self):
        """已算出的全部跃迁 [Transition] (各次计算合并，同一跃迁只保留一个)。"""
        return read_results(self.dir, self.manifest())


def read_results(directory, manifest):
    out = {}
    for rid, run in sorted(manifest.get("runs", {}).items(), key=lambda kv: kv[0] != "all"):
        for name in sorted(set(run["kinds"].values())):
            try:
                for t in parse_ct_cached(os.path.join(directory, rid, name)):
                    out.setdefault((t.kind, t.upper, t.lower), t)
            except (OSError, ValueError):
                pass
    return list(out.values())


def unchanged(stamp):
    path, mtime, size = stamp
    try:
        return os.path.getmtime(path) == mtime and os.path.getsize(path) == size
    except OSError:
        return False


def computed(workdir):
    """工作目录中已算好的跃迁：[({角色: 混合系数文件}, [Transition])]，跳过输入文件已改变的。"""
    root = os.path.join(os.path.abspath(workdir), CACHE_DIR)
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return []
    out = []
    for name in names:
        try:
            with open(os.path.join(root, name, "manifest.json")) as f:
                m = json.load(f)
        except (OSError, ValueError):
            continue
        inputs = m.get("inputs") or {}
        if m.get("version") != MANIFEST_VERSION or not inputs or not all(unchanged(st) for side in inputs.values() for st in side.values()):
            continue
        roles = {role: side["mix"][0] for role, side in inputs.items()}
        out.append((roles, read_results(os.path.join(root, name), m)))
    return out


def link(target, path):
    """软链接 (已是指向同一文件的链接时不动)；不支持软链接的文件系统上改为复制。"""
    if os.path.islink(path):
        if os.readlink(path) == target:
            return
        os.remove(path)
    elif os.path.exists(path):
        os.remove(path)
    try:
        os.symlink(target, path)
    except OSError:
        shutil.copyfile(target, path)


# ---------------------------------------------------------------------------
#  读取 rtransition 的结果 (.ct / .t)
# ---------------------------------------------------------------------------

class Transition:
    """upper / lower 为 (角色 a/b, 2J, 宇称 ±1, 能级序号)；values 为 {规范: (A, gf, S)}，
    电跃迁有 B (Babushkin，长度规范) 和 C (Coulomb，速度规范)，磁跃迁只有 M。"""

    def __init__(self, kind, upper, lower, energy_cm, values):
        self.kind, self.upper, self.lower, self.energy_cm, self.values = kind, upper, lower, energy_cm, values
        self.raw = []              # rtransition 输出中的原始行 (导出 GRASP 文件时原样写回)
        self.section = ()          # 所在段的段头 (类型、分隔线、空行、列标题两行)

    @property
    def wavelength(self):
        """真空波长 (Å)"""
        return 1e8 / self.energy_cm if self.energy_cm else float("inf")

    def main(self):
        """常用的那一组值：电跃迁用 Babushkin 规范，磁跃迁用 M。"""
        return self.values.get("B") or self.values.get("M") or next(iter(self.values.values()))

    def gauge_ratio(self):
        """电跃迁两种规范的比值 A_C / A_B (衡量计算精度)；磁跃迁为 None。"""
        b, c = self.values.get("B"), self.values.get("C")
        return c[0] / b[0] if b and c and b[0] else None


_ct_cache = {}


def parse_ct_cached(path):
    mtime = os.path.getmtime(path)
    hit = _ct_cache.get(path)
    if not hit or hit[0] != mtime:
        hit = _ct_cache[path] = (mtime, parse_ct(path))
    return hit[1]


KIND_RE = re.compile(r"(Electric|Magnetic)\s+2\*\*\(\s*(\d+)\)-pole")


def fnum(s):
    return float(s.strip().replace("D", "E").replace("d", "e"))


def parse_level(role, lev, jlab, jpar, roles):
    j = jlab.strip()
    twoj = int(j[:-2]) if j.endswith("/2") else 2 * int(j)
    return roles[role.strip()], twoj, 1 if jpar.strip() == "+" else -1, int(lev)


def parse_ct(path):
    """读取 rtransition 的输出。各行为固定列宽 (见 printa.f90 的 FORMAT 300-302)：
    能级序号超过 99 时文件标记与序号相连，因此不能按空格切分。"""
    with open(path) as f:
        lines = f.read().splitlines()
    roles = {}
    for ln in lines[:4]:
        m = re.match(r"\s*(f[12]?)\s*=\s*(\S+)", ln)
        if m:
            roles[m.group(1)] = m.group(2)
    if not roles:
        raise ValueError("not an rtransition output file")
    out, kind, cur, section = [], None, None, ()
    for i, ln in enumerate(lines):
        m = KIND_RE.search(ln)
        if m:
            kind = ("E" if m.group(1) == "Electric" else "M") + m.group(2)
            section = tuple(lines[i:i + 5])
            continue
        tag = ln[42:44].strip() if len(ln) >= 83 else ""
        if not kind or tag not in ("C", "B", "M"):
            continue
        vals = (fnum(ln[44:57]), fnum(ln[57:70]), fnum(ln[70:83]))
        if ln[1:3].strip():                  # 新跃迁的第一行
            upper = parse_level(ln[1:3], ln[3:6], ln[7:11], ln[11:15], roles)
            lower = parse_level(ln[15:17], ln[17:20], ln[21:25], ln[25:29], roles)
            cur = Transition(kind, upper, lower, fnum(ln[29:42]), {tag: vals})
            cur.raw, cur.section = [ln], section
            out.append(cur)
        elif cur:                            # 同一跃迁的另一种规范
            cur.values[tag] = vals
            cur.raw.append(ln)
    return out


def kind_key(kind):
    """段的顺序：按多极阶数，同阶时电跃迁在前 (E1, M1, E2, M2, ...)。"""
    return int(kind[1:]), 0 if kind[0] == "E" else 1


def write_grasp(path, transitions, names):
    """把已算的跃迁写成 rtransition 的输出格式，供 rtabtrans1 / rtabtrans2 使用。各行沿用 rtransition 的原始输出，
    只把文件头中计算时用的名称 a、b 换成 names ({角色: 名称}，如 {"a": "even4", "b": "odd4"})：
    rtabtrans1 靠这些名称与输入的态文件 (如 even4.cm) 对应。"""
    if list(names) == ["a"]:
        out = [" Transition in file:", f" f = {names['a']}"]
    else:
        out = [" Transition between files:", f" f1 = {names['a']}", f" f2 = {names['b']}"]
    for kind in sorted({t.kind for t in transitions}, key=kind_key):
        group = [t for t in transitions if t.kind == kind]
        out += ["", ""] + list(group[0].section)
        for t in group:
            out += t.raw
    with open(path, "w") as f:
        f.write("\n".join(out) + "\n")
