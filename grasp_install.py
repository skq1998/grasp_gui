"""桌面捆绑包的校验和显式 SSH 安装；不依赖服务器 Python、scp 或 Qt。"""

import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import subprocess
import sys
import tarfile
import threading
import time

import grasp_core as core
from grasp_core import T

ARCHIVE = "grasp_gui-server-linux-x86_64.tar.gz"
DEFAULT_ROOT = "~/.local/lib/grasp_gui"


def bundle(directory=None):
    root = Path(directory) if directory else Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / "server"
    archive = root if root.is_file() else root / ARCHIVE
    if not archive.is_file():
        raise ValueError(T("本构建不含服务器版"))
    h = hashlib.sha256()
    with archive.open("rb") as stream:
        for data in iter(lambda: stream.read(1 << 20), b""):
            h.update(data)
    digest = h.hexdigest()
    if Path(str(archive) + ".sha256").read_text().split()[0] != digest:
        raise ValueError(T("服务器压缩包校验失败"))
    size, version = 0, None
    with tarfile.open(archive) as stream:
        for item in stream:
            path = PurePosixPath(item.name)
            if (not path.parts or path.parts[0] != "grasp_gui-server" or ".." in path.parts
                    or not (item.isfile() or item.isdir())):
                raise ValueError(T("服务器压缩包包含不安全的路径或文件类型"))
            size += item.size
            if item.name == "grasp_gui-server/SERVER_VERSION":
                if item.size > 1024:
                    raise ValueError(T("服务器版本记录无效"))
                version = stream.extractfile(item).read().decode("utf-8").strip()
    if version != "GRASP GUI " + core.VERSION + " (server; remote protocol 1)":
        raise ValueError(T("捆绑服务器版的版本或协议与桌面版不一致"))
    return dict(path=str(archive), sha256=digest, size=archive.stat().st_size, expanded=size, version=core.VERSION)


def older_version(value, current):
    """只接受完整的数字发布版本；未知格式不触发安装提示。"""
    if not all(isinstance(v, str) and re.fullmatch(r"\d+\.\d+\.\d+", v) for v in (value, current)):
        return False
    return tuple(map(int, value.split("."))) < tuple(map(int, current.split(".")))


# 参数经 shlex.quote 传入位置参数；服务器不解析用户输入为 shell 代码。
COMMON = r'''
set -eu
root=$1
case "$root" in '~/'*) root="$HOME/${root#\~/}" ;; /*) ;; *) echo 'absolute install path required' >&2; exit 2 ;; esac
root=$(readlink -m -- "$root")
link="$HOME/.local/bin/grasp_gui"
owned() {
    [ -d "$1" ] && [ ! -L "$1" ] && [ -f "$1/.grasp-gui-version" ] &&
        [ "$(head -n 1 "$1/.grasp-gui-version")" = "$(basename "$1")" ]
}
active() {
    for proc in /proc/[0-9]*/exe; do
        running=$(readlink "$proc" 2>/dev/null || :)
        case "$running" in "$1/"*) return 0 ;; esac
    done
    return 1
}
'''
PROBE = COMMON + r'''
arch=$(uname -m)
glibc=$(getconf GNU_LIBC_VERSION 2>/dev/null || :)
[ "$arch" = x86_64 ] || { echo "unsupported architecture: $arch" >&2; exit 2; }
set -- $glibc
[ "${1:-}" = glibc ] || { echo 'glibc required' >&2; exit 2; }
major=${2%%.*}; minor=${2#*.}; minor=${minor%%.*}
[ "$major" -gt 2 ] || { [ "$major" -eq 2 ] && [ "$minor" -ge 17 ]; } || {
    echo "glibc 2.17 required: $glibc" >&2; exit 2;
}
parent=$root
while [ ! -e "$parent" ]; do parent=$(dirname "$parent"); done
[ -d "$parent" ] && [ -w "$parent" ] || { echo 'installation parent is not writable' >&2; exit 2; }
probe=$(mktemp -d "$parent/.probe.XXXXXX")
trap 'rm -rf "$probe"' EXIT HUP INT TERM
printf '#!/bin/sh\nexit 0\n' > "$probe/check"
chmod 700 "$probe/check"
"$probe/check" || { echo 'installation filesystem is noexec' >&2; exit 2; }
space=$(df -Pk "$parent" | awk 'END {print $4}')
printf '%s\0' "$arch" "$glibc" "$space" "$root" "$link"
for item in "$root"/*; do owned "$item" && printf '%s\0' "$item" || :; done
'''
INSTALL = COMMON + r'''
version=$2; expected=$3
mkdir -p "$root"
stage=$(mktemp -d "$root/.install.XXXXXX")
linkstage=''
cleanup() { rm -rf "$stage"; [ -z "$linkstage" ] || rm -rf "$linkstage"; }
trap cleanup EXIT
trap 'exit 130' HUP INT TERM
cat > "$stage/archive"
actual=$(sha256sum "$stage/archive" | cut -d ' ' -f 1)
[ "$actual" = "$expected" ] || { echo 'sha256 mismatch' >&2; exit 2; }
mkdir "$stage/tree"
tar xzf "$stage/archive" --strip-components=1 -C "$stage/tree"
info=$("$stage/tree/grasp_gui-server" --version)
case "$info" in "GRASP GUI $version (server; remote protocol 1)") ;; *) echo "$info" >&2; exit 2 ;; esac
"$stage/tree/grasp_gui-server" remote </dev/null | grep -qx 'GRASP_GUI_REMOTE 1'
dest="$root/$version"
previous=''
if [ -e "$link" ] || [ -L "$link" ]; then
    [ -L "$link" ] || { echo 'refusing to replace an unmanaged executable' >&2; exit 2; }
    target=$(readlink "$link")
    previous=$(dirname "$target")
    case "$previous" in "$root/"*) owned "$previous" ;; *) false ;; esac || {
        echo 'refusing to replace an unmanaged link' >&2; exit 2;
    }
fi
if [ -e "$dest" ] || [ -L "$dest" ]; then
    owned "$dest" && [ "$(tail -n 1 "$dest/.grasp-gui-version")" = "$expected" ] || {
        echo 'version directory already exists with different contents' >&2; exit 2;
    }
else
    printf '%s\n%s\n' "$version" "$expected" > "$stage/tree/.grasp-gui-version"
    mv -T "$stage/tree" "$dest"
fi
if [ "$previous" = "$dest" ] && [ -f "$root/.grasp-gui-previous" ]; then
    previous=$(cat "$root/.grasp-gui-previous")
fi
printf '%s\n' "$previous" > "$stage/previous"
mv -Tf "$stage/previous" "$root/.grasp-gui-previous"
mkdir -p "$(dirname "$link")"
linkstage=$(mktemp -d "$(dirname "$link")/.grasp-link.XXXXXX")
ln -s "$dest/grasp_gui-server" "$linkstage/current"
mv -Tf "$linkstage/current" "$link"
for item in "$root"/*; do
    if owned "$item" && [ "$item" != "$dest" ] && [ "$item" != "$previous" ] && ! active "$item"; then
        rm -rf "$item"
    fi
done
printf '%s\n' "$link"
'''
REMOVE = COMMON + r'''
[ -d "$root" ] || exit 0
# 仅移除带本程序所有权标记的版本，运行中的版本保留，不能破坏已加载的服务。
for item in "$root"/*; do
    if owned "$item" && active "$item"; then echo "still running: $item" >&2; exit 2; fi
done
if [ -L "$link" ]; then
    target=$(readlink "$link")
    base=$(dirname "$target")
    case "$base" in "$root/"*) if owned "$base"; then rm "$link"; fi ;; esac
fi
for item in "$root"/*; do
    if owned "$item"; then
        if active "$item"; then echo "still running: $item" >&2; exit 2; fi
        rm -rf "$item"
    fi
done
rm -f "$root/.grasp-gui-previous"
'''


def command(script, root=DEFAULT_ROOT, *args):
    if not isinstance(root, str) or not root.startswith(("/", "~/")) or any(c in root for c in "\n\r\x00"):
        raise ValueError(T("安装目录必须是完整路径或 ~/ 路径"))
    return "sh -c " + shlex.quote(script) + " sh " + shlex.join([root, *args])


def probe_result(raw, required):
    values = raw.decode("utf-8").rstrip("\0").split("\0")
    if len(values) < 5 or values[0] != "x86_64" or not re.fullmatch(r"glibc \d+\.\d+", values[1]):
        raise ValueError(T("无法识别服务器安装检查结果"))
    if tuple(map(int, values[1].split()[1].split("."))) < (2, 17):
        raise ValueError(T("服务器需要 glibc 2.17 或更新版本"))
    if not values[2].isdigit() or int(values[2]) * 1024 < required:
        raise ValueError(T("服务器安装目录空间不足"))
    return dict(architecture=values[0], glibc=values[1], free=int(values[2]) * 1024,
                root=values[3], link=values[4], versions=values[5:])


class Installer:
    def __init__(self, client):
        self.client = client
        self.cancelled = threading.Event()
        self.process = None

    def cancel(self):
        self.cancelled.set()
        process = self.process
        if process is not None and process.poll() is None:
            process.terminate()

    def run(self, script, root, *args, archive=None, progress=None):
        if self.cancelled.is_set():
            raise InterruptedError(T("安装已取消"))
        # 调用者先停止分析会话；ControlMaster 仍可复用，因此 MaxSessions 1 也能安装。
        argv = self.client.argv()[:-1] + [command(script, root, *args)]
        process = self.process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                                  stderr=subprocess.PIPE, env=self.client.environment())
        output, errors = bytearray(), bytearray()
        def read(stream, target):
            while True:
                data = stream.read(4096)
                if not data:
                    break
                target.extend(data)
                if len(target) > 65536:
                    del target[:-65536]
        readers = [threading.Thread(target=read, args=(process.stdout, output), daemon=True),
                   threading.Thread(target=read, args=(process.stderr, errors), daemon=True)]
        for thread in readers:
            thread.start()
        try:
            if archive:
                size, sent = os.path.getsize(archive), 0
                with open(archive, "rb") as stream:
                    for data in iter(lambda: stream.read(65536), b""):
                        if self.cancelled.is_set():
                            raise InterruptedError(T("安装已取消"))
                        process.stdin.write(data)
                        sent += len(data)
                        if progress:
                            progress(sent, size)
            process.stdin.close()
            deadline = time.monotonic() + 120
            while process.poll() is None:
                if self.cancelled.wait(.1):
                    raise InterruptedError(T("安装已取消"))
                if time.monotonic() > deadline:
                    raise TimeoutError(T("服务器安装请求超时"))
            for thread in readers:
                thread.join(2)
            if process.returncode:
                raise RuntimeError(errors.decode("utf-8", "replace") or T("服务器安装失败"))
            return bytes(output)
        except OSError as error:
            if self.cancelled.is_set():
                raise InterruptedError(T("安装已取消")) from error
            raise
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            for stream in (process.stdin, process.stdout, process.stderr):
                try:
                    stream.close()
                except OSError:
                    pass  # 子进程已结束；关闭管道不能覆盖取消或实际失败的诊断。
            self.process = None

    def probe(self, package=None, root=DEFAULT_ROOT):
        required = package["size"] + package["expanded"] * 2 if package else 0
        return probe_result(self.run(PROBE, root), required)

    def install(self, package, root=DEFAULT_ROOT, progress=None):
        if not re.fullmatch(r"\d+\.\d+\.\d+", package["version"]):
            raise ValueError("invalid server version")
        if not re.fullmatch(r"[a-f0-9]{64}", package["sha256"]):
            raise ValueError("invalid server checksum")
        self.probe(package, root)
        return self.run(INSTALL, root, package["version"], package["sha256"],
                        archive=package["path"], progress=progress).decode().strip()

    def remove(self, root=DEFAULT_ROOT):
        return self.run(REMOVE, root)
