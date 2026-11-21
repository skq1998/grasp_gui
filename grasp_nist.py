"""NIST ASD 能级查询、离线导入和能级对应，只用标准库。"""

from collections import Counter, defaultdict
import csv
from dataclasses import dataclass
from datetime import date
import fcntl
from fractions import Fraction
from hashlib import sha256
import io
import json
import math
import os
from pathlib import Path
import re
from threading import Lock
import unicodedata

from grasp_core import T
from grasp_mix import HARTREE_CM
import grasp_session as sessions

ELEMENTS = ("H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn "
            "Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La "
            "Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir Pt Au Hg Tl Pb Bi Po At Rn "
            "Fr Ra Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm Md No Lr Rf Db Sg Bh Hs Mt Ds Rg Cn Nh Fl Mc Lv Ts Og").split()
_NUMERALS = ((100, "C"), (90, "XC"), (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"))
ORBITALS = "spdfghiklmnoqrtuv"
CORES = dict(He="1s2", Ne="1s2.2s2.2p6", Ar="1s2.2s2.2p6.3s2.3p6",
             Kr="[Ar].3d10.4s2.4p6", Xe="[Kr].4d10.5s2.5p6", Rn="[Xe].4f14.5d10.6s2.6p6")
ENDPOINT = "https://physics.nist.gov/cgi-bin/ASD/energy1.pl"
CITATION = ("Kramida, A., Ralchenko, Yu., Reader, J., and NIST ASD Team (2024), "
            "NIST Atomic Spectra Database (v5.12), https://doi.org/10.18434/T4W30F")
MAX_BYTES = 4 * 1024 * 1024
MAX_ROWS = 20000
_request = Lock()


@dataclass(frozen=True)
class Level:
    key: str
    configuration: str
    term: str
    j: str
    twoj: int
    parity: int | None
    energy: float | None
    reported: str
    uncertainty: str
    reference: str


def _roman(stage):
    result = []
    for value, token in _NUMERALS:
        count, stage = divmod(stage, value)
        result.append(token * count)
    return "".join(result)


def spectrum(z, charge):
    if type(z) is int and type(charge) is int and 1 <= z <= len(ELEMENTS) and 0 <= charge < z:
        return f"{ELEMENTS[z - 1]} {_roman(charge + 1)}"
    raise ValueError(T("请输入有效的 NIST 光谱，例如 Li I 或 Fe XV。"))


def normalize_spectrum(text):
    match = re.fullmatch(r"([A-Za-z]{1,2}|[0-9]{1,3})(?:\s+([IVXLCivxlc]+|[0-9]{1,3}\+?))?", text.strip())
    if match:
        element, stage = match.groups()
        if element.isdigit():
            z = int(element)
        elif element.capitalize() in ELEMENTS:
            z = ELEMENTS.index(element.capitalize()) + 1
        else:
            z = 0
        stage = stage or "I"
        if stage.endswith("+"):
            charge = int(stage[:-1])
        elif stage.isdigit():
            charge = max(0, int(stage) - 1)
        else:
            charge = next((i for i in range(z) if _roman(i + 1) == stage.upper()), -1)
        return spectrum(z, charge)
    raise ValueError(T("请输入有效的 NIST 光谱，例如 Li I 或 Fe XV。"))


def _cell(value):
    text = (value or "").strip()
    return text[2:-1].strip() if text.startswith('="') and text.endswith('"') else text


def _configuration(text):
    """只对可完整解析的占据做匹配，保留未能解析的原始标记供界面显示。"""
    text = unicodedata.normalize("NFKC", text).replace(" ", "").replace("·", ".")
    result = {}
    for part in text.rstrip("?").split("."):
        if part.startswith("[") and part.endswith("]") and part[1:-1] in CORES:
            result.update(_configuration(CORES[part[1:-1]]))
        else:
            match = re.fullmatch(r"(\d+[" + ORBITALS + r"])(\d*)", part)
            if match is None:
                return {}
            shell, count = match[1], int(match[2] or 1)
            if not count or count > 4 * ORBITALS.index(shell[-1]) + 2 or shell in result:
                return {}
            result[shell] = count
    return result


def _term(text):
    value = unicodedata.normalize("NFKC", text).strip()
    result = re.fullmatch(r"(\d+[A-Z])(?:[o°*ºᵒ])?\??", value)
    return result[1] if result else ""


def _parity(term, configuration):
    if any(ch in term for ch in "*°ºᵒ") or (_term(term) and term.rstrip("?").endswith("o")):
        return -1
    values = _configuration(configuration)
    if values:
        return 1 - 2 * (sum(ORBITALS.index(shell[-1]) * occ for shell, occ in values.items()) % 2)
    return 1 if _term(term) else None


def _number(text):
    match = re.fullmatch(r"[\s\[(?]*([+-]?(?:\d[\d,\s]*(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?)[\s\])?]*", text)
    if match:
        value = float(re.sub(r"[,\s]", "", match[1]))
        return value if math.isfinite(value) else None
    return None


def _csv_rows(reader):
    try:
        yield from reader
    except csv.Error as error:
        raise ValueError(T("NIST 表格格式有误。")) from error


def parse(raw):
    if len(raw) > MAX_BYTES:
        raise ValueError(T("NIST 数据文件过大。"))
    try:
        source = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        source = raw.decode("latin-1")
    lines = source.splitlines()
    if not lines:
        raise ValueError(T("没有可读取的 NIST 能级。"))
    reader = csv.DictReader(io.StringIO(source), delimiter="\t" if "\t" in lines[0] else ",")
    try:
        columns = {name.casefold().strip(): name for name in reader.fieldnames or () if name}
    except csv.Error as error:
        raise ValueError(T("NIST 表格格式有误。")) from error
    energy_col = next((name for key, name in columns.items()
                       if re.fullmatch(r"level\s*\(cm\s*\^?[-−]1\)",
                                       unicodedata.normalize("NFKC", key).replace("⁻", "−"))), None)
    if not energy_col or "j" not in columns:
        raise ValueError(T("需要包含 J 和 Level (cm-1) 的 NIST CSV 或制表符文件。"))
    levels, keys = [], Counter()
    for row in _csv_rows(reader):
        def read(key):
            return _cell(row.get(columns.get(key)))

        configuration, term, j = read("configuration"), read("term"), read("j")
        raw_energy = _cell(row.get(energy_col))
        if term.casefold() == "limit":
            continue
        try:
            spin = Fraction(j.rstrip("?")) * 2
        except (ValueError, ZeroDivisionError):
            continue
        if spin.denominator != 1 or not 0 <= spin <= 999:
            continue
        energy = _number(raw_energy)
        if energy is None and not raw_energy:
            continue
        reported = read("prefix") + raw_energy + read("suffix")
        fields = (configuration, term, str(spin), reported)
        digest = sha256(json.dumps(fields, ensure_ascii=False).encode()).hexdigest()[:24]
        keys[digest] += 1
        key = f"{digest}:{keys[digest]}"
        uncertainty = next((_cell(row.get(name)) for k, name in columns.items() if k.startswith("uncertainty")), "")
        levels.append(Level(key, configuration, term, j, int(spin), _parity(term, configuration),
                            energy, reported, uncertainty, read("reference")))
        if len(levels) > MAX_ROWS:
            raise ValueError(T("NIST 能级数超过读取上限。"))
    if not levels:
        raise ValueError(T("没有可读取的 NIST 能级。"))
    return levels


def query_url(ion):
    from urllib.parse import urlencode

    params = dict(spectrum=normalize_spectrum(ion), units=0, format=2, output=0, multiplet_ordered=0,
                  conf_out="on", term_out="on", j_out="on", level_out="on", unc_out=1, biblio="on")
    return ENDPOINT + "?" + urlencode(params)


def fetch(ion):
    """仅由桌面按钮调用，使用环境中的 HTTPS 代理并拒绝重定向。"""
    import ssl
    from urllib.request import (HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener,
                                getproxies_environment)

    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None

    if not _request.acquire(blocking=False):
        raise ValueError(T("已有一次 NIST 请求在进行。"))
    try:
        proxy = getproxies_environment().get("https")
        opener = build_opener(ProxyHandler({"https": proxy} if proxy else {}),
                              NoRedirect(), HTTPSHandler(context=ssl.create_default_context()))
        request = Request(query_url(ion), headers={"User-Agent": "GRASP GUI (NIST ASD energy levels)"})
        with opener.open(request, timeout=15) as response:
            raw = response.read(MAX_BYTES + 1)
        parse(raw)
        return raw
    finally:
        _request.release()


def _cache_path(ion):
    root = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return root / "grasp_gui" / "nist" / (normalize_spectrum(ion).replace(" ", "-") + ".json")


def read_data(path):
    with open(path, "rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    return raw, parse(raw)


def save_cache(ion, raw, imported=False):
    levels = parse(raw)
    path = _cache_path(ion)
    metadata = dict(ion=normalize_spectrum(ion), date=date.today().isoformat(), imported=bool(imported))
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        sessions.atomic(path, json.dumps(dict(metadata, table=raw.decode("latin-1")), ensure_ascii=False).encode())
    except OSError as error:
        metadata["warning"] = str(error)
    return metadata, levels


def load_cache(ion):
    path = _cache_path(ion)
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_BYTES * 3 + 1)
        payload = json.loads(raw)
        if (payload["ion"] != normalize_spectrum(ion) or type(payload["imported"]) is not bool
                or not isinstance(payload["table"], str)):
            raise ValueError("Invalid NIST cache")
        date.fromisoformat(payload["date"])
        levels = parse(payload.pop("table").encode("latin-1"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError, KeyError, TypeError, RecursionError):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass              # 损坏的缓存已经弃用；只读目录中不必强行删除。
        return None
    return payload, levels


def calculated_id(relative_path, row):
    return json.dumps([relative_path, row["twoj"], row["parity"], row["pos"]],
                      ensure_ascii=False, separators=(",", ":"))


def _match_path(workdir):
    return Path(workdir) / ".grasp_gui" / "nist-matches.json"


def load_matches(workdir):
    """配对由用户创建，损坏时保留原件。"""
    try:
        payload = json.loads(_match_path(workdir).read_bytes())
        spectra = payload["spectra"]
        if type(payload["version"]) is not int or payload["version"] != 1 or not isinstance(spectra, dict):
            raise ValueError
        for pairs in spectra.values():
            if not isinstance(pairs, dict):
                raise ValueError
            for pair in pairs.values():
                if (not isinstance(pair["experimental"], str) or not isinstance(pair["component"], str)
                        or type(pair["energy"]) not in (int, float) or not math.isfinite(pair["energy"])):
                    raise ValueError
        return spectra
    except FileNotFoundError:
        return {}
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError) as error:
        raise ValueError(T("手动配对文件 nist-matches.json 格式有误，已保留原件。")) from error


def _component(row):
    return row.get("lsj_label") or row.get("jj") or ""


def match_overrides(calculated, pairs):
    """失效的手动配对保留计算态标识，等待用户处理。"""
    overrides = {}
    for row in calculated:
        if pair := pairs.get(row["id"]):
            current = pair["energy"] == row["energy"] and pair["component"] == _component(row)
            overrides[row["id"]] = pair["experimental"] if current else None
    return overrides


def save_match(workdir, ion, row, experimental):
    path = _match_path(workdir)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    # 配对使用独立的锁，计算进行中也能写入。
    fd = os.open(path.with_suffix(".lock"), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        spectra = load_matches(workdir)
        pairs = spectra.get(ion, {})
        identity = row["id"]
        if experimental is None:
            if identity not in pairs:
                return pairs
            del pairs[identity]
        else:
            if any(key != identity and pair["experimental"] == experimental for key, pair in pairs.items()):
                raise ValueError(T("该 NIST 能级已被手动配对到另一计算态。"))
            pairs[identity] = dict(experimental=experimental, energy=row["energy"], component=_component(row))
        if pairs:
            spectra[ion] = pairs
        else:
            del spectra[ion]
        raw = json.dumps(dict(version=1, spectra=spectra), ensure_ascii=False, indent=1).encode("utf-8")
        sessions.atomic(path, raw)
        return pairs
    finally:
        os.close(fd)


def _same_configuration(known, calculated):
    if not known or not calculated or any(calculated.get(shell) != value for shell, value in known.items()):
        return False
    if any(not re.fullmatch(r"\d+[" + ORBITALS + r"]", shell) for shell in calculated):
        return False
    order = lambda shell: (int(shell[:-1]), ORBITALS.index(shell[-1]))
    first = min(map(order, known))
    # ASD 的组态可省略更内层的满壳，未声明的外层占据不能忽略。
    return all(order(shell) < first and value == 4 * ORBITALS.index(shell[-1]) + 2
               for shell, value in calculated.items() if shell not in known)


def _score(calculated, nist):
    left, right = dict(calculated.get("full") or ()), _configuration(nist.configuration)
    calc_term, nist_term = _term(calculated.get("term") or ""), _term(nist.term)
    if ((left and right and not _same_configuration(right, left))
            or (calc_term and nist_term and calc_term != nist_term)):
        return 0
    return 1 + 2 * bool(left and right) + bool(calc_term and nist_term)


def compare(calculated, experimental, *, manual=None, reference=None):
    """同 J、同宇称时按已知组态和谱项排序；并列或重用候选留给人工配对。"""
    by_j = defaultdict(list)
    for lvl in experimental:
        by_j[lvl.twoj].append(lvl)
    overrides = manual or {}
    origin = next((row["energy"] for row in calculated if row["id"] == reference),
                  min((row["energy"] for row in calculated), default=0))
    experimental_origin = min((row.energy for row in experimental if row.energy is not None), default=0)
    pairs, reserved = [], Counter()
    for row in calculated:
        choices = [lvl for lvl in by_j[row["twoj"]] if lvl.parity in (None, row["parity"])]
        chosen = next((lvl for lvl in choices if lvl.key == overrides.get(row["id"])), None)
        if chosen:
            reserved[chosen.key] += 1
        pairs.append([row, choices, chosen])

    automatic = Counter()
    for pair in pairs:
        row, choices, picked = pair
        if picked or row["id"] in overrides:
            continue
        eligible = [lvl for lvl in choices if lvl.parity == row["parity"] and not reserved[lvl.key]]
        scores = sorted(((_score(row, nist), nist) for nist in eligible), key=lambda item: -item[0])
        if scores and scores[0][0] > 0 and (len(scores) == 1 or scores[0][0] > scores[1][0]):
            automatic[scores[0][1].key] += 1
            # 后续仍须处理两个计算态争用同一实验能级的情况。
            pair[2] = scores[0][1]

    results = []
    for row, choices, picked in pairs:
        is_manual = row["id"] in overrides
        rank = reserved if is_manual else automatic
        if picked is not None and rank[picked.key] != 1:
            picked = None
        status = ("manual" if is_manual else "automatic") if picked else (
            "ambiguous" if is_manual or choices else "unmatched")
        value = (row["energy"] - origin) * HARTREE_CM
        known = picked.energy - experimental_origin if picked and picked.energy is not None else None
        delta = value - known if known is not None else None
        error = delta / abs(known) if known else None
        results.append(dict(calculated=row, experimental=picked, candidates=choices, status=status,
                            calculated_cm=value, experimental_cm=known, delta_cm=delta, relative_error=error))
    return results
