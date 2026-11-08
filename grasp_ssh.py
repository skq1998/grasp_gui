"""系统 OpenSSH 的单会话客户端、只读重试和有界分析缓存；不导入 Qt。"""

from concurrent.futures import Future
import glob
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import shlex
import stat
import subprocess
import sys
import tempfile
import threading
import time

import grasp_core as core
import grasp_remote as protocol
from grasp_core import T


class ServerUnavailable(ConnectionError):
    """SSH 已进入远程命令，但程序在握手前退出；不包含认证和网络错误。"""


class UnknownOutcome(ConnectionError):
    """连接丢失后不能自动重发启动/停止；用户先核对重新查询的状态。"""


def hosts(path=None, seen=None):
    path = Path(path or Path.home() / ".ssh/config").expanduser()
    seen = set() if seen is None else seen
    if path in seen:
        return []
    seen.add(path)
    result = []
    try:
        for line in path.read_text().splitlines():
            try:
                fields = shlex.split(line, comments=True)
            except ValueError:
                continue  # 别名列表仅提取可解析行，真实连接由 OpenSSH 显示配置错误。
            if not fields:
                continue
            if fields[0].lower() == "host":
                result += [x for x in fields[1:] if not any(c in x for c in "*?!")]
            elif fields[0].lower() == "include":
                for value in fields[1:]:
                    value = os.path.expanduser(value)
                    if not os.path.isabs(value):
                        value = str(path.parent / value)
                    for child in glob.glob(value):
                        result += hosts(child, seen)
    except FileNotFoundError:
        pass
    return list(dict.fromkeys(result))


def private_dir(path):
    path = Path(path)
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise OSError(T("SSH 控制目录必须属于当前用户且权限为 700"))
    return path


def cache_root():
    return Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))) / "grasp_gui"


def executable():
    return [sys.executable] if getattr(sys, "frozen", False) else [
        sys.executable, str(Path(__file__).with_name("grasp_gui.py"))]


def startup(program=""):
    if "\x00" in program or "\n" in program:
        raise ValueError("invalid server executable")
    if program and not program.startswith(("/", "~/")):
        raise ValueError(T("服务器端程序请填写完整路径或 ~/ 路径"))
    if program.startswith("~/"):
        select = 'p="$HOME"/' + shlex.quote(program[2:])
    elif program:
        select = "p=" + shlex.quote(program)
    else:
        select = ('p="$HOME/.local/bin/grasp_gui"; if [ ! -x "$p" ]; then '
                  'p=$(command -v grasp_gui) || { echo "grasp_gui not found" >&2; exit 127; }; fi')
    return select + '; "$p" --version || exit $?; export GRASP_GUI_REMOTE_EXECUTABLE="$p"; exec "$p" remote'


def modern_ssh(binary="ssh"):
    try:
        result = subprocess.run([binary, "-V"], capture_output=True, text=True, timeout=5, env=core.child_env())
        match = re.search(r"OpenSSH_(\d+)\.(\d+)", result.stderr + result.stdout)
        return bool(match and (int(match[1]), int(match[2])) >= (8, 4))
    except (OSError, subprocess.SubprocessError):
        return False


class Cache:
    MAX_BYTES = 128 << 20

    def __init__(self, identity, root=None):
        digest = hashlib.sha256(identity.encode()).hexdigest()[:24]
        self.root = private_dir((root or cache_root() / "remote") / digest)

    def path(self, key):
        return self.root / (hashlib.sha256(key.encode()).hexdigest() + ".json")

    def get(self, key):
        p = self.path(key)
        try:
            if not p.is_file() or p.is_symlink() or p.stat().st_size > protocol.MAX_FRAME:
                return None
            data = json.loads(p.read_bytes())
            if (not isinstance(data, dict) or not isinstance(data.get("signature"), str)
                    or not isinstance(data.get("data"), dict)):
                return None
            os.utime(p, None)
            return data
        except (OSError, ValueError):
            return None

    def put(self, key, value):
        raw = json.dumps(value, ensure_ascii=True, allow_nan=False).encode()
        if len(raw) > protocol.MAX_FRAME:
            return
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.root, delete=False) as f:
                temporary = f.name
                f.write(raw)
            os.replace(temporary, self.path(key))
            self.trim()
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)

    def trim(self):
        files = sorted((p for p in self.root.glob("*.json") if p.is_file() and not p.is_symlink()),
                       key=lambda p: p.stat().st_mtime_ns, reverse=True)
        size = 0
        for p in files:
            size += p.stat().st_size
            if size > self.MAX_BYTES:
                p.unlink()


class Client:
    def __init__(self, host, program="", ssh="ssh", options=(), cache=None, command=None):
        if not host or host.startswith("-") or any(c.isspace() or ord(c) < 32 for c in host):
            raise ValueError(T("请输入有效的 SSH 主机或 user@host"))
        self.host, self.program, self.ssh, self.options = host, program, ssh, tuple(options)
        self.command_override = command
        self.events, self.outbound, self.incoming = queue.Queue(), queue.Queue(), queue.Queue()
        self.pending, self.counter, self.generation = {}, 0, 0
        self.guard = threading.Lock()
        self.closed = threading.Event()
        self.process = None
        self.rtt = 0.0
        self.connected = False
        self.watched = None
        self.fallback = False
        self.control = private_dir(cache_root() / "ssh")
        if len(str(self.control)) > 65:
            self.control = Path(tempfile.mkdtemp(prefix="grasp-ssh-"))
        self.control_path = str(self.control / hashlib.sha256((host + str(options)).encode()).hexdigest()[:20])
        self.cache = cache or Cache(host + "\0" + program + "\0" + str(options))
        self.custom_cache = cache is not None
        self.askpass_path = None
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def environment(self):
        env = core.child_env()
        self.key_only = not modern_ssh(self.ssh)
        if not self.key_only:
            helper = self.control / "askpass"
            raw = "#!/bin/sh\nexec " + shlex.join([*executable(), "_askpass"]) + ' "$@"\n'
            helper.write_text(raw)
            helper.chmod(0o700)
            self.askpass_path = helper
            env.update(SSH_ASKPASS=str(helper), SSH_ASKPASS_REQUIRE="force")
        else:
            self.events.put(dict(event="notice", text=T("本机 OpenSSH 低于 8.4，仅支持密钥或 ssh-agent 登录")))
            env.pop("SSH_ASKPASS", None)
            env.pop("SSH_ASKPASS_REQUIRE", None)
        return env

    def argv(self):
        if self.command_override:
            return list(self.command_override)
        mux = ["-o", "ControlMaster=no", "-o", "ControlPath=none"] if self.fallback else [
            "-o", "ControlMaster=auto", "-o", "ControlPersist=60", "-o", "ControlPath=" + self.control_path]
        return [self.ssh, "-T", "-x", "-a", "-o", "ClearAllForwardings=yes", "-o", "ServerAliveInterval=15",
                "-o", "ServerAliveCountMax=3", "-o", "ConnectTimeout=30", *mux,
                *(["-o", "BatchMode=yes"] if getattr(self, "key_only", False) else []), *self.options,
                "--", self.host, startup(self.program)]

    def request(self, method, args=None, cached=False):
        future = Future()
        with self.guard:
            self.counter += 1
            ident = self.counter
        future.request_id = ident
        if self.closed.is_set():
            future.set_exception(ConnectionError(T("SSH 连接已关闭")))
            return future
        self.outbound.put(dict(id=ident, method=method, args=dict(args or {}), future=future, cached=cached))
        return future

    def _reader(self, process, generation):
        try:
            seen = False
            while not self.closed.is_set():
                raw = process.stdout.readline(protocol.MAX_FRAME + 1)
                if not raw:
                    break
                if not seen:
                    if raw.startswith(b"GRASP_GUI_REMOTE ") and raw.rstrip() != protocol.GREETING.encode():
                        raise ValueError(T("远程协议不兼容，请更新服务器端程序"))
                    if raw.rstrip() == protocol.GREETING.encode():
                        seen = True
                        self.incoming.put((generation, dict(event="greeting")))
                    continue
                self.incoming.put((generation, protocol.unpack(raw)))
        except (OSError, ValueError, RecursionError) as e:
            self.incoming.put((generation, dict(event="protocol_error", text=str(e))))
        self.incoming.put((generation, dict(event="eof")))

    def _stderr(self, process):
        while True:
            try:
                raw = process.stderr.read1(1024)
            except (OSError, ValueError):
                return
            if not raw:
                return
            self.diagnostics = (self.diagnostics + raw.decode("utf-8", "replace"))[-65536:]

    def _send(self, item):
        item["sent"] = time.monotonic()
        self.pending[item["id"]] = item
        self.process.stdin.write(protocol.pack(dict(id=item["id"], method=item["method"], args=item["args"])))
        self.process.stdin.flush()

    def _connect(self):
        self.generation += 1
        self.diagnostics = ""
        environment = self.environment()
        self.process = process = subprocess.Popen(self.argv(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                                 stderr=subprocess.PIPE, env=environment,
                                                 start_new_session=True)
        threading.Thread(target=self._reader, args=(process, self.generation), daemon=True).start()
        stderr = threading.Thread(target=self._stderr, args=(process,), daemon=True)
        stderr.start()
        deadline = time.monotonic() + 120
        while not self.closed.is_set() and time.monotonic() < deadline:
            try:
                generation, value = self.incoming.get(timeout=0.1)
            except queue.Empty:
                continue
            if generation != self.generation:
                continue
            if value.get("event") == "greeting":
                started = time.monotonic()
                process.stdin.write(protocol.pack(dict(id=0, method="hello", args=dict(
                    protocol=protocol.PROTOCOL, version=core.VERSION))))
                process.stdin.flush()
                break
            if value.get("event") in ("eof", "protocol_error"):
                stderr.join(timeout=0.2)
                try:
                    code = process.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    code = None
                error = ServerUnavailable if value["event"] == "eof" and code not in (None, 255) else ConnectionError
                raise error(self.diagnostics or value.get("text") or
                            T("服务器握手失败，请检查程序路径并安装或更新服务器端"))
        else:
            raise TimeoutError(T("SSH 登录或服务器握手超时"))
        while not self.closed.is_set() and time.monotonic() < deadline:
            try:
                generation, value = self.incoming.get(timeout=0.1)
            except queue.Empty:
                continue
            if generation != self.generation:
                continue
            if value.get("id") == 0:
                if "error" in value or value.get("result", {}).get("protocol") != protocol.PROTOCOL:
                    raise ConnectionError(value.get("error", T("远程协议不兼容，请更新服务器端程序")))
                self.rtt = time.monotonic() - started
                if not self.custom_cache:
                    canonical = value["result"]["executable"]
                    self.cache = Cache(self.host + "\0" + canonical + "\0" + str(self.options))
                self.connected = True
                self.events.put(dict(event="connected", **value["result"], rtt=self.rtt))
                return
            if value.get("event") == "eof":
                raise ConnectionError(self.diagnostics)
        raise TimeoutError(T("SSH 登录或服务器握手超时"))

    def _lost(self, error):
        self.connected = False
        self.events.put(dict(event="disconnected", text=str(error)))
        uncertain = set()
        for ident, item in list(self.pending.items()):
            if item["method"] in protocol.READ_ONLY:
                self.outbound.put(item)
            else:
                if not item["future"].done():
                    item["future"].set_exception(UnknownOutcome(
                        T("启动或停止的结果无法确认；重连后请先核对状态，不要直接重复操作")))
                uncertain.add(item["args"].get("directory"))
        self.pending.clear()
        self._stop_process()
        for directory in uncertain:
            if directory:
                self.request("run_status", dict(directory=directory))
        if self.watched:
            self.request("watch", dict(directory=self.watched))

    def _stop_process(self):
        process, self.process = self.process, None
        if process:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()

    def _run(self):
        failures = 0
        try:
            while not self.closed.is_set():
                try:
                    if not self.connected:
                        self._connect()
                    while True:
                        try:
                            item = self.outbound.get_nowait()
                        except queue.Empty:
                            break
                        if item["future"].cancelled():
                            continue
                        if item["method"] == "watch":
                            self.watched = item["args"]["directory"]
                        if item.get("cached") and "cache_key" not in item:
                            item["cache_key"] = json.dumps([item["method"], item["args"]], sort_keys=True)
                            item["cache_value"] = self.cache.get(item["cache_key"])
                            if item["cache_value"]:
                                item["args"]["known_signature"] = item["cache_value"]["signature"]
                                self.events.put(dict(event="progress", id=item["id"], cached=True,
                                                     data=item["cache_value"]["data"]))
                        self._send(item)
                    try:
                        generation, value = self.incoming.get(timeout=0.05)
                    except queue.Empty:
                        now = time.monotonic()
                        for item in self.pending.values():
                            base = 180 if item["method"] in (
                                "view", "levels", "composition", "wavefunctions", "summary", "transfer_files") else 30
                            if now - item["sent"] > base + 20 * self.rtt:
                                raise TimeoutError(T("远程请求超时，正在重新连接"))
                        continue
                    if generation != self.generation:
                        continue
                    if value.get("event") in ("eof", "protocol_error"):
                        raise ConnectionError(self.diagnostics or T("SSH 连接已断开"))
                    if "event" in value:
                        self.events.put(value)
                        continue
                    item = self.pending.pop(value.get("id"), None)
                    if item is None or item["future"].done():
                        continue
                    self.rtt = 0.8 * self.rtt + 0.2 * (time.monotonic() - item["sent"])
                    if "error" in value:
                        item["future"].set_exception(ValueError(value["error"]))
                        continue
                    result = value["result"]
                    if item.get("cached"):
                        if result.get("unchanged"):
                            result = item["cache_value"]
                        else:
                            try:
                                self.cache.put(item["cache_key"], result)
                            except OSError as e:
                                self.events.put(dict(event="notice", text=str(e)))
                    failures = 0
                    item["future"].set_result(result)
                    if item["method"] in ("run_status", "watch"):
                        self.events.put(dict(event="state", directory=item["args"]["directory"],
                                             state=result.get("state")))
                    self.events.put(dict(event="latency", rtt=self.rtt))
                except (OSError, ValueError, EOFError, TimeoutError) as e:
                    self._lost(e)
                    failures += 1
                    if not self.fallback and any(x in str(e) for x in (
                            "mux_client", "ControlSocket", "master session", "Session open refused")):
                        self.fallback = True
                    if failures >= 3 or "Permission denied" in str(e):
                        raise
                    if self.closed.wait(min(2 ** failures, 5)):
                        break
        except Exception as e:
            self.events.put(dict(event="error", text=str(e)))
            for item in self.pending.values():
                if not item["future"].done():
                    item["future"].set_exception(e)
            while not self.outbound.empty():
                item = self.outbound.get_nowait()
                if not item["future"].done():
                    item["future"].set_exception(e)
        finally:
            self._stop_process()
            self.closed.set()
            remaining = list(self.pending.values())
            while not self.outbound.empty():
                remaining.append(self.outbound.get_nowait())
            for item in remaining:
                if not item["future"].done():
                    item["future"].set_exception(ConnectionError(T("SSH 连接已关闭")))

    def close(self, wait=True):
        self.closed.set()
        process = self.process
        if process and process.poll() is None:
            process.terminate()
        if wait:
            self.thread.join(timeout=5)
