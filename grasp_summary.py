"""给 agent 的有界结果摘要和逐步文件预检；只读结果，不执行计算，不导入 Qt。"""

from collections import deque
import copy
import datetime
import difflib
import errno
import fnmatch
import json
from pathlib import Path
import re
import struct

import grasp_bg as bg
import grasp_config as config
import grasp_core as core
import grasp_index as index
import grasp_mix as mix
from grasp_core import T

MAX_BYTES = 200 * 1024
SECTIONS = dict(levels="能级与主要成分", layer_changes="能级随层的变化", scf="SCF 收敛情况",
                csfs="各态的 CSF 数", run_status="各步运行状态", configuration="当前完整配置")
OPTIONAL = {"configuration"}        # 结果摘要为主；完整配置是附加的一部分，默认不选
STEPS = {s.key: s for s in core.STEPS}
NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[EeDd][-+]?\d+)?"


def page(job):
    step = STEPS.get(job.get("step"))
    if step is None or not isinstance(job.get("vals"), dict):
        return None, None
    values = config.job_values(job)
    return step, values


def check_files(workdir, jobs, start=0, run=None, offset=0, path_check=None):
    """检查选中步骤；workdir=None 只检查参数和派生名称，不据文件存在与否判断配置。"""
    root = Path(workdir) if workdir is not None else None
    available, removed, origins = set(), set(), {}
    missing, skipped, invalid = [], [], []
    names = {p.name for p in root.iterdir() if p.is_file()} if root is not None else set()
    reported = set()

    def require(name, number, title, errors):
        for error in errors:
            if error["kind"] != "name" or not name:
                continue
            value = error["name"]
            related = name == value if error["unit"] == "bytes" else (
                Path(name).name == value or Path(name).name.startswith(value + "."))
            origin = origins.get(name)
            if related and origin and origin["step"] != number:
                key = origin["step"], number, error["field"], value
                if key not in reported:
                    reported.add(key)
                    invalid.append(dict(error, **origin, file=name,
                                        required_by=dict(step=number, program=title, field=error["field"])))
        if root is None:
            return
        if not name:
            found = False
        else:
            try:
                if path_check:
                    path_check(str(root / name))
                found = any(fnmatch.fnmatchcase(p, name) for p in available)
                if not found:
                    candidates = [Path(name)] if Path(name).is_absolute() else root.glob(name)
                    found = any((path_check(str(p)) if path_check else p).is_file() and
                                (str(p) if Path(name).is_absolute() else str(p.relative_to(root))) not in removed
                                for p in candidates)
            except OSError as e:
                if e.errno != errno.ENAMETOOLONG:
                    raise
                found = False  # 超过文件系统限制的输入也不存在；名称问题仍在同一报告中。
        if not found:
            near = difflib.get_close_matches(name, sorted(names | available), n=3, cutoff=0.6) if name else []
            missing.append(dict(kind="missing", step=number, program=title,
                                file=name or T("（未指定文件）"), similar=near, reason=T("缺少输入文件")))

    def produced(name, origin):
        if root is not None and path_check:
            path_check(str(root / name))
        available.add(name)
        removed.discard(name)
        origins[name] = origin

    for number, job in enumerate(jobs[start:], start + offset + 1):
        step, values = page(job)
        title = (step.prog if step else "") or job.get("prog") or job.get("title", "")
        errors = []
        if step is not None:
            try:
                step.validate(values)
            except ValueError as e:
                row = dict(getattr(e, "details", dict(kind="validation", field=None, reason=str(e))),
                           step=number, program=title)
                errors.append(row)
                invalid.append(row)
        try:
            inputs = step.inputs(values, run) if step else None
            if inputs is None:
                skipped.append(dict(step=number, program=title, reason=T("未声明文件依赖，无法检查")))
                continue
            pre = job["pre"] if "pre" in job else step.pre(values)
            post = job["post"] if "post" in job else step.post(values)
            outputs, removes = step.outputs(values, run) or [], step.removes(values)
        except ValueError as e:
            if not errors:
                invalid.append(dict(kind="validation", step=number, program=title, field=None, reason=str(e)))
            continue
        origin = dict(step=number, program=title, field=", ".join(step.output_fields) or "outputs")
        for action in pre:
            if action[0] == "cp":
                require(action[1], number, title, errors)
                produced(action[2], origin)
        for name in inputs:
            require(name, number, title, errors)
        for name in outputs:
            produced(name, origin)
        for action in post:
            if action[0] == "cp":
                require(action[1], number, title, errors)
                produced(action[2], origin)
        for name in removes:
            available.discard(name)
            origins.pop(name, None)
            removed.add(name)
    invalid.sort(key=lambda r: r["step"])
    issues = sorted(invalid + missing, key=lambda r: r["step"])
    return dict(missing=missing, skipped=skipped, invalid=invalid, issues=issues)


def check_text(result):
    lines = [T("流程预检发现以下问题：")]
    for row in result["invalid"]:
        if "required_by" in row:
            consumer = row["required_by"]
            lines.append(T("第 {n} 步 {p} 的 {field} 写出 {file}，会使第 {next} 步 {consumer} 的 {target} 无法运行：{why}",
                           n=row["step"], p=row["program"], field=row["field"], file=row["file"],
                           next=consumer["step"], consumer=consumer["program"], target=consumer["field"],
                           why=row["reason"]))
        else:
            lines.append(T("第 {n} 步 {p} 的 {field}：{why}", n=row["step"], p=row["program"],
                           field=row.get("field") or T("参数"), why=row["reason"]))
    for row in result["missing"]:
        lines.append(T("第 {n} 步 {p}：缺少 {f}；未找到已声明的前序输出，请核对文件名、运行起点或未检查的步骤。",
                       n=row["step"], p=row["program"], f=row["file"]))
        if row["similar"]:
            lines.append(T("相近文件名：{names}", names=", ".join(row["similar"])))
    for row in result["skipped"]:
        lines.append(T("第 {n} 步 {p}：{why}", n=row["step"], p=row["program"], why=row["reason"]))
    return "\n".join(lines)


def job_key(job):
    return json.dumps(config.export_job(job), sort_keys=True, ensure_ascii=False)


def locate(pipeline, jobs):
    positions = [i for i in range(len(pipeline) - len(jobs) + 1)
                 if jobs and all(a is b for a, b in zip(pipeline[i:i + len(jobs)], jobs))]
    if len(positions) == 1:
        return positions[0]
    expected = [job_key(j) for j in jobs]
    keys = [job_key(j) for j in pipeline]
    starts = [i for i in range(len(keys) - len(expected) + 1) if expected and keys[i:i + len(expected)] == expected]
    return starts[0] if len(starts) == 1 else None


def run_identity(state):
    return state.get("start"), state.get("pid"), tuple(state.get("titles", []))


def run_status(jobs, workdir, history=()):
    rows = [dict(step=i + 1, program=j.get("title", ""), status="unknown", basis=T("没有可对应的运行记录"))
            for i, j in enumerate(jobs)]
    keys = [job_key(j) for j in jobs]
    records = []
    state = bg.status(workdir)
    if state:
        titles = state.get("titles", [])
        current = [j.get("title", "") for j in jobs]
        starts = [i for i in range(len(jobs) - len(titles) + 1) if titles and current[i:i + len(titles)] == titles]
        if len(starts) == 1 and not any(run_identity(r["state"]) == run_identity(state) for r in history):
            i = starts[0]
            records.append(dict(keys=keys[i:i + len(titles)], first=i, state=state,
                                basis=T("最近后台运行状态；按唯一连续标题序列对应，未保存参数指纹")))
    for saved in history:
        record = copy.deepcopy(saved)
        if state and run_identity(record["state"]) == run_identity(state):
            record["state"] = state
        records.append(record)
    records.sort(key=lambda r: r["state"].get("start", ""))
    for record in records:
        first, expected, st = record["first"], record["keys"], record["state"]
        current = st.get("step", 0)
        result = st.get("status")
        for n, key in enumerate(expected):
            if first + n >= len(keys) or keys[first + n] != key:
                continue
            status = "not_run"
            if result == "ok" or n < current:
                status = "success"
            elif n == current:
                status = {"running": "running", "failed": "failed", "stopped": "stopped"}.get(result, "unknown")
            rows[first + n].update(status=status, basis=record["basis"], start=st.get("start"), end=st.get("end"))
    return rows


def number(value):
    return float(value.lower().replace("d", "e"))


def scf_log(path, cancel=None):
    """9006157 的 scf/improv/newco 输出；正常结束本身不是收敛证据，须排除迭代耗尽。"""
    samples = deque(maxlen=3)
    current = None
    count = 0
    exceeded = complete = False
    before = index.stamp(path)
    with open(path, encoding="utf-8", errors="replace") as f:
        while True:
            index.checkpoint(cancel)
            line = f.readline(65536)
            if not line:
                break
            match = re.search(r"Iteration number\s+(\d+)", line)
            if match:
                if current is not None:
                    samples.append(current)
                count = int(match[1])
                current = dict(iteration=count, block_average_energies_hartree=[], optimized_levels=[],
                               max_printed_self_consistency=None)
            if "Maximum iterations in SCF Exceeded" in line or "Convergence not obtained" in line:
                exceeded = True
            if re.search(r"RMCDHF(?:_MEM)?(?:_MPI)?: Execution complete\.", line, re.I):
                complete = True
            if current is None:
                continue
            match = re.search(r"Weighted average energy of these levels\s*=\s*(" + NUMBER + ")", line)
            if match:
                current["weighted_energy_hartree"] = number(match[1])
            match = re.search(r"Average energy\s*=\s*(" + NUMBER + ")", line)
            if match:
                if len(current["block_average_energies_hartree"]) < 50:
                    current["block_average_energies_hartree"].append(number(match[1]))
                else:
                    current["omitted_block_averages"] = current.get("omitted_block_averages", 0) + 1
            match = re.search(r"Level\s+(\d+|\*+)\s+Energy\s*=\s*(" + NUMBER +
                              r")\s+Weight\s*=\s*(" + NUMBER + ")", line)
            if match:
                if len(current["optimized_levels"]) < 50:
                    current["optimized_levels"].append(dict(index=int(match[1]) if match[1].isdigit() else None,
                                                            energy_hartree=number(match[2]), weight=number(match[3])))
                else:
                    current["omitted_optimized_levels"] = current.get("omitted_optimized_levels", 0) + 1
            if re.match(r"\s*\d+[spdfghiklmnoqrtuvwxyz]-?\s", line):
                parts = line.split()
                if len(parts) >= 6:
                    try:
                        value = abs(number(parts[4]))
                        current["max_printed_self_consistency"] = max(
                            current["max_printed_self_consistency"] or 0, value)
                    except ValueError:
                        pass  # 非轨道数值行不参与统计，不据此推断收敛。
    if current is not None:
        samples.append(current)
    unchanged = before == index.stamp(path)
    previous = previous_single = None
    for row in samples:
        energy = row.get("weighted_energy_hartree")
        if energy is not None and previous is not None:
            row["delta_weighted_energy_hartree"] = energy - previous
        previous = energy
        levels = row["optimized_levels"]
        single = levels[0]["energy_hartree"] if len(levels) == 1 and not row.get("omitted_optimized_levels") else None
        if single is not None and previous_single is not None:
            row["delta_optimized_energy_hartree"] = single - previous_single
        previous_single = single
    converged = False if exceeded else True if complete and count and unchanged else None
    return dict(converged=converged,
                iterations=count, completed=complete, changed_while_reading=not unchanged,
                last_iterations=list(samples),
                basis="compas/grasp 9006157: scf.f90:154-235,292-295; improv.f90:173-185; newco.f90:116-128")


def rmcdhf_sum(path):
    """rmcdhf 的总结文件 (rmcdhf.sum，rsave 改名为 name.sum) 中每个轨道最终的自洽程度和广义占据数。
    格式依据 compas/grasp 9006157 的 endsum.f90:44-60,74-79 (FORMAT 302、304：1X,I2,A2 后接数值)；
    文件只有迭代结束后才写这两张表，没有迭代过程。"""
    orbitals, table = {}, None
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if line.startswith("Radial wavefunction summary"):
                table, orbitals = "radial", {}
            elif "Generalised" in line:
                table = "occupation"
            elif table and re.match(r" [ \d]\d[a-z][ -] ", line):
                name, parts = line[1:3].strip() + line[3:5].strip(), line[5:].split()
                try:
                    if table == "radial" and len(parts) == 7:
                        orbitals[name] = dict(name=name, self_consistency=abs(number(parts[5])))
                    elif table == "occupation" and len(parts) == 6 and name in orbitals:
                        orbitals[name]["occupation"] = number(parts[5])
                except ValueError:
                    continue
    if not orbitals:
        raise ValueError(T("{f} 中没有轨道总结 (rmcdhf 可能没有正常结束)", f=Path(path).name))
    rows = list(orbitals.values())
    return dict(sum_file=Path(path).name, orbitals=rows,
                max_final_self_consistency=max(r["self_consistency"] for r in rows),
                basis="compas/grasp 9006157: endsum.f90:44-60,74-79")


def result_order(jobs):
    ordered = []
    for i, job in enumerate(jobs):
        step, values = page(job)
        for name in step.result_files(values) if step else []:
            ordered.append(dict(file=name, group=job.get("group"), step=i + 1))
    # 同名文件再次写出时，旧层的能量已经不存在，不能把新结果伪装成旧结果。
    last = {r["file"]: i for i, r in enumerate(ordered)}
    return [r for i, r in enumerate(ordered) if last[r["file"]] == i]


def add_sum(row, root, values, group, earlier):
    """加入 .sum 中最终的自洽程度和广义占据数，标出比同一态组上一次 rmcdhf 新增的轨道。
    新增轨道的占据数小，说明这一层的贡献已经不大；没有日志时，最终自洽程度是唯一的收敛线索。"""
    state = values["save"].strip()
    path = root / ((state + ".sum") if state else "rmcdhf.sum")
    if not path.is_file():
        row["sum"] = dict(sum_file=path.name, missing=True)
        return
    try:
        data = rmcdhf_sum(path)
    except (OSError, ValueError) as e:
        row["sum"] = dict(sum_file=path.name, error=str(e))
        return
    result = path.with_suffix(".m") if state else root / "rmix.out"
    if result.is_file() and result.stat().st_mtime - path.stat().st_mtime > 300:
        data["possibly_stale"] = True          # 总结文件比混合系数文件早得多，可能是上一次计算留下的
    previous = next((r for r in reversed(earlier) if r.get("group") == group and "orbitals" in r.get("sum", {})), None)
    if previous is not None:
        known = {o["name"] for o in previous["sum"]["orbitals"]}
        for o in data["orbitals"]:
            o["new"] = o["name"] not in known
    row["group"] = group
    row["sum"] = data
    if row.get("converged") is None and not row["log"]:
        row["reason"] = T("无日志，迭代过程未知；最终自洽程度见 .sum")


def collect(workdir, cfg, jobs, history=(), per_block=5, cancel=None, progress=None):
    if type(per_block) is not int or not 1 <= per_block <= 200:
        raise ValueError(T("每块能级数须为 1–200"))
    root = Path(workdir)
    index.clean_cache(str(root))
    result = dict(configuration=cfg, run_status=run_status(jobs, root, history),
                  scf=[], csfs=[], levels=[], warnings=[])
    logs = {j.get("log"): i for i, j in enumerate(jobs) if j.get("log")}
    for i, job in enumerate(jobs):
        step, values = page(job)
        if not isinstance(step, core.RMcdhf):
            continue
        row = dict(step=i + 1, program=job.get("title"), log=job.get("log"), converged=None)
        if not row["log"]:
            row["reason"] = T("无日志，无法判断")
        elif logs[row["log"]] != i:
            row["reason"] = T("该日志由后续步骤复用，无法判断此步的收敛情况")
        else:
            try:
                row.update(scf_log(root / row["log"], cancel))
            except OSError as e:
                row["reason"] = str(e)
        if result["run_status"][i]["status"] == "running":
            row["converged"] = None
            row["reason"] = T("此步仍在运行，收敛尚无法确定")
        else:
            add_sum(row, root, values, job.get("group"), result["scf"])
        result["scf"].append(row)
    for path in sorted(root.glob("*.c")):
        index.checkpoint(cancel)
        try:
            meta = index.csf_index(path, cancel, progress)
            result["csfs"].append(dict(file=path.name, blocks=[dict(J_parity=b["label"], count=b["ncf"])
                                                            for b in meta["blocks"]]))
        except (OSError, ValueError) as e:
            result["warnings"].append(dict(file=path.name, error=str(e)))
    ordered = result_order(jobs)
    known = {r["file"] for r in ordered}
    ordered += [dict(file=n, group=None, step=None) for n in mix.find_mix_files(str(root)) if n not in known]
    for item in ordered:
        path = root / item["file"]
        if not path.is_file():
            result["levels"].append(dict(item, levels=[], missing=True, omitted_levels=0))
            continue
        index.checkpoint(cancel)
        try:
            saved = index.stamp(path)
            blocks = mix.open_mix(path, summary=True, k=3, cancel=cancel, progress=progress)
            csf = mix.find_csf_for(str(path), cancel, progress)
            csfs = mix.open_csfs(csf, cancel, progress) if csf else None
            _, labels = mix.find_lsj_for(str(path), blocks)
            rows = []
            for block in blocks:
                selected = block.levels[:per_block]
                positions = {i - 1 for lv in selected for i, _ in lv.top_components(3)}
                texts = csfs[block.nb - 1].read_many(positions, cancel) if csfs else {}
                for lv in selected:
                    top = lv.top_components(3)
                    rows.append(dict(J=lv.J, parity=lv.parity, index=lv.pos, energy_hartree=lv.energy,
                                     purity_percent=100 * top[0][1] ** 2 if top else None,
                                     components=[dict(csf=i, coefficient=c, weight=c*c,
                                                      configuration=texts.get(i - 1, (None, None))[0],
                                                      coupling=texts.get(i - 1, (None, None))[1]) for i, c in top],
                                     lsj=labels.get((block.nb, lv.pos))))
            index.unchanged(saved)
            result["levels"].append(dict(item, levels=rows, partial=blocks.partial,
                                         omitted_levels=sum(max(0, len(b.levels) - per_block) for b in blocks)))
        except (OSError, ValueError, IndexError, StopIteration, struct.error) as e:
            result["warnings"].append(dict(file=path.name, error=str(e)))
    energies = [r["energy_hartree"] for layer in result["levels"] for r in layer["levels"]]
    if energies:
        minimum = min(energies)
        for layer in result["levels"]:
            for row in layer["levels"]:
                row["relative_cm"] = (row["energy_hartree"] - minimum) * mix.HARTREE_CM
    return result


def layer_changes(layers):
    groups = {}
    for layer in layers:
        if layer["group"] is not None and layer["step"] is not None:
            groups.setdefault(layer["group"], []).append(layer)
    rows = []
    for group, ordered in groups.items():
        keys = {(r["J"], r["parity"], r["index"]) for layer in ordered for r in layer["levels"]}
        for key in sorted(keys):
            previous = None
            values = []
            for layer in ordered:
                found = next((r for r in layer["levels"] if (r["J"], r["parity"], r["index"]) == key), None)
                energy = found["energy_hartree"] if found else None
                values.append(dict(file=layer["file"], step=layer["step"], energy_hartree=energy,
                                   delta_cm=(energy - previous) * mix.HARTREE_CM
                                   if energy is not None and previous is not None else None,
                                   matched=found is not None))
                previous = energy
            rows.append(dict(group=group, J=key[0], parity=key[1], index=key[2], layers=values))
    return rows


def render(data, sections=None, max_bytes=MAX_BYTES):
    selected = set(SECTIONS if sections is None else sections)
    if not selected or selected - SECTIONS.keys():
        raise ValueError(T("请至少选择一个摘要部分"))
    data = copy.deepcopy(data)
    omissions = []

    def text():
        energies = [r["energy_hartree"] for layer in data["levels"] for r in layer["levels"]]
        zero = min(energies) if energies else None
        for layer in data["levels"]:
            for row in layer["levels"]:
                row["relative_cm"] = (row["energy_hartree"] - zero) * mix.HARTREE_CM
        lines = ["# GRASP GUI / " + T("结果摘要"), "",
                 "```json", json.dumps(dict(generated_at=datetime.datetime.now().isoformat(timespec="seconds"),
                                             omissions=omissions, warnings=data["warnings"],
                                             relative_zero_hartree=zero,
                                             notes=T("运行成功仅指流程结束；收敛按日志判断；层间按 J、宇称和序号对应，不保证物理态身份相同；同名输出只保留最后写入。")),
                                       ensure_ascii=False, allow_nan=False, indent=1), "```", ""]
        for key, label in SECTIONS.items():
            if key in selected:
                value = layer_changes(data["levels"]) if key == "layer_changes" else data[key]
                lines += ["## " + T(label), "", "```json",
                          json.dumps(value, ensure_ascii=False, allow_nan=False, indent=1), "```", ""]
        return "\n".join(lines)

    out = text()
    if len(out.encode("utf-8")) <= max_bytes:
        return out
    if "levels" in selected:
        for layer in data["levels"]:
            for row in layer["levels"]:
                row.pop("components", None)
        omissions.append(T("为满足大小上限，省略主要成分"))
    out = text()
    omitted_files = []
    while len(out.encode("utf-8")) > max_bytes and len(data["levels"]) > 1:
        position = next((i for i, r in enumerate(data["levels"]) if r["step"] is None), 0)
        old = data["levels"].pop(position)
        data["scf"] = [r for r in data["scf"] if r["step"] != old["step"]]
        state = Path(old["file"]).stem
        if not any(Path(r["file"]).stem == state for r in data["levels"]):
            data["csfs"] = [r for r in data["csfs"] if r["file"] != state + ".c"]
        if not omitted_files:
            omissions.append(dict(reason=T("为满足大小上限，省略较早或未关联的结果"), files=omitted_files,
                                  sections=["levels", "layer_changes", "scf", "csfs"]))
        omitted_files.append(old["file"])
        out = text()
    if len(out.encode("utf-8")) > max_bytes:
        raise ValueError(T("摘要仍超过 {n} KB，请减少每块能级数或取消部分内容；完整配置可单独导出。", n=max_bytes // 1024))
    return out
