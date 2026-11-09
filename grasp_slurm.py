"""Slurm 提交记录、限频状态查询与取消；不依赖 Qt，不在登录节点执行计算。"""

import datetime
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import shlex
import shutil
import stat
import subprocess
import threading
import time
import uuid

import grasp_core as core
import grasp_session as sessions
from grasp_core import T

RECORD = ".grasp_gui_slurm.json"
TERMINAL = {"COMPLETED", "CANCELLED", "FAILED", "TIMEOUT", "OUT_OF_MEMORY", "NODE_FAIL", "BOOT_FAIL",
            "DEADLINE", "PREEMPTED", "REVOKED"}
_cache = {}
_lock = threading.Lock()


class CommandFailed(ValueError):
    """命令明确返回失败；与超时、丢失回答导致的不确定结果分开。"""


def identity(state):
    return [(state or {}).get(k) for k in ("host", "pid", "start")]


def record_bytes(workdir):
    path = Path(workdir) / RECORD
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise ValueError(T("Slurm 作业记录无效")) from error
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError(T("Slurm 作业记录无效"))
        return stream.read(65537)


def read(workdir):
    raw = record_bytes(workdir)
    if raw is None:
        return None
    try:
        value = json.loads(raw) if len(raw) <= 65536 else None
        valid = (isinstance(value, dict) and value.get("version") == 1
                 and re.fullmatch(r"[0-9a-f]{32}", value.get("token", ""))
                 and isinstance(value.get("directory"), str)
                 and os.path.isabs(value["directory"])
                 and isinstance(value.get("job_id"), str)
                 and re.fullmatch(r"[1-9][0-9]*", value["job_id"] or "1")
                 and re.fullmatch(r"[A-Za-z0-9_.-]*", value.get("cluster", ""))
                 and isinstance(value.get("submitted"), str))
    except (ValueError, TypeError, RecursionError):
        valid = False
    if not valid:
        raise ValueError(T("Slurm 作业记录无效"))
    if value["directory"] != str(Path(workdir).resolve()):
        return None
    return value


def command(args, workdir, text=None):
    try:
        result = subprocess.run(args, cwd=workdir, input=text, capture_output=True, text=True,
                                timeout=60 if args[0] == "sbatch" else 30,
                                env=core.child_env(LC_ALL="C", TZ="UTC",
                                                               SLURM_TIME_FORMAT="%Y-%m-%dT%H:%M:%S"))
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError(T("Slurm 命令失败：{e}", e=str(error))) from error
    if result.returncode:
        raise CommandFailed(T("Slurm 命令失败：{e}", e=(result.stderr or result.stdout).strip()[-2000:]))
    return result.stdout.strip()


def cluster_args(record):
    return ["--clusters=" + record["cluster"]] if record.get("cluster") else []


def log_file(workdir, record):
    return Path(workdir) / (".grasp_gui_slurm_" + record["token"] + ".log")


def path_expression(value):
    """路径只展开环境变量；空格和命令替换符按普通文件名处理。"""
    if value.startswith("~/"):
        value = "$HOME/" + value[2:]
    parts = re.split(r"(\$\{[A-Za-z_][A-Za-z0-9_]*\}|\$[A-Za-z_][A-Za-z0-9_]*)", value)
    return "".join('"${' + part.lstrip("$").strip("{}") + '}"' if part.startswith("$") and
                   re.fullmatch(r"\$\{[A-Za-z_][A-Za-z0-9_]*\}|\$[A-Za-z_][A-Za-z0-9_]*", part)
                   else shlex.quote(part)
                   for part in parts if part) or "''"


def batch_script(jobs, settings):
    values = dict(settings, bindir=path_expression(settings["bindir"]),
                  mpitmp=path_expression(settings["mpitmp"] or "$SLURM_SUBMIT_DIR/tmp_mpi"))
    return core.slurm_script(jobs, values)


def finish_file(workdir, record):
    return Path(workdir) / (".grasp_gui_slurm_" + record["token"] + ".done")


def with_finish_marker(script, workdir, record):
    """在所有命令之前安装 EXIT trap；标记原子写入，SIGKILL 等没有标记时仍保持未知。"""
    target = shlex.quote(str(finish_file(workdir, record)))
    token = shlex.quote(record["token"])
    hook = ("_grasp_gui_finish() {\n"
            "    _grasp_gui_code=$?\n"
            "    trap - EXIT\n"
            f"    printf '%s|%s|%s\\n' {token} \"$SLURM_JOB_ID\" \"$_grasp_gui_code\" > "
            f"{target}.tmp.\"$SLURM_JOB_ID\" &&\n"
            f"        mv -f -- {target}.tmp.\"$SLURM_JOB_ID\" {target}\n"
            "    exit \"$_grasp_gui_code\"\n"
            "}\ntrap _grasp_gui_finish EXIT\ntrap 'exit 143' TERM\ntrap 'exit 130' INT\n")
    lines = script.splitlines(keepends=True)
    offset = next((i for i, line in enumerate(lines) if line.strip() and not line.startswith("#")), len(lines))
    return "".join(lines[:offset]) + "\n" + hook + "".join(lines[offset:])


def finished(workdir, record):
    path = finish_file(workdir, record)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError(T("Slurm 结束标记无效"))
        raw = stream.read(1025)
    match = re.fullmatch(rb"([0-9a-f]{32})\|([1-9][0-9]*)\|([0-9]{1,3})\n", raw)
    if (not match or match[1].decode() != record["token"] or int(match[3]) > 255
            or record["job_id"] and match[2].decode() != record["job_id"]):
        raise ValueError(T("Slurm 结束标记无效"))
    code = int(match[3])
    cancelled = code in (130, 143) and record.get("cancel_requested") is True
    result = "ok" if code == 0 else "stopped" if cancelled else "failed"
    return dict(job_id=match[2].decode(), scheduler_state="BATCH_EXITED", status=result,
                active=False, can_cancel=False, exit_code=f"{code}:0", evidence="batch_exit_marker")


def submit(workdir, script, background=None):
    """调用方已持有目录锁并检查空闲；先持久化意图，避免丢失回答后重复提交。"""
    root = Path(workdir).resolve()
    missing = [name for name in ("sbatch", "squeue", "scancel") if not shutil.which(name)]
    if missing:
        raise ValueError(T("服务器缺少 Slurm 命令：{names}", names=", ".join(missing)))
    token = uuid.uuid4().hex
    record = dict(version=1, directory=str(root), token=token, job_id="", cluster="",
                  submitted=datetime.datetime.now(datetime.timezone.utc).isoformat(), background=identity(background))
    script = with_finish_marker(script, root, record)
    record["script_sha256"] = hashlib.sha256(script.encode()).hexdigest()
    path = root / (".grasp_gui_slurm_" + token + ".sh")
    sessions.atomic(path, script.encode())
    sessions.atomic(root / RECORD, json.dumps(record).encode())
    # CLI 选项覆盖脚本中的同名指令，确保日志和所有权标记可以找回。
    try:
        output = str(log_file(root, record)).replace("%", "%%")
        raw = command(["sbatch", "--parsable", "--comment=grasp-gui:" + token,
                       "--chdir=" + str(root), "--output=" + output, "--error=" + output, str(path)], root)
    except CommandFailed as error:
        record.update(rejected=True, error=str(error))
        sessions.atomic(root / RECORD, json.dumps(record).encode())
        raise
    match = re.fullmatch(r"([1-9][0-9]*)(?:;([A-Za-z0-9_.-]+))?", raw)
    if not match:
        raise ValueError(T("Slurm 提交结果不明确，请核对队列，勿重复提交"))
    record.update(job_id=match[1], cluster=match[2] or "")
    sessions.atomic(root / RECORD, json.dumps(record).encode())
    return dict(started=True, backend="slurm", job_id=record["job_id"], cluster=record["cluster"])


def queue_rows(workdir, record):
    raw = command(["squeue", *cluster_args(record), "--noheader",
                    "--user=" + pwd.getpwuid(os.getuid()).pw_name, "--format=%i|%T|%k"], workdir)
    rows = [tuple(part.strip() for part in line.split("|", 2)) for line in raw.splitlines()]
    if any(len(row) != 3 for row in rows):
        raise ValueError(T("Slurm 队列中的任务与保存记录不匹配"))
    return rows


def queued(workdir, record):
    rows = queue_rows(workdir, record)
    matches = [row for row in rows if row[2] == "grasp-gui:" + record["token"]]
    if len(matches) > 1 or any(row[0] == record["job_id"] and row not in matches for row in rows):
        raise ValueError(T("Slurm 队列中的任务与保存记录不匹配"))
    if not matches:
        return None
    job, state, comment = matches[0]
    if not re.fullmatch(r"[1-9][0-9]*", job) or record["job_id"] and job != record["job_id"]:
        raise ValueError(T("Slurm 队列中的任务与保存记录不匹配"))
    record["job_id"] = job
    return state


def query(workdir, record):
    state = queued(workdir, record)
    if state:
        pending = {"PENDING", "CONFIGURING", "REQUEUED", "REQUEUE_FED", "REQUEUE_HOLD", "RESV_DEL_HOLD", "SPECIAL_EXIT"}
        running = {"RUNNING", "COMPLETING", "SUSPENDED", "SIGNALING", "STAGE_OUT", "STOPPED", "RESIZING"}
        result = "queued" if state in pending else "running" if state in running else "unknown"
        return dict(job_id=record["job_id"], scheduler_state=state, status=result, active=True, can_cancel=True)
    marker = finished(workdir, record)
    if not record["job_id"]:
        return marker or dict(status="unknown", active=True, can_cancel=False,
                              error=T("Slurm 提交结果不明确，请核对队列，勿重复提交"))
    # squeue 消失不代表成功；只采用分配本身的记录，忽略 .batch/.extern 等步骤。
    try:
        raw = command(["sacct", *cluster_args(record), "--noheader", "--parsable2", "--allocations",
                       "--jobs=" + record["job_id"], "--starttime=" + record["submitted"][:10],
                       "--format=JobIDRaw,State%40,ExitCode,WorkDir%4096,Submit"], workdir)
    except ValueError:
        if marker:
            return marker
        raise
    for line in raw.splitlines():
        parts = line.split("|")
        if len(parts) != 5 or parts[0] != record["job_id"]:
            continue
        job, state, exit_code, directory, submitted = parts
        if directory != str(Path(workdir).resolve()):
            continue
        try:
            before = datetime.datetime.fromisoformat(record["submitted"]).astimezone(datetime.timezone.utc)
            after = datetime.datetime.fromisoformat(submitted).replace(tzinfo=datetime.timezone.utc)
            if abs((after - before).total_seconds()) > 300:
                continue
        except ValueError:
            continue
        if not state.strip():
            continue
        state = state.split()[0].rstrip("+")
        if state not in TERMINAL:
            return marker or dict(scheduler_state=state, status="unknown", active=True, can_cancel=False)
        result = "ok" if state == "COMPLETED" and exit_code == "0:0" else (
            "stopped" if state == "CANCELLED" else "failed")
        return dict(scheduler_state=state, status=result, active=False, can_cancel=False, exit_code=exit_code,
                    evidence="accounting")
    return marker or dict(scheduler_state="UNKNOWN", status="unknown", active=True, can_cancel=False,
                error=T("Slurm 暂无作业记录，不能判定任务已结束"))


def status(workdir, background=None, refresh=False):
    try:
        record = read(workdir)
    except ValueError as error:
        raw = record_bytes(workdir)
        return dict(backend="slurm", status="unknown", active=True, can_cancel=False, can_clear=True,
                    record_invalid=True, record_revision=sessions.revision(raw), error=str(error))
    if record is None:
        return None
    # 新的后台直跑有自己的状态；旧 Slurm 记录不应覆盖它。
    if background and identity(background) != record.get("background"):
        return None
    base = dict(backend="slurm", job_id=record["job_id"], cluster=record["cluster"],
                start=record["submitted"], token=record["token"], status="unknown", active=True, can_cancel=False,
                record_revision=sessions.revision(record_bytes(workdir)))
    if not record["job_id"]:
        if record.get("rejected"):
            return dict(base, status="failed", active=False, error=record.get("error", ""))
    key = (str(Path(workdir).resolve()), record["token"], record.get("cancel_requested") is True)
    with _lock:
        cached = _cache.get(key)
        if cached and not refresh and cached[0] > time.monotonic():
            value = dict(cached[1])
        else:
            try:
                value = query(workdir, dict(record))
            except (ValueError, OSError) as error:
                value = dict(scheduler_state="UNKNOWN", status="unknown", active=True, can_cancel=False,
                             error=str(error))
            value["can_clear"] = value["status"] == "unknown"
            interval = 60 if value.get("error") or not value["active"] else 15
            _cache[key] = (time.monotonic() + interval, value)
        # 仅缓存当前浏览的少量任务，避免长期服务随访问目录数增长。
        while len(_cache) > 128:
            _cache.pop(next(iter(_cache)))
    # 不在缓存锁内取目录锁，避免与持目录锁的提交/更新请求互相等待。
    if not record["job_id"] and value.get("job_id"):
        with sessions.locked(workdir):
            current = read(workdir)
            if current and current["token"] == record["token"] and not current["job_id"]:
                current["job_id"] = value["job_id"]
                sessions.atomic(Path(workdir) / RECORD, json.dumps(current).encode())
                base["record_revision"] = sessions.revision(record_bytes(workdir))
    return dict(base, **value)


def clear(workdir, revision, confirmed=False):
    """显式解除未知记录；重新查队列，归档原字节，不把解除当作取消或计算成功。"""
    if confirmed is not True:
        raise ValueError(T("请先确认已人工核对 Slurm 作业"))
    with sessions.locked(workdir):
        raw = record_bytes(workdir)
        if raw is None or sessions.revision(raw) != revision:
            raise sessions.Conflict(T("Slurm 作业记录已改变，请刷新"))
        try:
            record = read(workdir)
        except ValueError:
            try:
                record = json.loads(raw)
            except (ValueError, RecursionError):
                record = {}
            if not isinstance(record, dict):
                record = {}
            record = {k: v for k, v in record.items() if isinstance(v, str)}
            if not re.fullmatch(r"[A-Za-z0-9_.-]*", record.get("cluster", "")):
                record.pop("cluster", None)
        if record is None:
            raise ValueError(T("Slurm 作业记录无效"))
        token = record.get("token", "")
        rows = queue_rows(workdir, record)
        valid_token = re.fullmatch(r"[0-9a-f]{32}", token)
        if any(not valid_token or row[2] == "grasp-gui:" + token for row in rows):
            raise ValueError(T("队列中仍有相关作业，不能清除记录"))
        path = Path(workdir) / RECORD
        archive = path.with_name(RECORD + ".cleared." + uuid.uuid4().hex)
        path.rename(archive)
    return dict(cleared=True, archive=archive.name)


def cancel(workdir):
    with sessions.locked(workdir):
        record = read(workdir)
        if record is None or not record["job_id"] or queued(workdir, record) is None:
            raise ValueError(T("无法确认 Slurm 作业，请先刷新并核对队列"))
        try:
            command(["scancel", *cluster_args(record), record["job_id"]], workdir)
            # 只有成功发出取消后，TERM/INT 的结束标记才可解释为用户取消；普通失败仍保持失败。
            record["cancel_requested"] = True
            sessions.atomic(Path(workdir) / RECORD, json.dumps(record).encode())
        finally:
            with _lock:
                for requested in (False, True):
                    _cache.pop((str(Path(workdir).resolve()), record["token"], requested), None)
    return dict(stop_requested=True, confirmed=False, backend="slurm", job_id=record["job_id"])
