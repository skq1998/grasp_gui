"""
后台运行：计算 (流程或单个程序) 总是由一个独立的运行进程执行，界面只是查看者。
关闭窗口、ssh 断开或界面崩溃时计算继续；再次打开该目录时界面自动接上。只用标准库。

    运行进程    grasp_gui _run 目录  (源码中为 python3 grasp_bg.py 目录)；新会话，不受 SIGHUP 影响
    请求        RUN_REQUEST：界面写入的任务和运行设置，运行进程读取后删除
    状态        RUN_STATE：pid、主机、开始 / 结束时间、各步标题、当前步、结果 (running / ok / failed / stopped)
    输出        RUN_LOG：全部输出 (纯文本)；界面连不上运行进程时 (如在另一台登录节点上打开目录) 读这个文件
    通道        Linux 抽象命名空间的 Unix 套接字 (不在目录中留下文件)，名称由用户和目录决定，同时用作锁：
                一个目录同一时间只有一个运行进程。

通道上的消息每行一个 JSON：运行进程 -> 界面 {"s": 文本, "tag": 标记}、{"step": i}、{"done": 结果}；
界面 -> 运行进程 "stop"。界面连上时先收到最近的输出 (HISTORY 字节以内)，然后是实时输出，不丢也不重复。
"""

import hashlib
import json
import os
import pwd
import re
import socket
import subprocess
import sys
import threading
import time

import grasp_core as core
from grasp_core import T

RUN_REQUEST = ".grasp_gui_run_request.json"
RUN_STATE = ".grasp_gui_run.json"
RUN_LOG = ".grasp_gui_run.log"
HISTORY = 1 << 20              # 界面连上时补发的最近输出 (字节)
ATTACH_WAIT = 10               # 运行进程开始计算前等待启动它的界面连上的秒数 (与界面的 connect 超时相同)
RESULTS = ("ok", "failed", "stopped")


def address(workdir):
    """运行进程的通道名 (抽象命名空间)：按用户和目录区分。"""
    h = hashlib.sha1(os.path.realpath(workdir).encode()).hexdigest()[:20]
    return f"\0grasp_gui-{os.getuid()}-{h}"


def read_state(workdir):
    try:
        with open(os.path.join(workdir, RUN_STATE), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def write_state(workdir, state):
    path = os.path.join(workdir, RUN_STATE)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def mark_seen(workdir):
    """界面已显示过这次运行的结果：以后打开目录时不再显示。"""
    st = read_state(workdir)
    if st and st.get("status") in RESULTS + ("running",) and not st.get("seen"):
        st["seen"] = True
        try:
            write_state(workdir, st)
        except OSError:
            pass


def log_tail(workdir, lines=core.MAX_OUT_LINES):
    """RUN_LOG 的最后 lines 行。"""
    try:
        with open(os.path.join(workdir, RUN_LOG), "rb") as f:
            f.seek(max(0, os.fstat(f.fileno()).st_size - HISTORY))
            text = f.read().decode("utf-8", "replace")
    except OSError:
        return ""
    return "\n".join(text.split("\n")[-lines:])


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def status(workdir):
    """该目录最近一次运行的状态 (没有时为 None)。running 但进程已不在 (本机) 时改为 "lost"；
    在另一台机器上运行时 remote 为真 (无法确认是否还在运行)。"""
    st = read_state(workdir)
    if not st:
        return None
    st["remote"] = st.get("host") != socket.gethostname()
    if st.get("status") == "running" and not st["remote"] and not alive(st.get("pid", -1)):
        st["status"] = "lost"
    return st


def connect(workdir, timeout=0.0):
    """连接该目录的运行进程；没有时返回 None。timeout：运行进程刚启动时等它开始监听的秒数。"""
    end = time.monotonic() + timeout
    while True:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            s.connect(address(workdir))
            return s
        except OSError:
            s.close()
            if time.monotonic() >= end:
                return None
            time.sleep(0.05)


def logout_kills():
    """登录会话结束 (ssh 断开、退出登录) 时，systemd-logind 是否会结束本会话中的全部进程，包括后台计算：
    KillUserProcesses=yes、当前用户不在 KillExcludeUsers 中、且本进程在登录会话的 scope (session-*.scope) 中。
    无法确定时为 False。说明见 docs/INTERFACE.md。"""
    try:
        with open("/proc/self/cgroup", encoding="utf-8") as f:
            if not re.search(r"/session-[^/]*\.scope", f.read()):
                return False
        def prop(name):
            return subprocess.run(["busctl", "get-property", "org.freedesktop.login1", "/org/freedesktop/login1",
                                   "org.freedesktop.login1.Manager", name], capture_output=True, text=True,
                                  timeout=3, env=core.child_env()).stdout
        if prop("KillUserProcesses").split()[:2] != ["b", "true"]:
            return False
        return pwd.getpwuid(os.getuid()).pw_name not in re.findall(r'"([^"]*)"', prop("KillExcludeUsers"))
    except (OSError, KeyError, subprocess.SubprocessError):
        return False


def command():
    """启动运行进程的命令 (不含目录参数)。"""
    if getattr(sys, "frozen", False):
        return [sys.executable, "_run"]
    return [sys.executable, os.path.abspath(__file__)]


def launch(workdir, jobs, cfg):
    """在后台启动运行进程。返回 Popen (界面用它回收进程)；目录中已有运行进程时抛出 RuntimeError。"""
    probe = connect(workdir)
    if probe:
        probe.close()
        raise RuntimeError(T("此目录已有计算在运行"))
    req = os.path.join(workdir, RUN_REQUEST)
    with open(req, "w", encoding="utf-8") as f:
        json.dump(dict(jobs=jobs, cfg=cfg, lang=core.LANG), f, ensure_ascii=False)
    # 新会话：关闭终端、ssh 断开时不会收到 SIGHUP；输出全部经由通道和 RUN_LOG
    return subprocess.Popen(command() + [workdir], cwd=workdir, env=core.child_env(), stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


# ---------------------------------------------------------------------------
#  运行进程
# ---------------------------------------------------------------------------

class Server:
    def __init__(self, workdir, listener, jobs, cfg):
        self.workdir, self.listener, self.jobs, self.cfg = workdir, listener, jobs, cfg
        self.lock = threading.Lock()
        self.attached = threading.Event()
        self.clients = []
        self.history = []          # [(文本, 标记)]，总长度不超过 HISTORY
        self.size = 0
        self.log = open(os.path.join(workdir, RUN_LOG), "w", encoding="utf-8", errors="replace")
        self.runner = core.Runner(self.emit)
        self.state = dict(pid=os.getpid(), host=socket.gethostname(), start=time.strftime("%Y-%m-%d %H:%M:%S"),
                          titles=[j.get("title", j.get("prog", "")) for j in jobs], step=0, status="running")
        write_state(workdir, self.state)

    def send(self, msg):
        """发给所有已连接的界面 (调用者持有 self.lock)；发送失败的界面断开。"""
        data = (json.dumps(msg, ensure_ascii=False) + "\n").encode()
        for c in list(self.clients):
            try:
                c.sendall(data)
            except OSError:
                self.drop(c)

    def drop(self, c):
        if c in self.clients:
            self.clients.remove(c)
        try:
            c.close()
        except OSError:
            pass

    def emit(self, text, tag):
        if not text:
            return
        with self.lock:
            self.log.write(text)
            self.log.flush()
            self.history.append((text, tag))
            self.size += len(text)
            while self.size > HISTORY and len(self.history) > 1:
                self.size -= len(self.history.pop(0)[0])
            self.send(dict(s=text, tag=tag))

    def started(self, i, job):
        with self.lock:
            self.state["step"] = i
            self.state["step_start"] = time.time()          # 界面据此显示本步已运行的时间
            write_state(self.workdir, self.state)
            self.send(dict(step=i))

    def accept_loop(self):
        while True:
            try:
                c, _ = self.listener.accept()
            except OSError:
                return
            c.settimeout(10)               # 界面卡住时不拖住计算
            with self.lock:
                try:
                    for text, tag in self.history:
                        c.sendall((json.dumps(dict(s=text, tag=tag), ensure_ascii=False) + "\n").encode())
                    c.sendall((json.dumps(dict(step=self.state["step"])) + "\n").encode())
                except OSError:
                    c.close()
                    continue
                self.clients.append(c)
            self.attached.set()
            threading.Thread(target=self.read_loop, args=(c,), daemon=True).start()

    def read_loop(self, c):
        """界面发来的命令：stop。"""
        buf = b""
        while True:
            try:
                data = c.recv(4096)
            except socket.timeout:
                continue
            except OSError:
                data = b""
            if not data:
                with self.lock:
                    self.drop(c)
                return
            buf += data
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if line.strip() == b"stop":
                    self.stop()

    def stop(self):
        """停止：结束正在运行的程序，不再开始后面的步骤；还在等界面连上时不再等待。"""
        self.runner.stop()
        self.attached.set()

    def run(self):
        threading.Thread(target=self.accept_loop, daemon=True).start()
        # 等启动它的界面连上再开始：几毫秒就结束的计算 (如单独运行 rnucleus) 否则在界面连上之前已经退出，
        # 界面会报告无法启动并且看不到输出。界面没有连上 (如刚启动就被关闭) 时等待后照常计算。
        self.attached.wait(ATTACH_WAIT)
        result = []
        self.runner.run_all(self.jobs, self.cfg, result.append, started=self.started)
        ok = result[0] if result else False
        with self.lock:
            self.state.update(status="ok" if ok else ("stopped" if self.runner.stopped else "failed"),
                              end=time.strftime("%Y-%m-%d %H:%M:%S"))
            write_state(self.workdir, self.state)
            self.send(dict(done=self.state["status"]))
            for c in list(self.clients):
                self.drop(c)
            self.log.close()
        self.listener.close()


def serve(workdir):
    """运行进程的主体：读取请求，执行，结束后退出。返回退出码。"""
    workdir = os.path.abspath(workdir)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        listener.bind(address(workdir))        # 同一目录已有运行进程时失败
    except OSError:
        return 1
    listener.listen(8)
    try:
        with open(os.path.join(workdir, RUN_REQUEST), encoding="utf-8") as f:
            req = json.load(f)
        os.remove(os.path.join(workdir, RUN_REQUEST))
    except (OSError, ValueError):
        return 1
    import signal
    core.set_lang(req.get("lang", "zh"))
    server = Server(workdir, listener, req["jobs"], req["cfg"])
    # SIGTERM / SIGHUP (例如用户用 kill 结束运行进程)：按“停止”处理，结束正在运行的程序
    signal.signal(signal.SIGTERM, lambda *_: server.stop())
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    server.run()
    return 0


if __name__ == "__main__":
    sys.exit(serve(sys.argv[1]) if len(sys.argv) == 2 else 2)
