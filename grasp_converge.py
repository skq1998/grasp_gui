"""按主成分跟踪有序轨道层的能级，计算激发能、增量和精细结构。"""

from collections import defaultdict
import csv
from dataclasses import dataclass
import io
import math
import os
from pathlib import Path

from grasp_core import T
import grasp_index as index
import grasp_mix as mix
import grasp_summary as summary
from grasp_trans import two_j

MAX_LAYERS = 50
MAX_PER_LAYER = 2000
DEFAULT_THRESHOLD = 1.0
STATUSES = {
    "converged": "已收敛", "converging": "收敛中", "unconverged": "未收敛",
    "changed": "主成分改变", "unmatched": "无法对应",
}
REASONS = {"insufficient": "层数不足", "zero_previous": "上一次增量为零", "alternating": "增量变号",
           "increasing": "未进入收敛区", "zero_current": "当前增量为零", "missing_reference": "参考能级缺失"}
SCF_STATUS = {"converged": "已收敛", "unconverged": "未收敛", "unknown": "无法判断",
              "missing": "无日志", "unreadable": "无法读取日志"}


@dataclass(frozen=True)
class State:
    j: str
    parity: int
    twoj: int
    block: int
    position: int
    energy: float
    primary: tuple
    components: tuple
    purity: float
    lsj: str = ""
    term: str = ""
    configuration: tuple = ()

    @property
    def symmetry(self):
        return self.twoj, self.parity


@dataclass(frozen=True)
class Layer:
    file: str
    levels: tuple
    scf: dict
    truncated: bool = False


def ordered_files(directory, jobs, group, *, path_check=None):
    root = Path(directory)
    files = []
    for row in summary.result_order(jobs):
        name = row["file"]
        if row["group"] == group and name.endswith((".m", ".cm")):
            path = root / name
            if path_check:
                path = path_check(str(path))
            if path.is_file():
                files.append(name)
    return files


def _log_for(files, jobs):
    output = {row["file"]: jobs[row["step"] - 1] for row in summary.result_order(jobs)}
    log_uses = defaultdict(int)
    for job in jobs:
        if job.get("log"):
            log_uses[job["log"]] += 1
    # 被后面的步骤覆盖的日志不能用作较早层的收敛证据。
    return {name: output[name]["log"] for name in files if name in output
            and output[name].get("step") == "rmcdhf" and log_uses[output[name].get("log")] == 1}


def _read_scf(root, log, path_check, cancel):
    if not log:
        return dict(missing=True)
    path = root / log
    if path_check:
        path = path_check(str(path))
    if not path.is_file():
        return dict(missing=True, file=Path(log).name)
    try:
        return dict(file=Path(log).name, **summary.scf_log(path, cancel))
    except (OSError, ValueError) as error:
        return dict(file=Path(log).name, error=str(error))


def _norm(text):
    return " ".join(text.split())


def _state(lv, names, components, lsj, core):
    texts = tuple((_norm(names[i - 1][0]), _norm(names[i - 1][1])) for i, _ in components if i - 1 in names)
    primary = texts[0] if texts else ("", "")
    label = max(lsj[1], key=lambda row: row[1])[2] if lsj and lsj[1] else ""
    configuration, term = mix.parse_lsj_label(label) if label else (None, None)
    config = dict(core)
    config.update(configuration or mix.nonrel_config(primary[0]))
    return State(lv.J, lv.parity, two_j(lv.block), lv.block.nb, lv.pos, lv.energy, primary, texts,
                 100 * components[0][1] ** 2 if components else 0, label, term or "", tuple(sorted(config.items())))


def read_layers(directory, files, *, jobs=(), limit=MAX_PER_LAYER, path_check=None, cancel=None):
    if not 1 <= len(files) <= MAX_LAYERS or len(files) != len(set(files)):
        raise ValueError(T("请选择 1–50 个不同的轨道层文件。"))
    if type(limit) is not int or not 1 <= limit <= MAX_PER_LAYER:
        raise ValueError(T("每层能级上限须为 1–2000。"))
    root = Path(directory)
    logs = _log_for(files, jobs)
    layers = []
    for filename in files:
        index.checkpoint(cancel)
        path = root / filename
        if path_check:
            path = path_check(str(path))
        saved = index.stamp(path)
        blocks = mix.open_mix(str(path), summary=True, k=3, cancel=cancel)
        csf_path = mix.find_csf_for(str(path), cancel, path_check=path_check)
        _, labels = mix.find_lsj_for(str(path), blocks, path_check=path_check)
        csf_saved = index.stamp(csf_path) if csf_path else None
        csfs = mix.open_csfs(csf_path, cancel) if csf_path else None
        core = mix.csf_core(csf_path) if csf_path else []
        sorted_levels = sorted((lv for block in blocks for lv in block.levels), key=lambda lv: lv.energy)
        selected = {id(lv) for lv in sorted_levels[:limit]}
        levels = []
        for block in blocks:
            index.checkpoint(cancel)
            # 系数为零的 CSF 不参与匹配，即使它被索引列在前三项中。
            top = [(lv, [(i, coeff) for i, coeff in lv.top_components(3) if coeff != 0])
                   for lv in block.levels if id(lv) in selected]
            names = csfs[block.nb - 1].read_many([i - 1 for _, comps in top for i, _ in comps], cancel) if csfs else {}
            levels.extend(_state(lv, names, comps, labels.get((block.nb, lv.pos)), core) for lv, comps in top)
        index.unchanged(saved)
        if csf_saved:
            index.unchanged(csf_saved)
        layers.append(Layer(filename, tuple(levels), _read_scf(root, logs.get(filename), path_check, cancel),
                            len(sorted_levels) > limit or blocks.partial))
    return layers


def _group(states, candidate, identity):
    groups = defaultdict(list)
    for i in candidate:
        key = identity(states[i])
        if key is not None:
            groups[(states[i].symmetry, key)].append(i)
    return groups


def _matches(before, after):
    left, right = set(range(len(before))), set(range(len(after)))
    matches = []

    def match(a, b, ambiguous=False):
        matches.append((a, b, ambiguous))
        left.remove(a)
        right.remove(b)

    def by_identity(identity, allow_ties):
        a, b = _group(before, left, identity), _group(after, right, identity)
        for key in sorted(set(a) & set(b)):
            aa, bb = a[key], b[key]
            ambiguous = max(len(aa), len(bb)) > 1
            if ambiguous and not allow_ties:
                continue
            aa.sort(key=lambda i: (before[i].energy, i))
            bb.sort(key=lambda i: (after[i].energy, i))
            for i, j in zip(aa, bb):
                match(i, j, ambiguous)

    # 有唯一 LSJ 时可消除同一主 CSF 中的多项歧义，再处理只靠主 CSF 的匹配。
    by_identity(lambda s: (s.primary, s.lsj) if s.primary[0] and s.lsj else None, False)
    by_identity(lambda s: s.lsj or None, False)
    by_identity(lambda s: s.primary if s.primary[0] else None, True)

    # 主项互换时只接受双向唯一的主要成分证据。
    by_after = _group(after, right, lambda s: s.primary if s.primary[0] else None)
    alternatives, reverse = defaultdict(list), defaultdict(list)
    for a in sorted(left):
        state = before[a]
        for other in state.components[1:]:
            for b in by_after[(state.symmetry, other)]:
                if state.primary in after[b].components[1:] and b not in alternatives[a]:
                    alternatives[a].append(b)
                    reverse[b].append(a)
    for a, choices in alternatives.items():
        if len(choices) == 1 and len(reverse[choices[0]]) == 1:
            match(a, choices[0])
    return matches


def _tracks(layers):
    tracks, previous = [], {}
    for layer_number, layer in enumerate(layers):
        current = {}
        if layer_number:
            for a, b, ambiguous in _matches(layers[layer_number - 1].levels, layer.levels):
                row = previous[a]
                row["states"][layer_number] = layer.levels[b]
                row["ambiguous"] |= ambiguous
                current[b] = row
        for i, state in enumerate(layer.levels):
            if i not in current:
                row = dict(id=f"{layer_number + 1}:{state.block}:{state.position}",
                           states=[None] * len(layers), ambiguous=False)
                row["states"][layer_number] = state
                current[i] = row
                tracks.append(row)
        previous = current
    return tracks


def _increments(values, total, scales=1):
    points = []
    previous_delta = None
    for i, value in enumerate(values):
        prior = values[i - 1] if i else None
        delta = (value - prior) * scales if value is not None and prior is not None else None
        previous_total = total[i - 1] if i else None
        total_delta = (total[i] - previous_total) * mix.HARTREE_CM if (
            total[i] is not None and previous_total is not None) else None
        ratio = delta / previous_delta if delta is not None and previous_delta else None
        if ratio is not None and not math.isfinite(ratio):
            ratio = None
        remainder = delta * ratio / (1 - ratio) if ratio is not None and 0 < ratio < 1 else None
        if delta is None or previous_delta is None:
            reason = "insufficient"
        elif previous_delta == 0:
            reason = "zero_previous"
        elif ratio is None or ratio >= 1:
            reason = "increasing"
        elif ratio < 0:
            reason = "alternating"
        elif not ratio:
            reason = "zero_current"
        else:
            reason = None
        points.append(dict(delta_cm=delta, total_delta_cm=total_delta, ratio=ratio, remainder_cm=remainder,
                           reason=reason))
        previous_delta = delta
    return points


def _values(row, zeros, threshold):
    states = row["states"]
    totals = [state.energy if state else None for state in states]
    excitation = [(energy - zero) if energy is not None and zero is not None else None
                  for energy, zero in zip(totals, zeros)]
    points = _increments(excitation, totals, mix.HARTREE_CM)
    previous = None
    for state, total, ex, zero, point in zip(states, totals, excitation, zeros, points):
        component_changed = bool(state and previous and (
            state.primary[0] and previous.primary[0] and state.primary != previous.primary
            or state.lsj and previous.lsj and state.lsj != previous.lsj))
        point.update(total_hartree=total, excitation_cm=ex * mix.HARTREE_CM if ex is not None else None,
                     purity_percent=state.purity if state else None, primary=state.primary if state else None,
                     lsj=state.lsj if state else "", changed=component_changed)
        if state and zero is None:
            point["reason"] = "missing_reference"
        if state is None or previous is None or point["delta_cm"] is None:
            status = "unmatched"
        elif component_changed:
            status = "changed"
        elif abs(point["delta_cm"]) < threshold:
            status = "converged"
        elif point["remainder_cm"] is not None:
            status = "converging"
        else:
            status = "unconverged"
        point["status"] = status
        previous = state
    first = next(state for state in states if state)
    return dict(id=row["id"], j=first.j, parity=first.parity, primary=first.primary, lsj=first.lsj,
                ambiguous=row["ambiguous"], status=points[-1]["status"], points=points)


def _splittings(layers, limit):
    rests = defaultdict(lambda: [None] * len(layers))
    for k, layer in enumerate(layers):
        grouped = defaultdict(lambda: defaultdict(list))
        for state in layer.levels:
            if state.term and state.configuration:
                grouped[(state.configuration, state.term, state.parity)][state.twoj].append(state)
        for (conf, term, parity), group in grouped.items():
            unique = sorted(j for j, options in group.items() if len(options) == 1)
            for a, b in zip(unique, unique[1:]):
                low, high = group[a][0], group[b][0]
                rests[(conf, term, parity, low.j, high.j)][k] = (high.energy - low.energy) * mix.HARTREE_CM
    return [dict(configuration=conf, term=term, parity=parity, lower_j=a, higher_j=b, splitting_cm=values,
                 points=_increments(values, [None] * len(layers)))
            for (conf, term, parity, a, b), values in sorted(rests.items())[:limit]], len(rests) > limit


def analyze(layers, *, threshold=DEFAULT_THRESHOLD, reference=None, limit=MAX_PER_LAYER):
    if not layers:
        raise ValueError(T("请选择 1–50 个不同的轨道层文件。"))
    if type(threshold) not in (float, int) or not math.isfinite(threshold) or threshold <= 0:
        raise ValueError(T("收敛阈值须为正的有限数值。"))
    if type(limit) is not int or not 1 <= limit <= MAX_PER_LAYER:
        raise ValueError(T("每层能级上限须为 1–2000。"))
    tracks = _tracks(layers)
    if reference is None:
        zeros = [min((state.energy for state in layer.levels), default=None) for layer in layers]
    else:
        anchor = next((row for row in tracks if row["id"] == reference), None)
        if anchor is None:
            raise ValueError(T("所选参考能级已不可用。"))
        zeros = [state.energy if state else None for state in anchor["states"]]
    splittings, truncated = _splittings(layers, limit)
    return dict(layers=[dict(file=item.file, scf=item.scf, truncated=item.truncated) for item in layers],
                rows=[_values(row, zeros, threshold) for row in tracks[:limit]], splittings=splittings,
                reference=reference, threshold_cm=threshold, zeros_hartree=zeros,
                truncated=truncated or len(tracks) > limit or any(layer.truncated for layer in layers))


def _scf_rows(result):
    """两种报告写出相同的最近迭代样本和各层的日志状态。"""
    for layer in result["layers"]:
        scf = layer["scf"]
        state = {True: "converged", False: "unconverged", None: "unknown"}[scf.get("converged")]
        if scf.get("missing"):
            state = "missing"
        elif "error" in scf:
            state = "unreadable"
        for item in scf.get("last_iterations") or ({},):
            yield (layer["file"], scf.get("file", ""), state, scf.get("iterations"), item.get("iteration"),
                   item.get("delta_weighted_energy_hartree"), item.get("delta_optimized_energy_hartree"))


def csv_text(result):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(["Track", "J", "Parity", "File", "Total energy (Hartree)", "Excitation (cm^-1)",
                     "Increment (cm^-1)", "Total increment (cm^-1)", "Ratio", "Remainder (cm^-1)",
                     "Purity (%)", "Main CSF", "Coupling", "LSJ", "Status", "Reason", "Ambiguous"])
    for row in result["rows"]:
        for layer, point in zip(result["layers"], row["points"]):
            writer.writerow([row["id"], row["j"], "+" if row["parity"] > 0 else "-", layer["file"],
                             *[point[name] for name in ("total_hartree", "excitation_cm", "delta_cm",
                                                        "total_delta_cm", "ratio", "remainder_cm", "purity_percent")],
                             *(point["primary"] or ("", "")), point["lsj"], point["status"], point["reason"],
                             row["ambiguous"]])
    if result["splittings"]:
        writer.writerow([])
        writer.writerow(["Configuration", "Term", "Parity", "Lower J", "Higher J", "File", "Splitting (cm^-1)",
                         "Increment (cm^-1)", "Ratio", "Remainder (cm^-1)", "Reason"])
        for row in result["splittings"]:
            configuration = " ".join(f"{orbital}^{occ}" for orbital, occ in row["configuration"])
            for layer, energy, point in zip(result["layers"], row["splitting_cm"], row["points"]):
                writer.writerow([configuration, row["term"], "+" if row["parity"] > 0 else "-",
                                 row["lower_j"], row["higher_j"], layer["file"], energy, point["delta_cm"],
                                 point["ratio"], point["remainder_cm"], point["reason"]])
    writer.writerow([])
    writer.writerow(["File", "SCF log", "SCF status", "Iterations", "Iteration",
                     "Weighted SCF increment (Hartree)", "Single-level SCF increment (Hartree)"])
    writer.writerows(_scf_rows(result))
    writer.writerow([])
    writer.writerow(["Threshold (cm^-1)", "Reference track", "Truncated"])
    writer.writerow([result["threshold_cm"], result["reference"] or "lowest per layer", result["truncated"]])
    return stream.getvalue()


def markdown(result):
    def emit(values):
        return "| " + " | ".join(str(val).replace("|", "\\|").replace("\n", " ") for val in values) + " |"

    def value(x):
        return "—" if x is None else f"{x:.6g}"

    layers = [layer["file"] for layer in result["layers"]]
    columns = ["J^π", T("主要成分"), *layers, T("总能量增量 (cm⁻¹)"), "r", T("剩余量 (cm⁻¹)"),
               T("纯度 (%)"), T("状态"), T("原因")]
    reference = result["reference"] or T("各层最低能级")
    caption = T("阈值 {n} cm⁻¹，参考能级 {ref}", n=value(result["threshold_cm"]), ref=reference)
    lines = ["# " + T("收敛") + " · " + T("激发能与增量 (cm⁻¹)"), "", caption]
    if result["truncated"]:
        lines.append(T("受数据或行数上限影响，部分结果未列出。"))
    lines.extend(["", emit(columns), emit(["---"] * len(columns))])
    for row in result["rows"]:
        values = [value(point["excitation_cm"]) + " / " + value(point["delta_cm"]) for point in row["points"]]
        last = row["points"][-1]
        state = T(STATUSES[row["status"]])
        if row["ambiguous"]:
            state += " · " + T("对应不唯一")
        reason = T(REASONS[last["reason"]]) if last["reason"] else "—"
        lines.append(emit([row["j"] + ("+" if row["parity"] > 0 else "-"), row["lsj"] or row["primary"][0],
                           *values, value(last["total_delta_cm"]), value(last["ratio"]), value(last["remainder_cm"]),
                           value(last["purity_percent"]), state, reason]))
    if result["splittings"]:
        columns = [T("组态"), T("谱项"), "J^π", *layers, "r", T("剩余量 (cm⁻¹)"), T("原因")]
        lines.extend(["", "## " + T("精细结构分裂及增量 (cm⁻¹)"),
                      "", emit(columns), emit(["---"] * len(columns))])
        for row in result["splittings"]:
            parity = "+" if row["parity"] > 0 else "-"
            pair = f"{row['lower_j']}{parity} → {row['higher_j']}{parity}"
            config = " ".join(f"{orbital}^{occ}" for orbital, occ in row["configuration"])
            values = [value(energy) + " / " + value(point["delta_cm"])
                      for energy, point in zip(row["splitting_cm"], row["points"])]
            last = row["points"][-1]
            lines.append(emit([config, row["term"], pair, *values, value(last["ratio"]),
                               value(last["remainder_cm"]), T(REASONS[last["reason"]]) if last["reason"] else "—"]))
    columns = [T("文件"), T("日志文件"), T("状态"), T("总迭代次数"), T("迭代序号"),
               T("加权 SCF 能量增量 (Hartree)"), T("单能级 SCF 能量增量 (Hartree)")]
    lines.extend(["", "## " + T("SCF 收敛情况"), "", emit(columns), emit(["---"] * len(columns))])
    for filename, log, state, iterations, iteration, weighted, single in _scf_rows(result):
        lines.append(emit([filename, log or "—", T(SCF_STATUS[state]), value(iterations), value(iteration),
                           value(weighted), value(single)]))
    return "\n".join(lines) + "\n"
