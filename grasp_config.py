"""
共享配置：把一个计算 (各步的参数和流程) 保存为一个 JSON 文件，在别的机器上导入后从头计算。
GRASP2018 手册算例 (demos/*.json) 也采用这个格式。只用标准库，格式说明见 docs/CONFIG_FORMAT.md。

    format, version         固定为 FORMAT 和配置格式的版本 (VERSION)；旧版本的文件读入时由 upgrade 逐版转换
    created_by              导出时的界面版本，如 "GRASP GUI 2.1.0"
    package                 计算所用的程序包，目前只支持 "GRASP2018"
    title / summary / runtime   字符串，或按语言给出的 {"zh": …, "en": …}
    author, created         作者、日期 (可选)；id, order 用于随程序提供的示例
    jobs                    按顺序组成流程：
                              {"step": 程序页, "group": "odd"/"even" (按态组保存参数的程序页), "vals": 表单参数}
                              {"command": 任务}   旧版本建立的流程中无法还原为表单参数的步骤，原样保存
    files                   附带的小文本文件 [{"name", "text"}]，导入时写入工作目录
    reference               参考结果 [{"file": 混合系数文件, "energies": [能级能量 (Hartree)]}]

表单参数只保存计算本身的设置；GRASP bin 目录、MPI 进程数等与机器有关的设置不放进配置。导入时由当前版本的
规则重新生成各程序的输入，因此生成规则修正后，旧的配置也能得到正确的输入。
"""

import json
import os
import re
import stat
import sys
import tempfile

import grasp_core as core
import grasp_session as sessions
from grasp_core import T

FORMAT = "grasp-gui-config"
VERSION = 2                    # 1：GUI 1.3 / 2.0 的示例格式 (demo_format = 1)
PACKAGES = ("GRASP2018",)
INPUTS_FILE = ".grasp_gui_inputs.json"
MAX_FILE = 1 << 20             # 附带文件：每个最多 1 MB，合计最多 MAX_FILES_TOTAL
MAX_FILES_TOTAL = 4 << 20
MAX_CONFIG = MAX_FILES_TOTAL * 8    # 给附带文本的 JSON 转义和流程参数留出空间
MAX_CLIPBOARD = MAX_FILES_TOTAL     # 大文本可改存文件，避免阻塞桌面剪贴板
DEMO_DIR = os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__))), "demos")

STEPS = {s.key: s for s in core.STEPS}


class ConfigError(ValueError):
    """无法导入的配置 (格式不对、版本太新、程序页不存在等)。"""


def text(cfg, key):
    """title / summary / runtime 按当前界面语言取值。"""
    d = cfg.get(key, "")
    return (d.get(core.LANG) or d.get("zh") or d.get("en", "")) if isinstance(d, dict) else d


# ---------------------------------------------------------------------------
#  读取：升级旧版本、检查
# ---------------------------------------------------------------------------

def upgrade(doc):
    """把旧版本的配置逐版转换为当前版本 (不修改 doc)；无法识别时抛出 ConfigError。"""
    if not isinstance(doc, dict):
        raise ConfigError(T("不是 GRASP GUI 的配置文件"))
    doc = dict(doc)
    if doc.get("demo_format") == 1 and "format" not in doc:            # 版本 1：示例格式
        doc.pop("demo_format")
        doc.update(format=FORMAT, version=1)
    if doc.get("format") != FORMAT:
        raise ConfigError(T("不是 GRASP GUI 的配置文件"))
    version = doc.get("version")
    if not isinstance(version, int) or version < 1:
        raise ConfigError(T("配置文件的版本号有误"))
    if version > VERSION:
        raise ConfigError(T("配置文件的格式 (版本 {v}) 比本程序新，请更新 GRASP GUI", v=version))
    if version == 1:
        doc.setdefault("package", "GRASP2018")
        doc["version"] = version = 2
    return doc


def check(doc, *, validate=True):
    """升级并检查配置：返回 (配置, 警告列表)；无法导入时抛出 ConfigError。
    缺少的参数用默认值补上，不认识的参数忽略 (都列入警告)。
    validate=False 仅供预检先检查结构，再逐步汇总校验问题；导入与生成仍用默认严格模式。"""
    cfg = upgrade(doc)
    warnings = []
    package = cfg.get("package", "GRASP2018")
    if package not in PACKAGES:
        raise ConfigError(T("配置使用的程序包 {p} 本程序不支持 (支持：{supported})", p=package, supported=", ".join(PACKAGES)))
    jobs = cfg.get("jobs")
    if not isinstance(jobs, list) or not jobs:
        raise ConfigError(T("配置中没有计算步骤"))
    for i, job in enumerate(jobs, 1):
        if not isinstance(job, dict):
            raise ConfigError(T("第 {i} 步的格式有误", i=i))
        if "command" in job:
            c = job["command"]
            if not isinstance(c, dict) or not isinstance(c.get("prog"), str) or not c["prog"].strip():
                raise ConfigError(T("第 {i} 步的格式有误", i=i))
            continue
        step = STEPS.get(job.get("step"))
        if step is None:
            raise ConfigError(T("第 {i} 步的程序页 {step} 不存在", i=i, step=job.get("step")))
        if step.key in core.GROUP_STEPS and job.get("group", core.GROUPS[0]) not in core.GROUPS:
            raise ConfigError(T("第 {i} 步的态组 {g} 不存在", i=i, g=job.get("group")))
        vals = job.get("vals")
        if not isinstance(vals, dict):
            raise ConfigError(T("第 {i} 步的格式有误", i=i))
        keys = {f.key for f in step.fields if isinstance(f, core.Field)}
        unknown = sorted(set(vals) - keys)
        missing = sorted(keys - set(vals) - {f.key for f in step.fields if getattr(f, "added", False)})
        if unknown:
            warnings.append(T("第 {i} 步 ({step}) 中不认识的参数已忽略：{k}", i=i, step=step.key, k=", ".join(unknown)))
        if missing:
            warnings.append(T("第 {i} 步 ({step}) 缺少的参数用默认值：{k}", i=i, step=step.key, k=", ".join(missing)))
        try:
            if validate:
                step.validate(job_values(job))
        except ValueError as e:
            raise ConfigError(T("第 {i} 步 ({step})：{e}", i=i, step=step.key, e=e)) from e
    total = 0
    for f in cfg.get("files", []):
        name = f.get("name") if isinstance(f, dict) else None
        if not valid_file_name(name) or not isinstance(f.get("text"), str):
            raise ConfigError(T("附带文件 {f} 的名称或内容有误", f=name))
        size = len(f["text"].encode("utf-8"))
        total += size
        if size > MAX_FILE or total > MAX_FILES_TOTAL:
            raise ConfigError(T("附带文件太大 ({f})", f=name))
    return cfg, warnings


def valid_file_name(name):
    """附带文件只能是工作目录中的普通文件名 (不含目录，不以 . 开头)。"""
    return (isinstance(name, str) and name and name == os.path.basename(name) and not name.startswith(".")
            and "\\" not in name and "\0" not in name)


def loads(content):
    """读取配置文本；文件与剪贴板共用检查，接受单个 Markdown 代码块。"""
    if not isinstance(content, str):
        raise ConfigError(T("不是 GRASP GUI 的配置文件"))
    if len(content.encode("utf-8")) > MAX_CONFIG:
        raise ConfigError(T("配置文本太大 (最多 {n} MB)", n=MAX_CONFIG >> 20))
    content = content.strip()
    match = re.fullmatch(r"```(?:json)?[ \t]*\r?\n(.*?)\r?\n[ \t]*```", content, re.S | re.I)
    if match:
        content = match.group(1).strip()
    try:
        doc = json.loads(content)
    except (ValueError, RecursionError) as e:
        raise ConfigError(T("配置文本不是有效的 JSON：{e}", e=e)) from e
    return check(doc)


def load(path):
    """读取并检查配置文件；与文本导入共用大小限制和格式检查。"""
    try:
        with open(path, "rb") as f:
            raw = f.read(MAX_CONFIG + 1)
        content = raw.decode("utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise ConfigError(T("无法读取 {p}：{e}", p=os.path.basename(path), e=e)) from e
    return loads(content)


def list_demos(path=DEMO_DIR):
    """随程序提供的示例，按 order 排序；无法读取或检查不通过的文件跳过。"""
    out = []
    for name in sorted(os.listdir(path)) if os.path.isdir(path) else []:
        if name.endswith(".json"):
            try:
                out.append(load(os.path.join(path, name))[0])
            except ConfigError:
                continue
    return sorted(out, key=lambda d: (d.get("order", 99), d.get("id", "")))


# ---------------------------------------------------------------------------
#  导入：配置 -> 工作目录记录
# ---------------------------------------------------------------------------

def job_values(job):
    """step 任务的完整表单参数 (缺少的用默认值，按字段类型转换)。"""
    step = STEPS[job["step"]]
    return {f.key: core.normalize(f, job["vals"].get(f.key, f.default)) for f in step.fields
            if isinstance(f, core.Field)}


def make_job(job):
    """配置中的一步 -> 流程中的任务 (由当前版本的规则生成程序输入)。"""
    if "command" in job:
        c = job["command"]
        return dict(title=c.get("title") or c["prog"], prog=c["prog"], args=list(c.get("args", [])),
                    stdin=c.get("stdin", ""), log=c.get("log", ""),
                    pre=[tuple(a) for a in c.get("pre", [])], post=[tuple(a) for a in c.get("post", [])],
                    mpi=bool(c.get("mpi", False)))
    step = STEPS[job["step"]]
    vals = job_values(job)
    out = step.job(vals)
    if step.key in core.GROUP_STEPS:
        out["group"] = job.get("group") or core.GROUPS[0]
    return out


def session(cfg):
    """配置对应的目录记录：流程，以及各程序页显示该程序最后一次使用的参数。"""
    forms, groups = {}, {g: {} for g in core.GROUPS}
    pipeline = [make_job(j) for j in cfg["jobs"]]
    for j in cfg["jobs"]:
        if "step" not in j:
            continue
        if j["step"] in core.GROUP_STEPS:
            groups[j.get("group") or core.GROUPS[0]][j["step"]] = job_values(j)
        else:
            forms[j["step"]] = job_values(j)
    first = next((j for j in cfg["jobs"] if "step" in j), {})
    group = next((j["group"] for j in cfg["jobs"] if j.get("step") in core.GROUP_STEPS and j.get("group")), core.GROUPS[0])
    return dict(steps=forms, groups=groups, group=group,
                pipeline=pipeline, tab=first.get("step", core.STEPS[0].key))


def programs(cfg):
    """配置用到的程序 (不含 cp)。"""
    progs = []
    for job in session(cfg)["pipeline"]:
        for p in [job["prog"]] + [a[1][0] for a in job["pre"] + job["post"] if a[0] == "run"]:
            if p not in progs and p != "cp":
                progs.append(p)
    return progs


def missing_programs(cfg, bindir):
    """配置用到、但在 bin 目录和 PATH 中都找不到的程序。"""
    return [p for p in programs(cfg) if not core.program_path(bindir, p)]


def custom_commands(cfg):
    """自定义的命令 (“自定义”程序页和原样保存的步骤)：可以运行任意程序，导入前应让用户看过。
    返回 [(步骤序号, shell 写法)]。"""
    out = []
    for i, j in enumerate(cfg["jobs"], 1):
        if "command" in j or j.get("step") == "custom":
            out.append((i, core.describe(make_job(j))))
    return out


def load_inputs(workdir):
    """缺少输入清单的旧目录返回 None；损坏的清单要报错，不能把输入误当结果。"""
    path = os.path.join(workdir, INPUTS_FILE)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, "rb") as f:
        if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
            raise ConfigError(T("附带输入清单的格式有误"))
        return parse_inputs(f.read())


def parse_inputs(raw):
    """按同一份字节解析清单，供预览后的过期检查使用。"""
    try:
        names = json.loads(raw)
    except (ValueError, RecursionError) as e:
        raise ConfigError(T("附带输入清单的格式有误")) from e
    if not isinstance(names, list) or not all(valid_file_name(n) for n in names):
        raise ConfigError(T("附带输入清单的格式有误"))
    return set(names)


def save_inputs(workdir, names):
    """清单只记录基名，原子替换避免写到一半被当作完整清单读取。"""
    names = list(names)
    if not all(valid_file_name(n) for n in names):
        raise ConfigError(T("附带输入清单的格式有误"))
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=workdir, prefix=".grasp-inputs-", delete=False) as f:
            temporary = f.name
            f.write((json.dumps(sorted(set(names)), ensure_ascii=False, indent=1) + "\n").encode("utf-8"))
        os.replace(temporary, os.path.join(workdir, INPUTS_FILE))
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def install(cfg, workdir):
    """在 workdir (不存在或为空) 中写入附带文件和目录记录；目录非空时抛出 FileExistsError。"""
    with sessions.locked(workdir):
        return _install(cfg, workdir)


def _install(cfg, workdir):
    cfg, _ = check(cfg)
    if os.path.isdir(workdir) and os.listdir(workdir):
        raise FileExistsError(T("目录不是空的：{p}", p=workdir))
    os.makedirs(workdir, exist_ok=True)
    for f in cfg.get("files", []):
        with open(os.path.join(workdir, f["name"]), "w", encoding="utf-8") as out:
            out.write(f["text"])
    if not core.save_session(workdir, session(cfg)):
        raise OSError(T("无法写入 {p}", p=os.path.join(workdir, core.SESSION_FILE)))
    save_inputs(workdir, (f["name"] for f in cfg.get("files", [])))


# ---------------------------------------------------------------------------
#  导出：工作目录记录 -> 配置
# ---------------------------------------------------------------------------

def export_job(job):
    """流程中的任务 -> 配置中的一步：记录了程序页和参数的任务保存参数，否则原样保存命令。"""
    if job.get("step") in STEPS and isinstance(job.get("vals"), dict):
        out = dict(step=job["step"], vals=job["vals"])
        if job["step"] in core.GROUP_STEPS:
            out["group"] = job.get("group") or core.GROUPS[0]
        return out
    keys = ("title", "prog", "args", "stdin", "log", "pre", "post", "mpi")
    return dict(command={k: job[k] for k in keys if k in job})


def read_text_file(path):
    """附带文件的内容；不是 UTF-8 文本或太大时抛出 ConfigError。"""
    name = os.path.basename(path)
    if not valid_file_name(name):
        raise ConfigError(T("附带文件 {f} 的名称或内容有误", f=name))
    if os.path.getsize(path) > MAX_FILE:
        raise ConfigError(T("{f} 太大 (附带文件最多 {n} MB)", f=name, n=MAX_FILE >> 20))
    try:
        with open(path, encoding="utf-8") as f:
            content = f.read()
    except UnicodeDecodeError:
        raise ConfigError(T("{f} 不是文本文件", f=name))
    if "\0" in content:
        raise ConfigError(T("{f} 不是文本文件", f=name))
    return content


def reference_energies(path):
    """混合系数文件中各能级的能量 (Hartree)，作为参考结果。"""
    import grasp_mix
    return [round(lv.energy, 10) for b in grasp_mix.open_mix(path) for lv in b.levels]


def export(pipeline, meta, files=(), references=(), *, validate=True):
    """由流程生成配置。meta：title、summary、author 等；files、references 为文件路径。"""
    cfg = dict(format=FORMAT, version=VERSION, created_by=f"GRASP GUI {core.VERSION}", package="GRASP2018")
    cfg.update({k: v for k, v in meta.items() if v})
    cfg["jobs"] = [export_job(j) for j in pipeline]
    if files:
        cfg["files"] = [dict(name=os.path.basename(p), text=read_text_file(p)) for p in files]
    if references:
        cfg["reference"] = [dict(file=os.path.basename(p), energies=reference_energies(p)) for p in references]
    check(cfg, validate=validate)
    return cfg


ABS_PATH = re.compile(r"(^|[\s=,'\"])(/|~/)\S")


def portability(cfg):
    """导出前提醒：在别的机器上可能出问题的地方。返回说明列表。"""
    notes = []
    raw = [i for i, j in enumerate(cfg["jobs"], 1) if "command" in j]
    if raw:
        notes.append(T("第 {steps} 步由旧版本加入流程，没有记录表单参数，只能按原样的命令导出；"
                       "在程序页中重新“加入流程”可以改为保存参数。", steps=", ".join(map(str, raw))))
    for i, j in enumerate(cfg["jobs"], 1):
        values = j["command"].values() if "command" in j else j["vals"].values()
        found = [v for v in values for s in (v if isinstance(v, list) else [v])
                 if isinstance(s, str) and ABS_PATH.search(s)]
        if found:
            notes.append(T("第 {i} 步含有绝对路径，在别的机器上可能不存在", i=i))
    custom = [i for i, _ in custom_commands(cfg)]
    if custom:
        notes.append(T("第 {steps} 步是自定义命令，导入的人会看到这些命令并确认", steps=", ".join(map(str, custom))))
    return notes


def dumps(cfg):
    """文件与剪贴板采用完全相同的 JSON 排版和末尾换行。"""
    content = json.dumps(cfg, ensure_ascii=False, indent=1) + "\n"
    if len(content.encode("utf-8")) > MAX_CONFIG:
        raise ConfigError(T("配置文本太大 (最多 {n} MB)", n=MAX_CONFIG >> 20))
    return content


def save(cfg, path):
    content = dumps(cfg)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


# ---------------------------------------------------------------------------
#  命令行 (不需要图形界面)：供脚本和 AI agent 生成、检查配置，见 docs/AGENT_SPEC.md
#    check FILE     检查配置，列出警告和自定义命令；有错误时返回 1
#    script FILE    按配置生成在当前目录运行整个流程的 shell 脚本 (与界面“导出流程为 shell 脚本”相同)
#    steps          输出各程序页的参数说明 (Markdown，即 docs/STEPS.md)
#    reference F…   输出混合系数文件的能级能量，即配置中的 reference
# ---------------------------------------------------------------------------

CLI_USAGE = """用法：
  grasp_gui check 配置.json                   检查配置：列出警告和自定义命令，有错误时返回 1
  grasp_gui script 配置.json [选项]           输出按配置在当前目录运行整个流程的 shell 脚本
      --bindir 目录                           GRASP 程序目录 (默认同界面)
      --mpi "mpirun -np 4"                    有并行版本的程序用 <程序>_mpi 运行
  grasp_gui steps                             输出各程序页的参数说明 (Markdown)
  grasp_gui reference 文件.cm …               输出混合系数文件的能级能量 (配置中的 reference，JSON)
不需要图形界面；源码中也可以用 python3 grasp_config.py 代替 grasp_gui。"""


def field_type(f):
    if isinstance(f, core.Check):
        return "bool"
    if isinstance(f, core.Choice):
        return "choice"
    if isinstance(f, core.CsfLists):
        return "lists"
    if isinstance(f, core.Text):
        return "text"
    return "string"


def both(s):
    """中文和英文写法 (有翻译时)。"""
    en = core.EN.get(s)
    return f"{s} / {en}" if en and en != s else s


def md_cell(s):
    return str(s).replace("|", "\\|").replace("\n", "\\n")


def steps_markdown():
    """各程序页的参数说明：键、类型、默认值、含义；由 grasp_core 的定义生成，与程序保持一致。"""
    out = [f"# Program tabs and fields / 程序页与参数 (GRASP GUI {core.VERSION})", "",
           "Generated by `grasp_gui steps` (or `python3 grasp_config.py steps`); do not edit by hand. "
           "See [AGENT_SPEC.md](AGENT_SPEC.md) and [CONFIG_FORMAT.md](CONFIG_FORMAT.md).", "",
           "由 `grasp_gui steps` 生成，请勿手工修改。`type`：string 为字符串，text 为多行字符串 (行间用 \\n)，bool 为 "
           "true/false，choice 取所列代码之一，lists 为 CSF 列表数组。", ""]
    for step in core.STEPS:
        out.append(f"## `{step.key}`")
        out.append("")
        info = [f"program `{step.prog or '(field prog)'}`"]
        if step.mpi:
            info.append(f"MPI `{step.prog}_mpi`")
        if step.key in core.GROUP_STEPS:
            info.append('`group`: "odd" / "even"')
        out += ["; ".join(info), ""]
        out += [f"> {both(n.text)}" for n in step.fields if isinstance(n, core.Note)]
        out += ["", "| key | type | default | meaning / 含义 |", "|---|---|---|---|"]
        for f in step.fields:
            if not isinstance(f, core.Field):
                continue
            meaning = both(f.label.strip()) if f.label else ""
            if isinstance(f, core.Choice):
                meaning += "<br>" + ", ".join(f"`{c}` = {both(t)}" for c, t in f.options)
            if isinstance(f, core.CsfLists):
                meaning = ("CSF lists of rcsfgenerate / CSF 列表：[{configs, active, jmin, jmax, nexc}]<br>" +
                           ", ".join(f"`{k}`: {both(t)}" + (f" ({both(h)})" if h else "")
                                     for k, t, h in [("configs", "组态 (每行一个)", "")] + core.CsfLists.LIST_FIELDS))
            if f.hint:
                meaning += f" — {both(f.hint)}"
            if getattr(f, "added", False):
                meaning += f" *({f.added})*"
            default = json.dumps(f.default, ensure_ascii=False)
            out.append(f"| `{f.key}` | {field_type(f)} | `{md_cell(default)}` | {md_cell(meaning)} |")
        out.append("")
    return "\n".join(out)


COMMANDS = ("check", "script", "steps", "reference")


def cli(args):
    """命令行入口；返回退出码。"""
    if "-h" in args or "--help" in args:
        print(T(CLI_USAGE))
        return 0
    if not args or args[0] not in COMMANDS:
        print(T(CLI_USAGE), file=sys.stderr)
        return 2
    cmd, args = args[0], args[1:]
    if cmd == "steps":
        print(steps_markdown())
        return 0
    if cmd == "reference":
        try:
            refs = [dict(file=os.path.basename(p), energies=reference_energies(p)) for p in args]
        except Exception as e:          # 读不了或不是混合系数文件
            print(T("错误：{e}", e=e), file=sys.stderr)
            return 1
        print(json.dumps(refs, indent=1) if refs else T(CLI_USAGE))
        return 0 if refs else 2
    opts = {}
    while len(args) > 1 and args[-2] in ("--bindir", "--mpi"):
        opts[args[-2]] = args[-1]
        args = args[:-2]
    if len(args) != 1 or args[0].startswith("-"):
        print(T(CLI_USAGE), file=sys.stderr)
        return 2
    try:
        cfg, warnings = load(args[0])
    except ConfigError as e:
        print(T("错误：{e}", e=e), file=sys.stderr)
        return 1
    if cmd == "script":
        for w in warnings:
            print(T("警告：{w}", w=w), file=sys.stderr)
        print(core.shell_script(session(cfg)["pipeline"], opts.get("--bindir") or core.default_bindir(), ".",
                                opts.get("--mpi", "")))
        return 0
    print(T("{n} 个计算步骤，所需程序：{p}", n=len(cfg["jobs"]), p=" ".join(programs(cfg))))
    for w in warnings:
        print(T("警告：{w}", w=w))
    for note in portability(cfg):
        print(T("提示：{w}", w=note))
    for i, c in custom_commands(cfg):
        print(T("第 {i} 步是自定义命令：", i=i) + "\n  " + c.replace("\n", "\n  "))
    print(T("配置可以导入"))
    return 0


if __name__ == "__main__":
    core.set_lang(core.load_config().get("lang", "zh"))
    sys.exit(cli(sys.argv[1:]))
