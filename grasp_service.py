"""供 MCP 与后续远程服务共用的本地计算操作；不导入 Qt。"""

import os
from pathlib import Path
import re
import threading

import grasp_bg as bg
import grasp_config as config
import grasp_core as core
import grasp_session as sessions
import grasp_summary as summary
import grasp_workdir as work
from grasp_core import T

MAX_TEXT = 65536
MAX_ROWS = 1000


class PreflightError(ValueError):
    """保留可读报告和结构化问题，供协议层返回；运行尚未启动。"""

    def __init__(self, checked):
        super().__init__(summary.check_text(checked))
        self.data = dict(started=False, **checked)


class Service:
    def __init__(self, root, allow_custom=False):
        self.root = Path(root).expanduser().resolve()
        self.allow_custom = allow_custom

    def path(self, value=".", directory=False):
        if not isinstance(value, str) or ".." in Path(value).parts:
            raise ValueError(T("路径必须位于允许的根目录中"))
        candidate = Path(value).expanduser()
        candidate = (self.root / candidate).resolve()
        if not candidate.is_relative_to(self.root):
            raise ValueError(T("路径必须位于允许的根目录中"))
        if directory and not candidate.is_dir():
            raise ValueError(T("工作目录不存在：{d}", d=str(candidate)))
        return candidate

    def record(self, directory):
        root = self.path(directory, directory=True)
        self.path(str(root / core.SESSION_FILE))
        data, revision = sessions.read(root)
        if data is None:
            raise ValueError(T("此目录没有工作目录记录"))
        return root, data, revision

    def custom(self, cfg):
        if not self.allow_custom and config.custom_commands(cfg):
            raise ValueError(T("自定义命令默认拒绝；请由用户用 --allow-custom 启动服务"))

    def validate_config(self, configuration):
        cfg, warnings = config.check(configuration, validate=False)
        # 配置中的额外动作不参与推算，实际导入会按表单参数重新生成。
        jobs = [dict(step=j.get("step"), vals=j.get("vals"), group=j.get("group")) for j in cfg["jobs"]]
        checked = summary.check_files(None, jobs)
        return dict(valid=not checked["invalid"], issues=checked["issues"], warnings=warnings,
                    custom_commands=config.custom_commands(cfg))

    def list_steps(self):
        return dict(steps=[dict(key=s.key, name=T(s.name), program=s.prog, mpi=s.mpi) for s in core.STEPS])

    def describe_step(self, step):
        if step not in config.STEPS:
            raise ValueError(T("程序页不存在：{step}", step=step))
        s = config.STEPS[step]
        fields = []
        for f in s.fields:
            if not isinstance(f, core.Field):
                continue
            row = dict(key=f.key, type=config.field_type(f), label=T(f.label), default=f.default)
            for key in ("hint", "options", "enabled_by", "added"):
                value = getattr(f, key, None)
                if value:
                    row[key] = value
            fields.append(row)
        return dict(key=s.key, name=T(s.name), program=s.prog, fields=fields)

    def list_examples(self):
        return dict(examples=[dict(id=c.get("id"), title=config.text(c, "title"),
                                  summary=config.text(c, "summary"), runtime=config.text(c, "runtime"))
                              for c in config.list_demos()])

    def configuration(self, configuration=None, example=None):
        if (configuration is None) == (example is None):
            raise ValueError(T("必须且只能提供 config 或 example 之一"))
        if example is not None:
            matches = [c for c in config.list_demos() if c.get("id") == example]
            if not matches:
                raise ValueError(T("示例不存在：{name}", name=example))
            configuration = matches[0]
        cfg, warnings = config.check(configuration)
        self.custom(cfg)
        return cfg, warnings

    def create_workdir(self, directory, configuration=None, example=None):
        root = self.path(directory)
        cfg, warnings = self.configuration(configuration, example)
        config.install(cfg, str(root))
        _, revision = sessions.read(root)
        return dict(directory=str(root), revision=revision, warnings=warnings)

    def get_pipeline(self, directory):
        root, data, revision = self.record(directory)
        cfg = config.export(data.get("pipeline", []), dict(title=root.name))
        return dict(directory=str(root), revision=revision, config=cfg)

    def update_workdir(self, directory, revision, configuration):
        root = self.path(directory, directory=True)
        cfg, warnings = self.configuration(configuration)
        with sessions.locked(root):
            _, _, current = self.record(directory)
            if revision != current:
                raise sessions.Conflict(T("工作目录记录已被其他程序修改，请重新载入"))
            plan = work.prepare_update(root, cfg)
            # 文件冲突统一保留；覆盖需要用户在 GUI 中查看差异并明确选择。
            work.apply_update(plan)
            _, current = sessions.read(root)
        return dict(directory=str(root), revision=current, changes=plan["text"], warnings=warnings,
                    kept_files=[f["name"] for f in plan["files"] if f["kind"] == "conflict"])

    def export_script(self, directory, slurm=None):
        root, data, _ = self.record(directory)
        jobs = data.get("pipeline", [])
        if slurm is not None:
            text = core.slurm_script(jobs, slurm)
        else:
            settings = core.saved_run_settings()
            mpi = f"{settings['mpirun']} -np {settings['np']}" if settings["np"] > 1 else ""
            text = core.shell_script(jobs, settings["bindir"], str(root), mpi, settings["mpitmp"])
        return dict(text=text[:MAX_TEXT], truncated=len(text) > MAX_TEXT)

    def run(self, directory, start=1, force=False, *, started_by="mcp", user_scope=False):
        root, data, _ = self.record(directory)
        jobs = data.get("pipeline", [])
        if not 1 <= start <= len(jobs):
            raise ValueError(T("起始步骤超出流程范围"))
        if type(force) is not bool:
            raise ValueError("force must be boolean")
        cfg = config.export(jobs, dict(title=root.name), validate=False)
        self.custom(cfg)
        settings = dict(core.saved_run_settings(), cwd=str(root))
        checked = summary.check_files(str(root), cfg["jobs"], start=start - 1, run=settings, path_check=self.path)
        if checked["invalid"] or checked["missing"] and not force:
            raise PreflightError(checked)
        # 表单参数是依据，不能让记录中伪造的 prog/pre 绕过自定义命令限制。
        jobs = [config.make_job(j) for j in cfg["jobs"][start - 1:]]
        for name in (bg.RUN_REQUEST, bg.RUN_STATE, bg.RUN_STATE + ".tmp", bg.RUN_LOG):
            path = self.path(str(root / name))
            if path.exists() and not path.is_file():
                raise ValueError(T("工作目录记录的格式有误"))
        for job in jobs:
            if job.get("log"):
                self.path(str(root / job["log"]))
            for action in job.get("pre", []) + job.get("post", []):
                if action[0] == "cp":
                    self.path(str(root / action[1]))
                    self.path(str(root / action[2]))
        work.idle(root)
        import tempfile
        # scope 启动失败时保留 systemd 原始诊断；文件不构成与后台进程相连的管道。
        diagnostic = tempfile.TemporaryFile() if user_scope else None
        try:
            options = dict(started_by=started_by, user_scope=user_scope)
            if diagnostic is not None:
                options["stderr"] = diagnostic
            proc = bg.launch(str(root), jobs, settings, **options)
            threading.Thread(target=proc.wait, daemon=True).start()
            connection = bg.connect(str(root), timeout=bg.ATTACH_WAIT)
            if connection is None and diagnostic is not None:
                diagnostic.seek(0)
                message = diagnostic.read(MAX_TEXT).decode("utf-8", "replace").strip()
                if message:
                    raise ValueError(message)
        finally:
            if diagnostic is not None:
                diagnostic.close()
        if connection is None:
            raise ValueError(T("无法连接后台运行进程，请检查运行状态和日志"))
        try:
            self.handshake(connection).close()
        finally:
            connection.close()
        return dict(started=True, directory=str(root), from_step=start, started_by=started_by)

    def handshake(self, connection):
        """先读初始状态，后台才能确认客户端已连上；不能连上后立刻关闭。"""
        connection.settimeout(bg.ATTACH_WAIT)
        stream = connection.makefile("rb")
        try:
            for raw in stream:
                import json
                if "step" in json.loads(raw):
                    return stream
            raise ValueError(T("无法连接后台运行进程，请检查运行状态和日志"))
        except Exception:
            stream.close()
            raise

    def status(self, directory):
        root = self.path(directory, directory=True)
        path = self.path(str(root / bg.RUN_STATE))
        if path.exists() and not path.is_file():
            raise ValueError(T("工作目录记录的格式有误"))
        return dict(state=bg.status(str(root)))

    def tail_log(self, directory, lines=100):
        root = self.path(directory, directory=True)
        path = self.path(str(root / bg.RUN_LOG))
        if path.exists() and not path.is_file():
            raise ValueError(T("工作目录记录的格式有误"))
        text = bg.log_tail(str(root), min(lines, 1000))
        return dict(text=text[-MAX_TEXT:], truncated=len(text) > MAX_TEXT)

    def stop(self, directory):
        root = self.path(directory, directory=True)
        st = self.status(directory)["state"]
        if st and st.get("remote") and st.get("status") == "running":
            raise ValueError(T("计算在另一台机器上运行，请在那台机器上停止"))
        connection = bg.connect(str(root))
        if connection is None:
            return dict(stopped=False, reason=T("没有正在运行的任务"))
        import json
        import socket
        stream = None
        try:
            stream = self.handshake(connection)
            connection.sendall(b"stop\n")
            connection.settimeout(5)
            for raw in stream:
                message = json.loads(raw)
                if "done" in message:
                    return dict(stop_requested=True, confirmed=True, status=message["done"])
        except socket.timeout:
            # 停止本来就是异步请求；没有收到完成消息时不能声称已经停止。
            return dict(stop_requested=True, confirmed=False)
        finally:
            if stream is not None:
                stream.close()
            connection.close()
        return dict(stop_requested=True, confirmed=False)

    def directory_files(self, directory):
        root = self.path(directory, directory=True)
        # 配对函数也会查看邻近候选文件；先检查链接，防止借候选文件越过根目录。
        for p in root.iterdir():
            self.path(str(p))
        return root

    @staticmethod
    def file_type(path):
        import grasp_mix as mix
        import grasp_wfn as wfn

        if mix.is_mix_file(path):
            return "mixing"
        if wfn.is_wfn_file(path):
            return "wavefunctions"
        with path.open("rb") as f:
            head = f.read(512)
        if head.startswith(b"Core subshells:"):
            return "csf"
        if re.search(rb"\bf[12]?\s*=", head):
            return "transitions"
        return None

    def list_results(self, directory):
        root = self.directory_files(directory)
        rows = []
        for path in sorted(root.iterdir()):
            if not path.is_file():
                continue
            kind = self.file_type(path)
            if kind:
                st = path.stat()
                rows.append(dict(name=path.name, type=kind, size=st.st_size, mtime_ns=st.st_mtime_ns))
        return dict(files=rows[:MAX_ROWS], truncated=len(rows) > MAX_ROWS)

    def levels(self, directory, files=None, limit=200, components=3, brief=False, cancel=None, progress=None):
        import grasp_mix as mix

        root = self.directory_files(directory)
        names = files if files is not None else mix.find_mix_files(str(root))
        rows = []
        truncated = False
        for name in names:
            path = self.path(str(root / name))
            blocks = mix.open_mix(path, summary=not brief, k=max(components, 1), cancel=cancel, progress=progress)
            csf_path = None if brief else mix.find_csf_for(str(path))
            csfs = mix.open_csfs(csf_path, cancel, progress) if csf_path and components and not brief else None
            lsj_path, labels = (None, {}) if brief else mix.find_lsj_for(str(path), blocks)
            core_orbitals = mix.csf_core(csf_path) if csf_path else []
            for b in blocks:
                for lv in b.levels:
                    if len(rows) >= limit:
                        return dict(levels=rows, truncated=True)
                    import grasp_index
                    grasp_index.checkpoint(cancel)
                    top = lv.top_components(max(components, 1)) if not brief else []
                    parts = []
                    for i, c in top[:components]:
                        text = csfs[b.nb - 1][i - 1] if csfs else None
                        parts.append(dict(csf=i, coefficient=c, weight=c * c,
                                          configuration=text[0] if text else None,
                                          coupling=text[1] if text else None))
                    rows.append(dict(file=path.name, block=b.nb, index=lv.pos, J=lv.J,
                                     parity=lv.parity, energy_hartree=lv.energy, ncf=b.ncf,
                                     csf_file=os.path.basename(csf_path) if csf_path else None,
                                     lsj_file=os.path.basename(lsj_path) if lsj_path else None,
                                     core_orbitals=core_orbitals,
                                     purity_percent=100 * top[0][1] ** 2 if top else 0,
                                     components=parts, lsj=labels.get((b.nb, lv.pos))))
        return dict(levels=rows, truncated=truncated)

    def transitions(self, directory, files=None, limit=200):
        import json
        import grasp_trans as trans

        root = self.directory_files(directory)
        groups = []
        if files is not None:
            for name in files:
                groups.append((name, {}, trans.parse_ct(self.path(str(root / name)))))
        else:
            cache = self.path(str(root / trans.CACHE_DIR))
            if cache.is_dir():
                for folder in cache.iterdir():
                    self.path(str(folder))
                    if not folder.is_dir():
                        continue
                    path = self.path(str(folder / "manifest.json"))
                    if not path.is_file():
                        continue
                    manifest = json.loads(path.read_bytes())
                    for side in (manifest.get("inputs") or {}).values():
                        for stamp in side.values():
                            self.path(stamp[0])
                    for rid, run in manifest.get("runs", {}).items():
                        for name in run.get("kinds", {}).values():
                            self.path(str(folder / rid / name))
            groups = [(None, roles, rows) for roles, rows in trans.computed(str(root))]
        rows = []
        for name, roles, transitions in groups:
            for t in transitions:
                upper = (roles.get(t.upper[0], t.upper[0]), *t.upper[1:])
                lower = (roles.get(t.lower[0], t.lower[0]), *t.lower[1:])
                a, gf, strength = t.main()
                rows.append(dict(file=name, kind=t.kind, upper=upper, lower=lower,
                                 wavelength_angstrom=t.wavelength if t.energy_cm else None,
                                 A=a, gf=gf, S=strength, values=t.values))
        rates = {}
        for r in rows:
            rates[r["upper"]] = rates.get(r["upper"], 0) + abs(r["A"])
        for r in rows:
            total = rates[r["upper"]]
            r["branching_ratio"] = abs(r["A"]) / total if total else None
        return dict(transitions=rows[:limit], truncated=len(rows) > limit,
                    branching_basis="computed transitions only")

    def orbitals(self, directory, files=None, limit=200):
        import grasp_wfn as wfn

        root = self.directory_files(directory)
        names = files if files is not None else wfn.find_w_files(str(root))
        rows = []
        for name in names:
            path = self.path(str(root / name))
            for identity, properties in wfn.orbital_properties(path):
                if len(rows) >= limit:
                    return dict(orbitals=rows, truncated=True)
                rows.append(dict(file=path.name, **identity, **properties))
        return dict(orbitals=rows, truncated=False)
