"""本地工作目录的配置差异、备份和清理；只用标准库，不执行计算。"""

from collections import defaultdict, deque
from contextlib import ExitStack, contextmanager
import datetime
import difflib
import fnmatch
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile

import grasp_bg as bg
import grasp_config as config
import grasp_core as core
import grasp_session as sessions
import grasp_slurm as slurm
from grasp_core import T

BACKUPS = 5
CLEANED = ".grasp_gui_cleaned"
CLEAN_STAMP = "%Y%m%d-%H%M%S-%f"
MAX_CLEAN_FILES = 50000
DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def _internal(name):
    return any(part.startswith(".grasp_gui") or part == "grasp_run" for part in PurePosixPath(name).parts)


def _angular_cleanup_files(names, mpitmp, directory):
    """匹配串行及实际存在的 MPI 角系数文件，不依赖当前进程数。"""
    roots = {("tmp_mpi",)}
    if mpitmp:
        if os.path.isabs(mpitmp):
            mpitmp = os.path.relpath(mpitmp, directory) if directory is not None else ".."
        parts = PurePosixPath(mpitmp).parts
        if ".." not in parts:
            roots.add(parts)
    files = set()
    for name in names:
        parts = PurePosixPath(name).parts
        if len(parts) == 1 and re.fullmatch(r"mcp\.[3-9][0-9]", name):
            files.add(name)
        elif len(parts) >= 2 and parts[:-2] in roots:
            if re.fullmatch(r"([0-9]{3})/mcp\1\.[3-9][0-9]", "/".join(parts[-2:])):
                files.add(name)
    return files


def cleanup_classes(names, jobs, attached=(), run=None, directory=None, *, recorded_mpitmp=None):
    """记录每个现有文件第一次被读时是否已由流程写出，再按外部输入优先分类。"""
    from grasp_summary import page

    names = set(names)
    external, generated, available = set(attached), set(), set()
    internal = {name for name in names if _internal(name)}
    candidates = set(names) - internal
    mpitmp = recorded_mpitmp or (run or {}).get("mpitmp")
    angular = _angular_cleanup_files(candidates, mpitmp, directory)
    directories = defaultdict(list)
    for name in candidates:
        directories[os.path.dirname(name)].append(name)

    def matches(pattern, declared_pattern=False):
        if not pattern:
            return set()
        if os.path.isabs(pattern):
            if directory is None:
                return set()
            pattern = os.path.relpath(pattern, directory)
        parts = PurePosixPath(pattern).parts
        if not parts or ".." in parts:
            return set()
        pattern = PurePosixPath(*parts).as_posix()
        if not declared_pattern or not any(digits in pattern for digits in ("[0-9]", "[3-9]")):
            return {pattern} & candidates
        return {name for name in directories.get(os.path.dirname(pattern), ()) if fnmatch.fnmatchcase(name, pattern)}

    def read(path, declared_pattern=False):
        external.update(matches(path, declared_pattern) - available)

    def written(path, declared_pattern=False):
        files = matches(path, declared_pattern)
        generated.update(files)
        available.update(files)

    def apply_actions(actions):
        for action in actions:
            if action[0] == "cp":
                read(action[1])
                written(action[2])
            elif action[0] == "write":
                written(action[1])

    for job in jobs:
        step, vals = page(job)
        # 未声明的用户命令不推断文件，明确声明的标准输出日志仍可定位。
        if step is not None:
            apply_actions(job["pre"] if "pre" in job else step.pre(vals))
            for name in step.inputs(vals) or []:
                read(name)
            for name in step.side_inputs(vals):
                read(name, declared_pattern=True)
            if isinstance(step, core.RMcdhf):
                external.update(angular - available)
            for name in (step.outputs(vals) or []) + step.result_files(vals):
                written(name)
            for name in step.side_outputs(vals):
                written(name, declared_pattern=True)
            if isinstance(step, core.RAngular):
                generated.update(angular)
                available.update(angular)
            apply_actions(job["post"] if "post" in job else step.post(vals))
            for name in step.removes(vals):
                available.difference_update(matches(name))
        written(job.get("log"))

    return {name: ("internal" if name in internal else "external" if name in external else
                   "generated" if name in generated else "unknown") for name in names}


def idle(workdir, *, cleaning=False):
    state = bg.status(workdir)
    if cleaning:
        scheduled = slurm.status(workdir, state, refresh=True, reconcile=False)
    else:
        scheduled = slurm.status(workdir, state)
    if scheduled and scheduled.get("active", True):
        raise ValueError(T("已有 Slurm 作业或提交结果未确认，请先核对状态。"))
    if state and state.get("status") == "running":
        raise ValueError(T("已有任务在运行，请等待或先停止。"))
    connection = bg.connect(workdir)
    if connection:
        connection.close()
        raise ValueError(T("已有任务在运行，请等待或先停止。"))
    request = Path(workdir) / bg.RUN_REQUEST
    if request.exists() and datetime.datetime.now().timestamp() - request.stat().st_mtime < bg.ATTACH_WAIT + 2:
        raise ValueError(T("已有任务在运行，请等待或先停止。"))


def _record(workdir):
    path = Path(workdir) / core.SESSION_FILE
    if path.is_symlink():
        raise ValueError(T("工作目录记录不能是符号链接"))
    raw = _read_plain(path)
    data = json.loads(raw)
    if not isinstance(data, dict) or not isinstance(data.get("pipeline", []), list):
        raise ValueError(T("工作目录记录的格式有误"))
    return data, raw


def _read_plain(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as f:
        if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
            raise ValueError(T("工作目录记录的格式有误"))
        return f.read()


def _atomic(path, raw, mode=None):
    path = Path(path)
    temporary = None
    if mode is None:
        try:
            existing = path.lstat()
            if stat.S_ISREG(existing.st_mode):
                mode = stat.S_IMODE(existing.st_mode)
        except FileNotFoundError:
            pass
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".grasp-update-", delete=False) as f:
            temporary = f.name
            f.write(raw)
            if mode is not None:
                os.fchmod(f.fileno(), mode)
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def backups(workdir):
    return sorted((p for p in Path(workdir).glob(core.SESSION_FILE + ".bak-*")
                   if p.is_file() and not p.is_symlink()), reverse=True)


def _backup(workdir, raw):
    name = core.SESSION_FILE + ".bak-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    _atomic(Path(workdir) / name, raw)
    for path in backups(workdir)[BACKUPS:]:
        path.unlink()


def _job(job):
    exported = config.export_job(job)
    if "command" in exported:
        return exported
    result = dict(step=exported["step"], vals=config.job_values(exported))
    if result["step"] in core.GROUP_STEPS:
        result["group"] = exported.get("group", core.GROUPS[0])
    return result


def _key(job):
    return json.dumps(_job(job), ensure_ascii=False, sort_keys=True)


def _value(value):
    return json.dumps(value, ensure_ascii=False)


def _parameters(a, b):
    if a.get("step") != b.get("step") or "step" not in a:
        return []
    fields = {f.key: f for f in config.STEPS[a["step"]].fields if isinstance(f, core.Field)}
    changes = []
    if a.get("group") != b.get("group"):
        changes.append(T("态组") + ": " + _value(a.get("group")) + " → " + _value(b.get("group")))
    for key in fields:
        if a["vals"].get(key) != b["vals"].get(key):
            changes.append(T(fields[key].label) + ": " + _value(a["vals"].get(key)) + " → " +
                           _value(b["vals"].get(key)))
    return changes


def _forms(data):
    result = {}
    for step in core.STEPS:
        if step.key not in core.GROUP_STEPS:
            vals = dict(step.defaults(), **data.get("steps", {}).get(step.key, {}))
            result[(step.key, None)] = _job(dict(step=step.key, vals=vals))
    groups = data.get("groups")
    if groups is None:
        groups = {core.GROUPS[0]: data.get("steps", {})}
    for group in core.GROUPS:
        for key, defaults in core.group_defaults(group).items():
            vals = dict(defaults, **groups.get(group, {}).get(key, {}))
            result[(key, group)] = _job(dict(step=key, group=group, vals=vals))
    return result


def compare(old, new):
    """按补齐默认值后的内容匹配步骤；中间插入不会把后续步骤误判为修改。"""
    a, b = old.get("pipeline", []), new.get("pipeline", [])
    ka, kb = [_key(j) for j in a], [_key(j) for j in b]
    opcodes = difflib.SequenceMatcher(None, ka, kb, autojunk=False).get_opcodes()
    append_from = len(a) if len(b) > len(a) and ka == kb[:len(a)] else None
    first = min((j for op, i, end, j, stop in opcodes if op != "equal" and j < len(b)), default=None)
    lines, removed, inserted = [], defaultdict(deque), defaultdict(deque)
    for op, i, end, j, stop in opcodes:
        if op in ("delete", "replace"):
            for pos in range(i, end):
                removed[ka[pos]].append(pos)
        if op in ("insert", "replace"):
            for pos in range(j, stop):
                inserted[kb[pos]].append(pos)
    moved_old, moved_new = set(), set()
    for key in sorted(removed.keys() & inserted.keys()):
        while removed[key] and inserted[key]:
            i, j = removed[key].popleft(), inserted[key].popleft()
            moved_old.add(i)
            moved_new.add(j)
            lines.append(T("移动：第 {old} 步 → 第 {new} 步", old=i + 1, new=j + 1))
    if append_from is not None:
        lines.insert(0, T("前面 {n} 步不变，新增 {m} 步", n=len(a), m=len(b) - len(a)))
    covered = set()
    for op, i, end, j, stop in opcodes:
        if op == "equal":
            if i != j:
                lines.append(T("原第 {a}–{b} 步保持不变，现为第 {c}–{d} 步", a=i + 1, b=end, c=j + 1, d=stop))
            continue
        left = [p for p in range(i, end) if p not in moved_old]
        right = [p for p in range(j, stop) if p not in moved_new]
        left_names = [_job(a[p]).get("step", "") for p in left]
        right_names = [_job(b[p]).get("step", "") for p in right]
        matched_left, matched_right = set(), set()
        matches = difflib.SequenceMatcher(None, left_names, right_names, autojunk=False).get_matching_blocks()
        for start_a, start_b, count in matches:
            for offset in range(count):
                x, y = left[start_a + offset], right[start_b + offset]
                before, after = _job(a[x]), _job(b[y])
                if not before.get("step"):
                    continue
                changes = _parameters(before, after)
                lines.append(T("修改：第 {n} 步 {step}", n=y + 1, step=after["step"]))
                lines.extend("  " + line for line in changes)
                covered.update((after["step"], after.get("group"), line) for line in changes)
                matched_left.add(x)
                matched_right.add(y)
        left = [p for p in left if p not in matched_left]
        right = [p for p in right if p not in matched_right]
        lines.extend(T("删除：原第 {n} 步 {step}", n=p + 1, step=a[p].get("step") or a[p].get("prog", ""))
                     for p in left)
        lines.extend(T("新增：第 {n} 步 {step}", n=p + 1, step=b[p].get("step") or b[p].get("prog", ""))
                     for p in right)
    forms = []
    old_forms, new_forms = _forms(old), _forms(new)
    for key, after in new_forms.items():
        changes = [line for line in _parameters(old_forms[key], after) if (*key, line) not in covered]
        if changes:
            forms.append(T("程序页 {step} ({group})", step=key[0], group=key[1] or "—"))
            forms.extend("  " + line for line in changes)
    if old.get("group", "odd") != new.get("group", "odd"):
        forms.append(T("态组") + ": " + str(old.get("group", "odd")) + " → " + str(new.get("group", "odd")))
    if forms:
        lines += ["", T("程序页参数的其他改动："), *forms]
    return dict(text="\n".join(lines) or T("流程和参数没有变化"), first=first, append_from=append_from)


def prepare_update(workdir, cfg, current=None):
    idle(workdir)
    cfg, _ = config.check(cfg)
    try:
        disk, raw = _record(workdir)
    except FileNotFoundError:
        disk, raw = {}, None
    old = dict(current if current is not None else disk)
    old.pop("workdir", None)
    old.pop("bindir", None)
    new = dict(old, **config.session(cfg))
    # 补齐未在新流程中出现的表单，避免界面保留上个方案的隐含参数。
    new["steps"] = {s.key: dict(s.defaults(), **new["steps"].get(s.key, {})) for s in core.STEPS
                    if s.key not in core.GROUP_STEPS}
    manifest = Path(workdir) / config.INPUTS_FILE
    try:
        inputs_raw = _read_plain(manifest)
    except FileNotFoundError:
        inputs_raw = None
    inputs = config.parse_inputs(inputs_raw) if inputs_raw is not None else set()
    files, names = [], set()
    for item in cfg.get("files", []):
        if item["name"] in names:
            raise ValueError(T("附带文件名重复：{name}", name=item["name"]))
        names.add(item["name"])
        path = Path(workdir) / item["name"]
        target = item["text"].encode("utf-8")
        try:
            st = path.lstat()
        except FileNotFoundError:
            files.append(dict(name=item["name"], kind="new", text=item["text"], previous=None, signature=None))
            continue
        signature = [st.st_dev, st.st_ino, st.st_mode, st.st_size, st.st_mtime_ns]
        existing = None
        if stat.S_ISREG(st.st_mode) and st.st_size <= config.MAX_FILE:
            existing = _read_plain(path)
        kind = "same" if existing == target else "conflict"
        previous = existing.decode("utf-8", "replace") if existing is not None else T("现有项目不是小型普通文本文件")
        files.append(dict(name=item["name"], kind=kind, text=item["text"], previous=previous, signature=signature,
                          mode=stat.S_IMODE(st.st_mode) if stat.S_ISREG(st.st_mode) else None))
    inputs.update(names)
    return dict(root=os.path.realpath(workdir), expected=raw, session=new, files=files,
                inputs=inputs, inputs_expected=inputs_raw, **compare(old, new))


def apply_update(plan, overwrite=(), *, bindir=None):
    with sessions.locked(plan["root"]):
        return _apply_update(plan, overwrite, bindir=bindir)


def _apply_update(plan, overwrite=(), *, bindir=None):
    root = plan["root"]
    idle(root)
    try:
        _, raw = _record(root)
    except FileNotFoundError:
        raw = None
    if raw != plan["expected"]:
        raise ValueError(T("工作目录记录已改变，请重新预览"))
    overwrite = set(overwrite)
    writes = [f for f in plan["files"] if f["kind"] == "new" or f["kind"] == "conflict" and f["name"] in overwrite]
    for item in plan["files"]:
        path = Path(root) / item["name"]
        try:
            st = path.lstat()
            signature = [st.st_dev, st.st_ino, st.st_mode, st.st_size, st.st_mtime_ns]
        except FileNotFoundError:
            signature = None
        if signature != item["signature"]:
            raise ValueError(T("附带文件已改变，请重新预览：{name}", name=item["name"]))
        if item in writes and path.is_dir() and not path.is_symlink():
            raise ValueError(T("不能用附带文件覆盖目录：{name}", name=item["name"]))
    manifest = Path(root) / config.INPUTS_FILE
    try:
        inputs_raw = _read_plain(manifest)
    except FileNotFoundError:
        inputs_raw = None
    if inputs_raw != plan["inputs_expected"]:
        raise ValueError(T("附带输入清单已改变，请重新预览"))
    writes.append(dict(name=config.INPUTS_FILE,
                       text=json.dumps(sorted(plan["inputs"]), ensure_ascii=False, indent=1) + "\n"))
    if raw is not None:
        _backup(root, raw)
    originals = []
    try:
        for item in writes:
            path = Path(root) / item["name"]
            previous = None
            if path.exists() or path.is_symlink():
                fd, previous = tempfile.mkstemp(dir=root, prefix=".grasp-input-backup-")
                os.close(fd)
                try:
                    os.replace(path, previous)
                except OSError:
                    os.unlink(previous)
                    raise
            originals.append((path, previous))
            _atomic(path, item["text"].encode("utf-8"), item.get("mode"))
        sessions.write(root, plan["session"], sessions.revision(raw), warnings=plan.setdefault("script_warnings", []),
                       bindir=bindir)
    except OSError:
        for path, previous in reversed(originals):
            if previous is None:
                path.unlink(missing_ok=True)
            else:
                os.replace(previous, path)
        raise
    else:
        for _, previous in originals:
            if previous is not None:
                try:
                    os.unlink(previous)
                except OSError as e:
                    # 配置已经提交成功；清理失败要告知用户，不能误报整个更新失败。
                    plan.setdefault("warnings", []).append(str(e))
    return plan["session"]


def restore(workdir, *, warnings=None, bindir=None):
    with sessions.locked(workdir):
        return _restore(workdir, warnings=warnings, bindir=bindir)


def _restore(workdir, *, warnings=None, bindir=None):
    idle(workdir)
    choices = backups(workdir)
    if not choices:
        raise ValueError(T("没有可恢复的工作目录记录"))
    raw = _read_plain(choices[0])
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError(T("工作目录记录的格式有误"))
    _, current = _record(workdir)
    _backup(workdir, current)
    sessions.write_bytes(workdir, raw, sessions.revision(current), warnings=warnings, bindir=bindir)
    return data


SYSTEM_DIRS = ("/", "/home", "/root", "/usr", "/etc", "/var", "/bin", "/sbin", "/lib", "/lib64", "/opt",
               "/tmp", "/proc", "/sys", "/dev", "/run", "/boot", "/srv", "/mnt", "/media", "/Users",
               "/System", "/Library", "/Applications", "/Volumes")

SYSTEM_TREES = ("/usr", "/etc", "/bin", "/sbin", "/lib", "/lib64", "/proc", "/sys", "/dev", "/run",
                "/boot", "/System", "/Library", "/Applications")


def safe_root(workdir, allow_untracked=False, *, require_idle=True):
    root = os.path.realpath(workdir)
    forbidden = {os.path.realpath(p) for p in SYSTEM_DIRS} | {os.path.realpath(Path.home())}
    system_trees = [os.path.realpath(p) for p in SYSTEM_TREES]
    if root in forbidden or any(root.startswith(p + os.sep) for p in system_trees):
        raise ValueError(T("拒绝清空系统目录或家目录：{p}", p=root))
    if not os.path.isdir(root):
        raise ValueError(T("工作目录不存在：{d}", d=root))
    record = Path(root) / core.SESSION_FILE
    if (not record.is_file() or record.is_symlink()) and not allow_untracked:
        raise ValueError(T("此目录没有工作目录记录，必须另行确认"))
    if require_idle:
        idle(root, cleaning=True)
    return root


def _signature(path):
    return _stat_signature(os.lstat(path))


def _stat_signature(st):
    return [st.st_dev, st.st_ino, st.st_mode, st.st_size, st.st_mtime_ns]


def _mounted_paths():
    # Linux 的同设备 bind mount 不体现在 st_dev 中。
    try:
        lines = Path("/proc/self/mountinfo").read_bytes().splitlines()
    except FileNotFoundError:
        return set()
    return {os.fsdecode(re.sub(rb"\\([0-7]{3})", lambda m: bytes([int(m[1], 8)]), line.split()[4]))
            for line in lines}


@contextmanager
def _directory(fd, name, path, device, mounts, *, create=False):
    if create:
        try:
            os.mkdir(name, 0o700, dir_fd=fd)
        except FileExistsError:
            pass
    before = os.stat(name, dir_fd=fd, follow_symlinks=False)
    if path in mounts or before.st_dev != device or not stat.S_ISDIR(before.st_mode):
        raise ValueError(T("不能清理挂载点或特殊文件：{name}", name=path))
    child = os.open(name, DIR_FLAGS, dir_fd=fd)
    try:
        opened = os.fstat(child)
        if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            raise ValueError(T("目录内容已改变，请重新预览"))
        yield child
    finally:
        os.close(child)


@contextmanager
def _parent(fd, root, name, mounts, *, create=False):
    """从已打开的根目录逐层定位，禁止跟随替换进来的链接。"""
    device = os.fstat(fd).st_dev
    path = root
    with ExitStack() as stack:
        parts = PurePosixPath(name).parts
        for part in parts[:-1]:
            path = os.path.join(path, part)
            fd = stack.enter_context(_directory(fd, part, path, device, mounts, create=create))
        yield fd, parts[-1]


def _files(fd, root, device, mounts, *, scan_backups=False, strict=False):
    """枚举普通文件和链接，目录、特殊文件及挂载点不可勾选。"""
    visited = 0

    def walk(current, prefix):
        nonlocal visited
        for leaf in sorted(os.listdir(current)):
            visited += 1
            if visited > MAX_CLEAN_FILES:
                raise ValueError(T("目录中的项目过多，无法预览清理"))
            name = prefix + leaf
            path = os.path.join(root, name)
            st = os.stat(leaf, dir_fd=current, follow_symlinks=False)
            if not scan_backups and _internal(name):
                yield dict(name=name, count=1, size=st.st_size, mtime=st.st_mtime)
            elif path in mounts or st.st_dev != device or not (
                    stat.S_ISDIR(st.st_mode) or stat.S_ISREG(st.st_mode) or stat.S_ISLNK(st.st_mode)):
                if strict:
                    raise ValueError(T("不能清理挂载点或特殊文件：{name}", name=path))
            elif stat.S_ISDIR(st.st_mode):
                with _directory(current, leaf, path, device, mounts) as child:
                    yield from walk(child, name + "/")
            else:
                yield dict(name=name, count=1, size=st.st_size, mtime=st.st_mtime,
                           signature=_stat_signature(st))

    return walk(fd, "")


def _clean_stamp(name):
    try:
        return datetime.datetime.strptime(name.removesuffix(".deleted"), CLEAN_STAMP)
    except ValueError:
        return None


def _clean_backups(root, fd, mounts):
    if CLEANED not in os.listdir(fd):
        return []
    path = os.path.join(root, CLEANED)
    device = os.fstat(fd).st_dev
    rows = []
    with _directory(fd, CLEANED, path, device, mounts) as base:
        for name in sorted(os.listdir(base), reverse=True):
            moment = _clean_stamp(name)
            if moment is None or name.endswith(".deleted"):
                continue
            dest = os.path.join(path, name)
            with _directory(base, name, dest, device, mounts) as backup:
                files = list(_files(backup, dest, device, mounts, scan_backups=True))
                signature = _stat_signature(os.fstat(backup))
            rows.append(dict(name=f"{CLEANED}/{name}", time=moment.isoformat(sep=" ", timespec="seconds"),
                             count=len(files), size=sum(f["size"] for f in files), signature=signature))
    return rows


def last_cleaned(workdir):
    """从清理子目录名称取得时间；直接删除时只留下一个空标记目录。"""
    directory = Path(workdir) / CLEANED
    if not directory.is_dir() or directory.is_symlink():
        return None
    try:
        with os.scandir(directory) as entries:
            return max((moment for entry in entries if entry.is_dir(follow_symlinks=False)
                        and (moment := _clean_stamp(entry.name)) is not None), default=None)
    except OSError:
        return None


def _prepare_clean(workdir, allow_untracked=False, jobs=None, run=None):
    root = safe_root(workdir, allow_untracked, require_idle=False)
    with sessions.locked(root):
        record, revision = sessions.read(root)
        try:
            raw = _read_plain(Path(root) / config.INPUTS_FILE)
        except FileNotFoundError:
            raw = None
        attached = config.parse_inputs(raw) if raw is not None else set()
        mounts = _mounted_paths()
        fd = os.open(root, DIR_FLAGS)
        try:
            opened = os.fstat(fd)
            identity = [opened.st_dev, opened.st_ino]
            rows = list(_files(fd, root, opened.st_dev, mounts))
            saved = _clean_backups(root, fd, mounts)
        finally:
            os.close(fd)
    jobs = (record or {}).get("pipeline", []) if jobs is None else jobs
    if run is None and jobs:
        run = core.saved_run_settings(persist=False)
    kinds = cleanup_classes((row["name"] for row in rows), jobs, attached, run, root,
                            recorded_mpitmp=(record or {}).get("mpitmp"))
    groups = {kind: [] for kind in ("generated", "external", "unknown", "internal")}
    for row in rows:
        groups[kinds[row["name"]]].append(row)
    return dict(root=root, revision=revision, signature=identity, classes=groups, backups=saved,
                entries=groups["generated"] + groups["external"] + groups["unknown"], all_files=False,
                inputs_expected=raw, allow_untracked=allow_untracked, jobs=jobs, run=run)


def preview_clean(workdir):
    plan = prepare_clear(workdir, classified=True)
    # 仅传送界面需要的元数据，保留本机预览用的签名与原始清单。
    def public(rows):
        return [{key: val for key, val in row.items() if key != "signature"} for row in rows]

    return dict(root=plan["root"], revision=plan["revision"],
                classes={kind: public(rows) for kind, rows in plan["classes"].items()},
                backups=public(plan["backups"]))


def clean_workdir(workdir, revision, names, mode, *, allow_untracked=False, expected=None):
    if (mode not in ("move", "delete") or not isinstance(names, list) or not names or len(names) > MAX_CLEAN_FILES
            or any(not isinstance(name, str) for name in names) or len(names) != len(set(names))):
        raise ValueError(T("删除列表有误，请重新预览"))
    root = safe_root(workdir, allow_untracked, require_idle=False)
    with sessions.locked(root):
        idle(root, cleaning=True)
        _, actual_revision = sessions.read(root)
        if actual_revision != revision:
            raise sessions.Conflict(T("工作目录记录已被其他程序修改，请重新载入"))
        plan = _prepare_clean(root, allow_untracked, **(
            {key: expected[key] for key in ("jobs", "run")} if expected else {}))
        rows = {row["name"]: row["signature"] for row in plan["entries"]}
        deleting_backup = (mode == "delete" and len(names) == 1
                           and names[0] in {row["name"] for row in plan["backups"]})
        if deleting_backup:
            rows = {row["name"]: row["signature"] for row in plan["backups"]}
        if not set(names) <= rows.keys():
            raise ValueError(T("删除列表有误，请重新预览"))
        if expected:
            original = {row["name"]: row["signature"] for row in expected["entries"] + expected["backups"]}
            if (expected["signature"] != plan["signature"] or expected["inputs_expected"] != plan["inputs_expected"]
                    or any(rows[name] != original.get(name) for name in names)):
                raise ValueError(T("目录内容已改变，请重新预览"))
        return _clean_selected(plan, names, mode, rows, deleting_backup)


def _clean_selected(plan, names, mode, rows, deleting_backup):
    root = plan["root"]
    mounts = _mounted_paths()
    fd = os.open(root, DIR_FLAGS)
    processed, error = [], None
    timestamp = datetime.datetime.now().strftime(CLEAN_STAMP)
    backup = f"{CLEANED}/{timestamp}"
    try:
        opened = os.fstat(fd)
        if [opened.st_dev, opened.st_ino] != plan["signature"]:
            raise ValueError(T("目录内容已改变，请重新预览"))
        for name in names:
            try:
                with _parent(fd, root, name, mounts) as (parent, leaf):
                    path = os.path.join(root, name)
                    st = os.stat(leaf, dir_fd=parent, follow_symlinks=False)
                    if path in mounts or st.st_dev != opened.st_dev:
                        raise ValueError(T("不能清理挂载点或特殊文件：{name}", name=path))
                    if deleting_backup:
                        if _stat_signature(st) != rows[name]:
                            raise ValueError(T("目录内容已改变，请重新预览"))
                        with _directory(parent, leaf, path, opened.st_dev, mounts) as child:
                            list(_files(child, path, opened.st_dev, mounts, scan_backups=True, strict=True))
                        _remove(parent, leaf, path=path, device=opened.st_dev, mounts=mounts)
                    else:
                        if _stat_signature(st) != rows[name]:
                            raise ValueError(T("目录内容已改变，请重新预览"))
                        if mode == "move":
                            if not processed:
                                with _parent(fd, root, backup, mounts, create=True) as (base, stamp):
                                    os.mkdir(stamp, 0o700, dir_fd=base)
                            with _parent(fd, root, f"{backup}/{name}", mounts, create=True) as (target, out):
                                os.rename(leaf, out, src_dir_fd=parent, dst_dir_fd=target)
                        else:
                            os.unlink(leaf, dir_fd=parent)
                    processed.append(name)
            except (OSError, ValueError) as exc:
                error = str(exc)
                break
        if processed and mode == "delete" and not deleting_backup:
            try:
                with _parent(fd, root, f"{backup}.deleted", mounts, create=True) as (parent, name):
                    os.mkdir(name, 0o700, dir_fd=parent)
            except (OSError, ValueError) as exc:
                error = f"{error}\n{exc}" if error else str(exc)
    finally:
        os.close(fd)
    return dict(processed=processed, remaining=names[len(processed):], error=error,
                backup=backup if mode == "move" and processed else None)


def _measure(fd, name):
    """只记录链接本身，遍历目录时也不跟随后来换入的链接。"""
    st = os.stat(name, dir_fd=fd, follow_symlinks=False)
    signature = [st.st_dev, st.st_ino, st.st_mode, st.st_size, st.st_mtime_ns]
    size, count = (0 if stat.S_ISDIR(st.st_mode) else st.st_size), 1
    if stat.S_ISDIR(st.st_mode):
        child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
        try:
            opened = os.fstat(child)
            if (opened.st_dev, opened.st_ino) != (st.st_dev, st.st_ino):
                raise ValueError(T("目录内容已改变，请重新预览"))
            for entry in os.listdir(child):
                nbytes, nitems, _ = _measure(child, entry)
                size += nbytes
                count += nitems
        finally:
            os.close(child)
    return size, count, signature


def prepare_clear(workdir, all_files=False, allow_untracked=False, *, classified=False, jobs=None, run=None):
    if classified and not all_files:
        return _prepare_clean(workdir, allow_untracked, jobs, run)
    root = safe_root(workdir, allow_untracked)
    inputs_raw = None
    if not all_files:
        try:
            inputs_raw = _read_plain(Path(root) / config.INPUTS_FILE)
        except FileNotFoundError:
            pass
    inputs = config.parse_inputs(inputs_raw) if inputs_raw is not None else None
    protected = set(inputs or ()) | {core.SESSION_FILE, config.INPUTS_FILE, "grasp_run"}
    protected.update(p.name for p in backups(root))
    entries, kept = [], []
    fd = os.open(root, DIR_FLAGS)
    try:
        items = [(Path(root) / name, _measure(fd, name)) for name in sorted(os.listdir(fd))]
    finally:
        os.close(fd)
    for path, (size, count, signature) in items:
        item = dict(name=path.name, size=size, count=count, signature=signature)
        if not all_files and path.name in protected:
            kept.append(item)
        else:
            # 旧目录没有输入清单时，不猜测普通文件；运行文件和缓存仍可明确识别。
            item["checked"] = all_files or inputs is not None or path.name.startswith(".grasp_gui_run") or (
                path.name == ".grasp_gui")
            entries.append(item)
    return dict(root=root, signature=_signature(root), entries=entries, kept=kept, all_files=all_files,
                allow_untracked=allow_untracked, unknown_inputs=inputs is None and not all_files,
                inputs_expected=inputs_raw)


def _remove(fd, name, *, path=None, device=None, mounts=()):
    """用目录描述符逐层删除，路径被替换为链接时也不会进入外部目录。"""
    before = os.stat(name, dir_fd=fd, follow_symlinks=False)
    if device is not None and (path in mounts or before.st_dev != device or not (
            stat.S_ISDIR(before.st_mode) or stat.S_ISREG(before.st_mode) or stat.S_ISLNK(before.st_mode))):
        raise ValueError(T("不能清理挂载点或特殊文件：{name}", name=path))
    if stat.S_ISDIR(before.st_mode):
        child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
        try:
            opened = os.fstat(child)
            if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
                raise ValueError(T("目录内容已改变，请重新预览"))
            for entry in os.listdir(child):
                _remove(child, entry, path=os.path.join(path, entry) if path else None, device=device, mounts=mounts)
        finally:
            os.close(child)
        os.rmdir(name, dir_fd=fd)
    else:
        os.unlink(name, dir_fd=fd)


def clear(plan, selected, confirmation=None):
    if "classes" in plan:
        return clean_workdir(plan["root"], plan["revision"], sorted(selected), confirmation,
                             allow_untracked=plan["allow_untracked"], expected=plan)
    with sessions.locked(plan["root"]):
        return _clear(plan, selected, confirmation)


def _clear(plan, selected, confirmation=None):
    root = safe_root(plan["root"], plan["allow_untracked"])
    if plan["all_files"] and confirmation != os.path.basename(root):
        raise ValueError(T("请输入目录名确认：{name}", name=os.path.basename(root)))
    if not plan["all_files"]:
        try:
            inputs_raw = _read_plain(Path(root) / config.INPUTS_FILE)
        except FileNotFoundError:
            inputs_raw = None
        if inputs_raw != plan["inputs_expected"]:
            raise ValueError(T("附带输入清单已改变，请重新预览"))
    expected = {item["name"]: item for item in plan["entries"]}
    selected = set(selected)
    if not selected <= expected.keys():
        raise ValueError(T("删除列表有误，请重新预览"))
    fd = os.open(root, DIR_FLAGS)
    processed, error = [], None
    try:
        opened = os.fstat(fd)
        if [opened.st_dev, opened.st_ino] != plan["signature"][:2]:
            raise ValueError(T("目录内容已改变，请重新预览"))
        for name in selected:
            if _signature(Path(root) / name) != expected[name]["signature"]:
                raise ValueError(T("目录内容已改变，请重新预览"))
        idle(root, cleaning=True)
        mounts = _mounted_paths()
        for name in sorted(selected):
            try:
                _remove(fd, name, path=os.path.join(root, name), device=opened.st_dev, mounts=mounts)
            except (OSError, ValueError) as exc:
                if not plan["all_files"]:
                    raise
                error = str(exc)
                break
            processed.append(name)
    finally:
        os.close(fd)
    return dict(processed=processed, remaining=sorted(selected - set(processed)), error=error, backup=None)
