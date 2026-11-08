"""用户选择的文件清单与系统 rsync 传输；只用标准库。"""

import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import signal
import subprocess
import threading

import grasp_core as core
from grasp_core import T

DEFAULT_THRESHOLD = 100_000_000


def relative(name):
    if not isinstance(name, str):
        raise ValueError(T("传输文件必须是目录内的相对路径"))
    path = PurePosixPath(name)
    if not path.parts or path.is_absolute() or ".." in path.parts or "\0" in name:
        raise ValueError(T("传输文件必须是目录内的相对路径"))
    return path.as_posix()


def scan(directory, names=None):
    root = Path(directory).resolve(strict=True)
    rows, skipped, seen = [], [], set()
    names = sorted(p.name for p in root.iterdir()) if names is None else names
    if not isinstance(names, list) or len(names) > 50000:
        raise ValueError(T("文件太多，请缩小选择范围"))
    def visit(name):
        name = relative(name)
        if name in seen:
            return
        seen.add(name)
        if len(seen) > 50000:
            raise ValueError(T("文件太多，请缩小选择范围"))
        path, parent = root / name, root
        for part in PurePosixPath(name).parts:
            parent /= part
            if parent.is_symlink():
                skipped.append(name)
                return
        info = path.lstat()
        if path.is_dir():
            for child in sorted(path.iterdir()):
                visit(name + "/" + child.name)
        elif path.is_file():
            rows.append(dict(name=name, size=info.st_size, mtime_ns=info.st_mtime_ns))
        else:
            skipped.append(name)
    for name in names:
        visit(name)
    return dict(directory=str(root), files=rows, skipped=skipped)


def safe_targets(directory, names):
    root = Path(directory).resolve()
    for name in names:
        path = root
        for part in PurePosixPath(relative(name)).parts:
            path /= part
            if path.is_symlink():
                raise ValueError(T("目标路径包含符号链接，请换一个目录：{p}", p=str(path)))
        if (path.parent / ".grasp-rsync-partial").is_symlink():
            raise ValueError(T("目标路径包含符号链接，请换一个目录：{p}", p=str(path.parent)))


def capabilities(program="rsync"):
    if not isinstance(program, str) or (program != "rsync" and not program.startswith(("/", "~/"))):
        raise ValueError(T("rsync 请填写完整路径或使用 rsync"))
    path = shutil.which(program) if program == "rsync" else str(Path(program).expanduser())
    if not path or not os.path.isfile(path) or not os.access(path, os.X_OK):
        raise ValueError(T("找不到 rsync；请安装系统 rsync 或设置完整路径"))
    version = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=5)
    help_result = subprocess.run([path, "--help"], capture_output=True, text=True, timeout=5)
    text = help_result.stdout + help_result.stderr
    lines = (version.stdout or version.stderr).splitlines()
    version = lines[0] if lines else "rsync --version: no output"
    needed = ("--partial", "--partial-dir", "--checksum", "--files-from", "--from0", "--safe-links",
              "--ignore-existing", "--whole-file", "--ignore-times")
    missing = [flag for flag in needed if not re.search(re.escape(flag) + r"(?=[= ,\n]|$)", text)]
    if missing:
        raise ValueError(T("此 rsync 缺少续传或校验所需选项，请安装 GNU rsync：{v}；缺少 {p}",
                           v=version[:200], p=", ".join(missing)))
    match = re.search(r"version (\d+)\.(\d+)", version)
    return dict(path=path, version=version[:200], progress2=bool(match and tuple(map(int, match.groups())) >= (3, 1)),
                protect="--protect-args" in text or "--secluded-args" in text)


def totals(rows, selected, threshold=DEFAULT_THRESHOLD):
    result = {key: dict(count=0, size=0) for key in ("small", "large", "skipped_large")}
    for row in rows:
        large = row["size"] > threshold
        key = ("large" if large else "small") if row["name"] in selected else "skipped_large" if large else None
        if key:
            result[key]["count"] += 1
            result[key]["size"] += row["size"]
    return result


def download_directory(host, directory):
    label = re.sub(r"[^\w.@-]", "_", host)
    return str(Path.home() / "grasp_calcs" / "remote" / label / (PurePosixPath(directory).name or "root"))


def arguments(client, local, remote, names, local_caps, remote_caps, upload=False, policy="skip"):
    if policy not in ("skip", "resume", "overwrite"):
        raise ValueError("invalid overwrite policy")
    if not names or len(names) > 50000:
        raise ValueError(T("请先选择要传输的文件"))
    names = [relative(name) for name in names]
    # 沿用同一 Client 的 SSH 参数；丢失 master 时明确失败，不能另行认证。
    base = client.argv()[:-3]
    shared, options = [], iter(base[1:])
    for option in options:
        if option == "-J":
            next(options, None)
        elif not option.startswith("-J"):
            shared.append(option)
    # 跳板路由已经由 master 建好；子会话去掉 -J，避免它与禁止新连接的 ProxyCommand 冲突。
    rsh = [base[0], "-o", "ControlMaster=no", "-o", "ProxyCommand=false", "-o", "BatchMode=yes", *shared]
    args = [local_caps["path"], "-rt", "--checksum", "--partial", "--partial-dir=.grasp-rsync-partial",
            "--safe-links", "--files-from=-", "--from0", "--timeout=60", "-e", shlex.join(rsh),
            "--rsync-path=" + shlex.quote(remote_caps["path"])]
    if local_caps["protect"] and remote_caps["protect"]:
        args.append("-s")
    elif any(not re.fullmatch(r"[\w/.-]+", value) for value in [remote, *names]):
        raise ValueError(T("含空格或特殊字符的路径需要两端 rsync 支持 --protect-args"))
    args.append("--info=progress2" if local_caps["progress2"] and remote_caps["progress2"] else "--progress")
    if policy == "skip":
        args.append("--ignore-existing")
    elif policy == "overwrite":
        args += ["--ignore-times", "--whole-file"]
    remote_path, local_path = client.host + ":" + remote.rstrip("/") + "/", str(Path(local).resolve()) + "/"
    args += ["--", *([local_path, remote_path] if upload else [remote_path, local_path])]
    return args, b"\0".join(name.encode("utf-8") for name in names) + b"\0"


def progress(line):
    match = re.search(r"([\d,]+)\s+(\d+)%\s+(\S+)\s+(\d+:\d+:\d+)", line)
    if match:
        return dict(bytes=int(match[1].replace(",", "")), percent=int(match[2]), speed=match[3], eta=match[4])
    return None


class Transfer:
    def __init__(self, client):
        self.client, self.process = client, None
        self.cancelled = threading.Event()

    def cancel(self):
        self.cancelled.set()
        process = self.process
        if process is not None and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGINT)
            except ProcessLookupError:
                pass  # 取消恰逢完成，无需再次发信号。

    def run(self, local, remote, rows, upload=False, policy="skip", rsync="rsync", notify=None):
        if self.cancelled.is_set():
            raise InterruptedError(T("传输已取消，已传的部分可在下次续传"))
        if self.client.fallback or not self.client.connected or not Path(self.client.control_path).exists():
            raise ValueError(T("文件传输需要现有 SSH 连接复用，请先重新连接"))
        local_caps, names = capabilities(), [row["name"] for row in rows]
        request = dict(directory=remote, names=[] if upload else names, rsync=rsync)
        if upload:
            request["targets"] = names
        checked = self.client.request("transfer_files", request).result(180)
        if self.cancelled.is_set():
            raise InterruptedError(T("传输已取消，已传的部分可在下次续传"))
        fresh = scan(local, names)["files"] if upload else checked["files"]
        if sorted(fresh, key=lambda r: r["name"]) != sorted(rows, key=lambda r: r["name"]):
            raise ValueError(T("文件在确认后已改变，请刷新清单后重新选择"))
        if not upload:
            safe_targets(local, names)
            Path(local).mkdir(parents=True, exist_ok=True)
        args, manifest = arguments(self.client, local, remote, names, local_caps, checked["rsync"], upload, policy)
        env = self.client.environment()
        env["LC_ALL"] = "C"
        process = self.process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                                  stderr=subprocess.PIPE, env=env, start_new_session=True)
        if self.cancelled.is_set():
            self.cancel()
        errors = bytearray()
        def stderr():
            for data in iter(lambda: process.stderr.read(4096), b""):
                errors.extend(data)
                if len(errors) > 65536:
                    del errors[:-65536]
        def send():
            try:
                process.stdin.write(manifest)
                process.stdin.close()
            except (OSError, ValueError):
                pass  # 由退出码和 stderr 显示实际失败原因。
        reader, writer = threading.Thread(target=stderr, daemon=True), threading.Thread(target=send, daemon=True)
        reader.start()
        writer.start()
        try:
            line = bytearray()
            for data in iter(lambda: process.stdout.read(1), b""):
                if data in (b"\r", b"\n"):
                    state = progress(line.decode("utf-8", "replace"))
                    if state and notify:
                        notify(state)
                    line.clear()
                elif len(line) < 4096:
                    line.extend(data)
            code = process.wait()
            reader.join(2)
            if self.cancelled.is_set():
                raise InterruptedError(T("传输已取消，已传的部分可在下次续传"))
            if code:
                diagnostic = errors.decode("utf-8", "replace")
                if any(text in diagnostic for text in ("Session open refused", "session request failed",
                                                        "administratively prohibited")):
                    raise RuntimeError(T("服务器拒绝传输会话，请管理员将 MaxSessions 调大；现有分析会话仍可使用"))
                raise RuntimeError(T("rsync 传输失败：{e}", e=diagnostic or str(code)))
            return dict(count=len(rows), size=sum(row["size"] for row in rows))
        finally:
            if process.poll() is None:
                self.cancel()
                try:
                    process.wait(5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
            writer.join(2)
            reader.join(2)
            for stream in (process.stdin, process.stdout, process.stderr):
                try:
                    stream.close()
                except OSError:
                    pass  # 不能用管道关闭错误覆盖原始传输错误。
            self.process = None
