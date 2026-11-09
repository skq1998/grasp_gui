"""SSH stdio 服务：结构化分析和任务控制；无 Qt 或监听端口，Slurm 调度调用系统客户端。"""

import argparse
import base64
import codecs
import fnmatch
import inspect
import json
import os
from pathlib import Path
import pwd
import queue
import re
import subprocess
import sys
import threading
import time
import zlib

import grasp_bg as bg
import grasp_config as config
import grasp_core as core
import grasp_service as service
import grasp_session as sessions
from grasp_core import T

PROTOCOL = 1
GREETING = "GRASP_GUI_REMOTE 1"
MAX_FRAME = 32 << 20
COMPRESS_AT = 16384
READ_ONLY = {"hello", "list_dir", "levels", "transitions", "orbitals", "wavefunctions", "composition",
             "run_status", "tail_log", "pipeline", "run_plan", "export_script", "view", "watch", "summary",
             "transfer_files", "get_pipeline", "preview_upload"}
MUTATIONS = {"run", "stop", "clear_slurm_record", "create_workdir", "update_workdir"}


def pack(data):
    raw = json.dumps(data, ensure_ascii=True, allow_nan=False).encode()
    if len(raw) > MAX_FRAME:
        raise ValueError(T("远程结果过大，请减少文件或行数"))
    if len(raw) >= COMPRESS_AT:
        raw = json.dumps(dict(zlib=base64.b64encode(zlib.compress(raw)).decode(), size=len(raw))).encode()
    return raw + b"\n"


def unpack(raw):
    if len(raw) > MAX_FRAME:
        raise ValueError("remote frame too large")
    data = json.loads(raw.decode("utf-8"))
    if isinstance(data, dict) and "zlib" in data:
        decoder = zlib.decompressobj()
        value = decoder.decompress(base64.b64decode(data["zlib"], validate=True), MAX_FRAME + 1)
        if len(value) > MAX_FRAME or not decoder.eof or decoder.unused_data:
            raise ValueError("invalid compressed frame")
        data = json.loads(value.decode("utf-8"))
    return data


def integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError("invalid integer argument")
    return value


class Operations(service.Service):
    def __init__(self, cancel=None):
        super().__init__("/")
        self.cancel = cancel

    def path(self, value=".", directory=False):
        if not isinstance(value, str):
            raise ValueError("directory must be a string")
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            candidate = Path.home() / candidate
        return super().path(str(candidate.resolve()), directory)

    def hello(self, protocol, version=""):
        if type(protocol) is not int or protocol != PROTOCOL:
            raise ValueError(T("远程协议不兼容，请更新服务器端程序"))
        executable = os.environ.get("GRASP_GUI_REMOTE_EXECUTABLE") or (
            sys.executable if getattr(sys, "frozen", False) else sys.argv[0])
        return dict(protocol=PROTOCOL, version=core.VERSION, executable=os.path.abspath(executable),
                    home=str(Path.home()), server_types=["workstation", "slurm"], capabilities=["workdir_write_v1"])

    def get_pipeline(self, directory):
        root, data, revision = self.record(directory)
        with sessions.locked(root):
            root, data, revision = self.record(directory)
            files, warnings, size = [], [], 0
            for name in sorted(config.load_inputs(str(root)) or []):
                path = self.path(str(root / name))
                try:
                    if (root / name).is_symlink():
                        raise ValueError(T("工作目录记录不能是符号链接"))
                    text = config.read_text_file(path)
                    size += len(text.encode())
                    if size > config.MAX_FILES_TOTAL:
                        raise ValueError(T("附带输入文件超过总大小限制"))
                    files.append(dict(name=name, text=text))
                except (OSError, ValueError) as error:
                    warnings.append(dict(file=name, error=str(error)))
            cfg = config.export(data.get("pipeline", []), dict(title=root.name))
            if files:
                cfg["files"] = files
            return dict(directory=str(root), revision=revision, config=cfg, warnings=warnings)

    def create_workdir(self, directory, configuration, allow_custom=False):
        if type(allow_custom) is not bool:
            raise ValueError("confirmation must be boolean")
        root = self.path(directory)
        return service.Service("/", allow_custom=allow_custom).create_workdir(str(root), configuration)

    def update_workdir(self, directory, revision, configuration, allow_custom=False):
        if type(allow_custom) is not bool:
            raise ValueError("confirmation must be boolean")
        root = self.path(directory, directory=True)
        return service.Service("/", allow_custom=allow_custom).update_workdir(str(root), revision, configuration)

    def preview_upload(self, directory, configuration, revision=None, uploads=None):
        import grasp_summary as summary
        import grasp_transfer as transfer
        import grasp_workdir as work
        cfg, warnings = config.check(configuration)
        root = self.path(directory)
        names = [] if uploads is None else uploads
        if not isinstance(names, list) or len(names) > 50000:
            raise ValueError("invalid upload names")
        names = [transfer.relative(name) for name in names]
        with sessions.locked(root):
            if revision is None:
                if root.exists() and (not root.is_dir() or any(root.iterdir())):
                    raise ValueError(T("目标目录不是空的；已有流程请先在本机取出编辑。"))
                work.idle(root)
                changes, kept = T("新建服务器流程：{n} 步", n=len(cfg["jobs"])), []
            else:
                if self.record(str(root))[2] != revision:
                    raise sessions.Conflict(T("服务器流程已改变，请重新取出；可先导出本机修改。"))
                plan = work.prepare_update(root, cfg)
                changes = plan["text"]
                kept = [f["name"] for f in plan["files"] if f["kind"] == "conflict"]
            transfer.safe_targets(root, names)
            run = core.saved_run_settings()
            checked = summary.check_files(str(root), cfg["jobs"], run=run, path_check=self.path)
            planned = names + [f["name"] for f in cfg.get("files", [])]
            pending = [row for row in checked["missing"] if any(fnmatch.fnmatchcase(n, row["file"]) for n in planned)]
            checked["missing"] = [row for row in checked["missing"] if row not in pending]
            checked.update(pending=pending, issues=checked["invalid"] + checked["missing"])
            return dict(directory=str(root), revision=revision, changes=changes, kept_files=kept, checked=checked,
                        missing_programs=config.missing_programs(cfg, run["bindir"]),
                        custom_commands=config.custom_commands(cfg), warnings=warnings,
                        existing=[n for n in names if (root / n).exists()])

    def list_dir(self, directory):
        root = self.path(directory, directory=True)
        rows = []
        for p in sorted(root.iterdir(), key=lambda p: (not p.is_dir(), p.name)):
            try:
                st = p.stat()
                kind = "directory" if p.is_dir() else "file"
                if p.is_file():
                    try:
                        kind = self.file_type(p) or "file"
                    except OSError:
                        kind = "unreadable"
                rows.append(dict(name=p.name, directory=p.is_dir(), size=st.st_size, mtime_ns=st.st_mtime_ns,
                                 type=kind))
            except OSError:
                continue  # 扫描期间删除的目录项无需阻止浏览其余文件。
            if len(rows) >= 5000:
                break
        return dict(directory=str(root), parent=str(root.parent), files=rows, truncated=len(rows) == 5000)

    def pipeline(self, directory):
        root, data, revision = self.record(directory)
        return dict(jobs=data.get("pipeline", []), revision=revision, directory=str(root))

    def transfer_files(self, directory, names=None, rsync="rsync", targets=None):
        import grasp_transfer as transfer
        root = self.path(directory, directory=True)
        if targets is not None:
            if not isinstance(targets, list) or len(targets) > 50000:
                raise ValueError("invalid target list")
            transfer.safe_targets(root, targets)
        result = transfer.scan(root, names)
        result["rsync"] = transfer.capabilities(rsync)
        return result

    def summary(self, directory, per_block=5, sections=None):
        import grasp_summary as summary
        integer(per_block, 1, 200)
        if sections is None:
            sections = [k for k in summary.SECTIONS if k not in summary.OPTIONAL]
        if (not isinstance(sections, list) or not sections or len(sections) > len(summary.SECTIONS)
                or any(not isinstance(k, str) or k not in summary.SECTIONS for k in sections)):
            raise ValueError(T("请至少选择一个摘要部分"))
        root = self.path(directory, directory=True)
        jobs = []
        if (root / core.SESSION_FILE).exists():
            jobs = self.record(directory)[1].get("pipeline", [])
        cfg = config.export(jobs, dict(title=root.name), validate=False) if jobs else None
        selected = [k for k in sections if cfg is not None or k != "configuration"]
        # 只使用服务器状态文件；桌面会话中的参数指纹与运行记录不属于该服务器。
        result = summary.collect(str(root), cfg, jobs, history=(), per_block=per_block, cancel=self.cancel)
        return dict(text=summary.render(result, selected), has_configuration=cfg is not None)

    def run_status(self, directory):
        return self.status(directory)

    def run_plan(self, directory, server_type="workstation"):
        _, data, revision = self.record(directory)
        cfg = config.export(data.get("pipeline", []), {})
        if server_type == "slurm":
            settings = dict(core.SLURM_DEFAULTS, **core.load_config().get("slurm", {}))
            settings["bindir"] = core.default_bindir()
            return dict(revision=revision, custom_commands=config.custom_commands(cfg), server_type="slurm",
                        slurm=settings, logout_kills=False, lingering=False)
        if server_type != "workstation":
            raise ValueError("invalid server type")
        linger = False
        try:
            result = subprocess.run(["loginctl", "show-user", pwd.getpwuid(os.getuid()).pw_name,
                                     "-p", "Linger", "--value"], capture_output=True, text=True,
                                    timeout=3, env=core.child_env())
            linger = result.returncode == 0 and result.stdout.strip() == "yes"
        except (OSError, subprocess.SubprocessError):
            pass  # 无 systemd 的服务器不提供 scope 选项，仍可按普通方式运行。
        return dict(revision=revision, custom_commands=config.custom_commands(cfg),
                    logout_kills=bg.logout_kills(), lingering=linger, run_settings=core.saved_run_settings())

    def run(self, directory, revision, start=1, allow_custom=False, user_scope=False, accept_logout=False,
            server_type="workstation", slurm=None):
        for flag in (allow_custom, user_scope, accept_logout):
            if type(flag) is not bool:
                raise ValueError("confirmation must be boolean")
        integer(start, 1, 100000)
        root = self.path(directory, directory=True)
        with sessions.locked(root):
            if self.record(directory)[2] != revision:
                raise sessions.Conflict(T("工作目录记录已被其他程序修改，请重新载入"))
            if server_type == "slurm":
                import grasp_slurm
                import grasp_workdir
                import grasp_summary
                _, data, _ = self.record(directory)
                jobs = data.get("pipeline", [])
                if not 1 <= start <= len(jobs) or not isinstance(slurm, dict):
                    raise ValueError("invalid Slurm submission")
                cfg = config.export(jobs, {}, validate=False)
                service.Service("/", allow_custom=allow_custom).custom(cfg)
                settings = {k: str(slurm.get(k, v)).strip() for k, v in core.SLURM_DEFAULTS.items()}
                # 该入口只提交单个普通作业；数组会改变取消和状态归属，不能悄悄当成单作业。
                if re.search(r"(?:^|\s)(?:-*array(?:=|\s)|-a(?:\d|\s|=))", settings["extra"]):
                    raise ValueError(T("此处不支持 Slurm 作业数组"))
                if not all(re.fullmatch(r"[1-9][0-9]*", settings[k]) for k in ("nodes", "ntasks")):
                    raise ValueError(T("Slurm 节点数和每节点进程数必须为正整数"))
                count = int(settings["nodes"]) * int(settings["ntasks"])
                if count > 100000:
                    raise ValueError(T("Slurm 总进程数不能超过 100000"))
                temporary = settings["mpitmp"] or "$SLURM_SUBMIT_DIR/tmp_mpi"
                variables = dict(SLURM_SUBMIT_DIR=str(root), HOME=str(Path.home()))
                temporary = re.sub(r"\$\{(SLURM_SUBMIT_DIR|HOME)\}|\$(SLURM_SUBMIT_DIR|HOME)(?![A-Za-z0-9_])",
                                   lambda match: variables[match[1] or match[2]], temporary)
                if temporary.startswith("~/"):
                    temporary = str(Path.home() / temporary[2:])
                run = dict(bindir=settings["bindir"], np=count if settings["mpirun"] else 1,
                           mpi=bool(settings["mpirun"]), mpitmp=temporary, nice=False)
                checked = grasp_summary.check_files(str(root), cfg["jobs"], start=start - 1,
                                                     run=run, path_check=self.path)
                if checked["invalid"] or checked["missing"]:
                    raise service.PreflightError(checked)
                grasp_workdir.idle(root)
                selected = [config.make_job(j) for j in cfg["jobs"][start - 1:]]
                script = grasp_slurm.batch_script(selected, settings)
                return grasp_slurm.submit(root, script, bg.status(str(root)))
            if server_type != "workstation" or slurm is not None:
                raise ValueError("invalid server type")
            plan = self.run_plan(directory)
            if user_scope and not plan["lingering"]:
                raise ValueError(T("服务器未开启 lingering，不能使用用户 scope"))
            if plan["logout_kills"] and not user_scope and not accept_logout:
                raise ValueError(T("断线会结束计算，请由用户选择用户 scope、Slurm 或明确照常启动"))
            operation = service.Service("/", allow_custom=allow_custom)
            return operation.run(directory, start, started_by="remote", user_scope=user_scope)

    def signature(self, directory):
        root = self.directory_files(directory)
        rows = []
        for p in root.iterdir():
            if p.name.startswith(".grasp_gui"):
                continue
            st = p.stat()
            rows.append([str(p), st.st_size, st.st_mtime_ns])
            if len(rows) > 20000:
                raise ValueError(T("远程目录中文件过多，请选择更小的目录"))
        for child in (root / ".grasp_gui" / "tr").rglob("*"):
            self.path(str(child))
            if child.is_file():
                st = child.stat()
                rows.append([str(child), st.st_size, st.st_mtime_ns])
            if len(rows) > 20000:
                raise ValueError(T("远程目录中文件过多，请选择更小的目录"))
        import hashlib
        return hashlib.sha256(json.dumps(sorted(rows)).encode()).hexdigest()

    def wavefunctions(self, directory, files=None, names=None, points=1000, limit=100):
        import grasp_wfn as wfn
        integer(points, 2, 10000)
        integer(limit, 1, 100)
        self.selection(files, names)
        root = self.directory_files(directory)
        files = files if files is not None else wfn.find_w_files(str(root))
        curves = []
        for name in files:
            path = self.path(str(root / name))
            for row in wfn.sampled_w(path, names, points):
                curves.append(dict(row, file=path.name))
                if len(curves) >= limit:
                    return dict(curves=curves, truncated=True)
        return dict(curves=curves, truncated=False)

    def composition(self, directory, file, block, index, limit=500):
        import grasp_mix as mix
        integer(limit, 1, 500)
        integer(block, 1, 1000000)
        integer(index, 1, 1000000)
        root = self.directory_files(directory)
        path = self.path(str(root / file))
        blocks = mix.open_mix(path, summary=True, k=limit, cancel=self.cancel)
        b = next(b for b in blocks if b.nb == block)
        lv = next(lv for lv in b.levels if lv.pos == index)
        csf = mix.find_csf_for(str(path))
        csfs = mix.open_csfs(csf, cancel=self.cancel) if csf else None
        top = lv.top_components(limit)
        texts = csfs[b.nb - 1].read_many([i - 1 for i, _ in top], self.cancel) if csfs else {}
        return dict(components=[dict(csf=i, coefficient=c, configuration=texts.get(i - 1, (None, None))[0],
                                     coupling=texts.get(i - 1, (None, None))[1]) for i, c in top])

    @staticmethod
    def selection(files, names):
        if files is not None and (not isinstance(files, list) or len(files) > 100 or
                                  any(not isinstance(x, str) for x in files)):
            raise ValueError("invalid file list")
        if names is not None and (not isinstance(names, list) or len(names) > 100 or
                                  any(not isinstance(x, str) for x in names)):
            raise ValueError("invalid orbital list")

    def view(self, directory, kind, files=None, names=None, points=1000, limit=10000, known_signature=None, emit=None):
        integer(limit, 1, 20000)
        integer(points, 2, 10000)
        self.selection(files, names)
        signature = self.signature(directory)
        if signature == known_signature:
            return dict(unchanged=True, signature=signature)
        if kind == "levels":
            import grasp_mix as mix
            catalog = [os.path.basename(p) for p in mix.find_mix_files(str(self.path(directory, directory=True)))]
            if emit:
                brief = self.levels(directory, files, limit, 0, brief=True, cancel=self.cancel)
                emit(dict(kind=kind, data=dict(brief, files=catalog)))
            data = self.levels(directory, files, limit, 3, cancel=self.cancel)
            data["files"] = catalog
            data["transitions"] = self.transitions(directory, limit=1000)["transitions"]
        elif kind == "wfn":
            metadata = self.orbitals(directory, files, limit=1000)
            chosen = names if names is not None else list(dict.fromkeys(o["name"] for o in metadata["orbitals"]))[:5]
            if emit:
                coarse = self.wavefunctions(directory, files, chosen, min(points, 80))
                coarse["truncated"] |= metadata["truncated"]
                emit(dict(kind=kind, data=dict(metadata, **coarse)))
            curves = self.wavefunctions(directory, files, chosen, points)
            curves["truncated"] |= metadata["truncated"]
            data = dict(metadata, **curves)
        else:
            raise ValueError("unknown view")
        if signature != self.signature(directory):
            raise ValueError(T("读取期间远程文件已改变，请刷新"))
        return dict(signature=signature, data=data)


class Server:
    def __init__(self, source=None, target=None):
        self.source = source or sys.stdin.buffer
        self.target = target or sys.stdout.buffer
        self.cancel = threading.Event()
        self.lock = threading.Lock()
        self.mutations = threading.Lock()
        self.tasks = queue.Queue(maxsize=64)
        self.watching = None
        self.operation = Operations(self.cancel)

    def send(self, value):
        raw = pack(value)
        with self.lock:
            self.target.write(raw)
            self.target.flush()

    def watch(self, directory):
        root = self.operation.path(directory, directory=True)
        state = self.operation.status(directory)["state"]
        path = self.operation.log_path(directory, state)
        offset, inode, text = 0, None, ""
        if path.is_file():
            with path.open("rb") as f:
                inode = os.fstat(f.fileno()).st_ino
                f.seek(0, 2)
                offset = f.tell()
                f.seek(max(0, offset - service.MAX_TEXT))
                text = f.read(offset - f.tell()).decode("utf-8", "replace")
                text = "".join(text.splitlines(keepends=True)[-100:])
        self.watching = dict(directory=directory, offset=offset, state=state,
                             run=((state or {}).get("start"), (state or {}).get("pid"),
                                  (state or {}).get("job_id")), inode=inode,
                             decoder=codecs.getincrementaldecoder("utf-8")("replace"))
        return dict(state=state, text=text, truncated=offset > service.MAX_TEXT)

    def push(self):
        while not self.cancel.wait(0.2):
            item = self.watching
            if item is None:
                continue
            try:
                state = self.operation.status(item["directory"])["state"]
                identity = (state or {}).get("start"), (state or {}).get("pid"), (state or {}).get("job_id")
                new_run = identity != item["run"]
                if state != item["state"]:
                    if new_run:
                        item["offset"] = 0
                    item["run"], item["state"] = identity, state
                    self.send(dict(event="state", directory=item["directory"], state=state))
                path = self.operation.log_path(item["directory"], state)
                if not path.is_file():
                    continue
                st = path.stat()
                reset = new_run or item["offset"] > st.st_size or item["inode"] not in (None, st.st_ino)
                if reset:
                    item["offset"] = 0
                    item["decoder"].reset()
                item["inode"] = st.st_ino
                with path.open("rb") as f:
                    f.seek(item["offset"])
                    raw = f.read(65536)
                    item["offset"] = f.tell()
                if raw:
                    self.send(dict(event="log", directory=item["directory"], text=item["decoder"].decode(raw),
                                   reset=reset))
            except (OSError, ValueError):
                continue  # 计算刚启动或轮换日志时下次本地检查重试，不向桌面发轮询请求。

    def worker(self):
        while not self.cancel.is_set():
            try:
                request = self.tasks.get(timeout=0.2)
            except queue.Empty:
                continue
            ident = request.get("id")
            try:
                method, args = request["method"], request.get("args", {})
                if method not in READ_ONLY | MUTATIONS or not isinstance(args, dict):
                    raise ValueError("unsupported remote method or arguments")
                if type(ident) not in (int, str):
                    raise ValueError("invalid request id")
                if method == "watch":
                    function = self.watch
                else:
                    function = getattr(self.operation, method)
                if method == "view":
                    args = dict(args, emit=lambda value: self.send(dict(event="progress", id=ident, **value)))
                inspect.signature(function).bind(**args)
                if method in MUTATIONS:
                    with self.mutations:
                        result = function(**args)
                else:
                    result = function(**args)
                self.send(dict(id=ident, result=result))
            except Exception as e:
                try:
                    self.send(dict(id=ident, error=str(e)[:service.MAX_TEXT]))
                except (OSError, ValueError):
                    self.cancel.set()

    def serve(self):
        self.target.write((GREETING + "\n").encode())
        self.target.flush()
        for _ in range(2):
            threading.Thread(target=self.worker, daemon=True).start()
        threading.Thread(target=self.push, daemon=True).start()
        try:
            while True:
                raw = self.source.readline(MAX_FRAME + 1)
                if not raw:
                    return 0
                if len(raw) > MAX_FRAME:
                    while raw and not raw.endswith(b"\n"):
                        raw = self.source.readline(MAX_FRAME + 1)
                    self.send(dict(id=None, error="remote frame too large"))
                    continue
                try:
                    request = unpack(raw)
                    if not isinstance(request, dict):
                        raise ValueError("request must be an object")
                    self.tasks.put_nowait(request)
                except (ValueError, RecursionError, queue.Full) as e:
                    self.send(dict(id=None, error=str(e)))
        finally:
            self.cancel.set()


def main(argv=None):
    argparse.ArgumentParser(description="GRASP GUI remote stdio").parse_args(argv)
    core.set_lang(core.load_config().get("lang", core.LANG))
    try:
        return Server().serve()
    except (BrokenPipeError, KeyboardInterrupt):
        return 0
