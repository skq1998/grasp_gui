"""工作目录记录的原子写入与修订检查；GUI、MCP 和本地配置工具共用。"""

from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import threading

import grasp_core as core
from grasp_core import T

ANY_REVISION = object()
_held = threading.local()


class Conflict(ValueError):
    """调用方读取之后，记录已被其他写者修改。"""


def _bytes(workdir):
    path = Path(workdir) / core.SESSION_FILE
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, "rb") as f:
        if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
            raise ValueError(T("工作目录记录的格式有误"))
        return f.read()


def revision(raw):
    return hashlib.sha256(raw).hexdigest() if raw is not None else None


def read(workdir):
    raw = _bytes(workdir)
    if raw is None:
        return None, None
    try:
        data = json.loads(raw)
    except (ValueError, RecursionError) as e:
        raise ValueError(T("工作目录记录的格式有误")) from e
    if not isinstance(data, dict) or not isinstance(data.get("pipeline", []), list):
        raise ValueError(T("工作目录记录的格式有误"))
    return data, revision(raw)


@contextmanager
def locked(workdir):
    """锁放在用户配置目录，清空计算目录不会删除正在使用的锁。"""
    key = os.path.realpath(workdir)
    held = getattr(_held, "paths", set())
    if key in held:
        yield
        return
    directory = Path(core.CONFIG_FILE).parent / "grasp_gui" / "session-locks"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    name = hashlib.sha256(os.fsencode(key)).hexdigest() + ".lock"
    fd = os.open(directory / name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        _held.paths = held | {key}
        try:
            yield
        finally:
            _held.paths = held
    finally:
        os.close(fd)


def atomic(path, raw):
    path = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".grasp-record-", delete=False) as f:
            temporary = f.name
            f.write(raw)
            if path.is_file():
                os.fchmod(f.fileno(), stat.S_IMODE(path.stat().st_mode))
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def write(workdir, data, expected=ANY_REVISION):
    raw = json.dumps(data, ensure_ascii=False, indent=1).encode("utf-8")
    with locked(workdir):
        previous = _bytes(workdir)
        if expected is not ANY_REVISION and revision(previous) != expected:
            raise Conflict(T("工作目录记录已被其他程序修改，请重新载入"))
        atomic(Path(workdir) / core.SESSION_FILE, raw)
    return revision(raw)
