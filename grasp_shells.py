"""离线基态组态、电子占据和 rcsfgenerate 参数构造。"""

import base64
from dataclasses import dataclass
from functools import lru_cache
import gzip
import json
import re

from grasp_core import T
from grasp_shell_data import PAYLOAD

ELEMENTS = ("H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn "
            "Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La "
            "Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir Pt Au Hg Tl Pb Bi Po At Rn "
            "Fr Ra Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm Md No Lr Rf Db Sg Bh Hs Mt Ds Rg Cn Nh Fl Mc Lv Ts Og").split()
ORBITALS = "spdfghiklmn"
REF_ORBITALS = "spdfghikl"
MAX_N = 15
CORES = (("", 0), ("He", 2), ("Ne", 10), ("Ar", 18), ("Kr", 36), ("Xe", 54), ("Rn", 86))
CORE_ELECTRONS = dict(CORES, Cd=48, Hg=80)


@dataclass(frozen=True)
class Ground:
    occupations: tuple
    term: str = ""
    ionization_ev: str = ""
    uncertainty_ev: str = ""
    references: str = ""
    estimated: bool = False


class ShellError(ValueError):
    pass


@lru_cache(maxsize=1)
def snapshot():
    data = json.loads(gzip.decompress(base64.b85decode(PAYLOAD)))
    rows = {(row[0], row[1]): tuple(row[2:]) for row in data.pop("rows")}
    return data, rows


def element(value):
    text = str(value).strip()
    if text.isdecimal():
        z = int(text)
        if 1 <= z <= len(ELEMENTS):
            return z
    elif text.capitalize() in ELEMENTS:
        return ELEMENTS.index(text.capitalize()) + 1
    raise ShellError(T("请输入有效的元素符号或原子序数 (1–118)。"))


def orbitals(max_n=MAX_N):
    return [(n, l) for n in range(1, max_n + 1) for l in range(min(n, len(REF_ORBITALS)))]


def label(shell):
    n, l = shell
    return str(n) + ORBITALS[l]


def capacity(shell):
    return 4 * shell[1] + 2


def madelung(electrons):
    """缺失数据时按 n+l 和 n 的次序填充，不推算能级项和电离能。"""
    occup = {}
    for shell in sorted(orbitals(8), key=lambda nl: (sum(nl), nl[0])):
        if not electrons:
            break
        count = min(capacity(shell), electrons)
        occup[shell] = count
        electrons -= count
    return occup


def _occupations(shells):
    """解析 NIST Ground Shells，包括按表注定义的闭壳层简写。"""
    result = {}
    for part in shells.split("."):
        if part.startswith("["):
            result.update(madelung(CORE_ELECTRONS[part[1:-1]]))
        else:
            match = re.fullmatch(r"([1-9][0-9]?)([spdfghikl])([0-9]*)", part)
            n, letter, count = match.groups()
            result[(int(n), ORBITALS.index(letter))] = int(count or 1)
    return result


def ground(z, charge):
    z = element(z)
    if type(charge) is not int or not 0 <= charge <= z:
        raise ShellError(T("离化度须为 0 到原子序数。"))
    found = snapshot()[1].get((z, charge))
    if found is not None:
        shells, term, energy, uncertainty, refs = found
        return Ground(tuple(sorted(_occupations(shells).items())), term, energy, uncertainty, refs)
    return Ground(tuple(sorted(madelung(z - charge).items())), estimated=charge < z)


def format_term(term):
    """用通用字体可显示的字符表示项符号与 J。"""
    term = term.replace("*", "°")
    match = re.fullmatch(r"(.*?)(?:<([0-9]+(?:/[0-9]+)?)>|([0-9]+))", term)
    if match is None:
        return term
    symbol, bracketed, integer = match.groups()
    j = bracketed or integer
    return symbol + j if symbol else f"J = {j}"


def format_ionization(value, uncertainty):
    """保留 ASD 给出的有效数字，并标注括号代表的数据来源。"""
    if not value:
        return "—"
    source = ""
    if value.startswith("(") and value.endswith(")"):
        source = T("理论值")
    elif value.startswith("[") and value.endswith("]"):
        source = T("半经验值")
    if source:
        value = value[1:-1].strip()
    energy = f"{value} ± {uncertainty}" if uncertainty else value
    return energy + " eV" + (f" · {source}" if source else "")


def parity(occupations):
    return "-" if sum(l * count for (_, l), count in occupations.items()) % 2 else "+"


def relativistic(shell, occupancy):
    """图示先填 j=l−1/2 的子壳，NIST 非相对论数据并不指定这种分配。"""
    n, l = shell
    name = label(shell)
    if not l:
        return [(name, occupancy, 2)]
    lower = min(2 * l, occupancy)
    return [(name + "-", lower, 2 * l), (name, occupancy - lower, 2 * l + 2)]


def transfer(occupations, source, target):
    if source == target:
        return dict(occupations)
    if not occupations.get(source) or occupations.get(target, 0) >= capacity(target):
        raise ShellError(T("来源壳层须有电子，目标壳层须有空位。"))
    changed = dict(occupations)
    changed[source] -= 1
    if not changed[source]:
        del changed[source]
    changed[target] = changed.get(target, 0) + 1
    return changed


def closed_core(code):
    try:
        if str(int(code)) == str(code) and 0 <= int(code) < len(CORES):
            return madelung(CORES[int(code)][1])
    except (TypeError, ValueError):
        pass
    raise ShellError(T("请选择有效的闭壳芯。"))


def core_suggestion(references):
    for code, (_, electrons) in reversed(list(enumerate(CORES))):
        core = madelung(electrons)
        if all(sum(occ.values()) > electrons and all(occ.get(shell) == count for shell, count in core.items())
               for occ in references):
            return str(code)
    return "0"


def active_set(text):
    active = {}
    for token in re.split(r"[\s,]+", text.strip()):
        match = re.fullmatch(r"([1-9][0-9]?)([spdfghiklmn])", token)
        if match is None:
            raise ShellError(T("活跃轨道集须列出各 l 的最高 n，例如 5s,4p,3d。"))
        n, letter = int(match[1]), match[2]
        l = ORBITALS.index(letter)
        if not l < n <= MAX_N or l in active:
            raise ShellError(T("活跃轨道须满足 l < n ≤ 15，每个 l 只写一次。"))
        active[l] = n
    return active


def default_active(configurations, core):
    active = {}
    for occ in configurations:
        for n, l in occ:
            if (n, l) not in core:
                active[l] = max(n, active.get(l, 0))
    return ",".join(label((n, l)) for l, n in sorted(active.items()))


def build_fields(references, num_electrons, core, active, jmin, jmax, excitations):
    """按宇称生成列表；每个参考可以独立选择最小占据和激发标记。"""
    core_occ = closed_core(core)
    active = active_set(active)
    max_n, max_l = max(active.values()), max(active)
    # rcsfexcitation.f90:80–97,253–265 将 Xe、Rn 的一部分加回组态行。
    extra_shells, extra_chars, core_n = {"5": (3, 22, 5), "6": (4, 30, 6)}.get(str(core), (0, 0, 0))
    if max_n < core_n:
        raise ShellError(T("活跃集的最大 n 须至少为 {n}，以容纳闭壳芯。", n=core_n))
    if (type(jmin) is not int or type(jmax) is not int or not 0 <= jmin <= jmax
            or jmin % 2 != num_electrons % 2 or jmax % 2 != num_electrons % 2):
        raise ShellError(T("2J 范围须递增，且上下限的奇偶性须与电子数一致。"))
    if type(excitations) is not int or abs(excitations) > num_electrons - sum(core_occ.values()):
        raise ShellError(T("激发数的绝对值不能超过芯外电子数。"))
    if not references:
        raise ShellError(T("至少需要一个参考组态。"))
    by_parity = {}
    for occupations, markers in references:
        if sum(occupations.values()) != num_electrons or any(
                not (1 <= n <= MAX_N and 0 <= l < min(n, len(REF_ORBITALS))
                     and type(count) is int and 0 < count <= capacity((n, l)))
                for (n, l), count in occupations.items()):
            raise ShellError(T("参考组态须保留电子总数，且每个壳层需满足占据容量。"))
        if any(occupations.get(shell) != count for shell, count in core_occ.items()):
            raise ShellError(T("所选闭壳芯在每个参考组态中都必须满占据。"))
        parts = []
        for shell, count in sorted(occupations.items()):
            if shell in core_occ:
                continue
            marker = markers.get(shell, "i")
            if marker not in ("i", "*") and (not re.fullmatch(r"[0-9]{1,2}", str(marker))
                                             or not 0 <= int(marker) <= count):
                raise ShellError(T("最少占据数不能大于参考组态的占据数：{shell}", shell=label(shell)))
            n, l = shell
            if n > max_n or l > max_l:
                raise ShellError(T("活跃集没有覆盖参考组态的壳层：{shell}", shell=label(shell)))
            parts.append(f"{label(shell)}({count},{marker})")
        line = "".join(parts)
        if not parts or len(parts) + extra_shells > 15 or len(line) + extra_chars > 135:
            raise ShellError(T("每个参考须有芯外电子，GRASP 组态行最多容纳 15 个壳层和 135 个字符。"))
        by_parity.setdefault(parity(occupations), []).append(line)
    orbital_text = ",".join(label((n, l)) for l, n in sorted(active.items()))
    # 预留第 100 行给空的分隔行；否则 GRASP 不会读到列表结束。
    lists = [dict(configs="\n".join(rows[start:start + 99]), active=orbital_text,
                  jmin=str(jmin), jmax=str(jmax), nexc=str(excitations))
             for rows in by_parity.values() for start in range(0, len(rows), 99)]
    return dict(core=str(core), lists=lists)
