"""服务器工作区的本机草稿：~/.config/grasp_gui/drafts/<digest>.json。

草稿不是工作目录记录，也不是共享配置。格式 (version 1)：
  host              连接标识 (归一化的 user@host:port)
  directory         服务器返回的绝对路径
  base_revision     开始编辑时服务器流程的修订号
  base_fingerprint  开始编辑时服务器配置的指纹；失去回答后据此判断服务器上是否仍是原来的流程
  configuration     共享配置对象 (编辑后的流程和附带文件)
  fingerprint       configuration 的指纹 (与“在本机编辑”使用同一算法)
  updated           最后保存草稿的时间
  pending           可选；正在写入或结果未确认时：{"revision": 写入所用的修订号, "fingerprint": 写入的配置指纹}
只用标准库。"""

import datetime
import hashlib
import json
import os
from pathlib import Path
import tempfile

import grasp_config as config
import grasp_core as core
import grasp_remote_edit as edit
from grasp_core import T

VERSION = 1
FIELDS = {"version": int, "host": str, "directory": str, "base_revision": str, "base_fingerprint": str,
          "configuration": dict, "fingerprint": str, "updated": str}


class DraftError(ValueError):
    """草稿版本不认识或格式有误；原文件保留，不覆盖也不删除。"""


def directory():
    # 与 CONFIG_FILE 同在 ~/.config 下 (macOS 也一样)
    return Path(core.CONFIG_FILE).parent / "grasp_gui" / "drafts"


def digest(key):
    """key 为工作区标识 (连接标识, 服务器绝对路径)；按规范 JSON 编码计算。"""
    raw = json.dumps(list(key), ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def path(key):
    return directory() / (digest(key) + ".json")


def fingerprint(cfg):
    """空流程不是合法配置，但可以是草稿；按同一算法给出指纹。"""
    if cfg.get("jobs"):
        return edit.fingerprint(cfg)
    files = sorted(cfg.get("files", []), key=lambda row: row["name"])
    value = json.dumps(dict(jobs=[], files=files), sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(value.encode()).hexdigest()


def configuration(pipeline, base):
    """编辑后的流程 -> 配置；附带文件和说明沿用服务器配置。"""
    cfg = {key: value for key, value in base.items() if key != "jobs"}
    cfg["jobs"] = [config.export_job(job) for job in pipeline]
    return cfg


def check(data, key):
    if not isinstance(data, dict) or data.get("version") != VERSION:
        raise DraftError(T("草稿版本无法识别：{p}", p=path(key)))
    for name, kind in FIELDS.items():
        if type(data.get(name)) is not kind:
            raise DraftError(T("草稿格式有误：{p}", p=path(key)))
    pending = data.get("pending")
    if pending is not None and not (isinstance(pending, dict) and isinstance(pending.get("revision"), str)
                                    and isinstance(pending.get("fingerprint"), str)):
        raise DraftError(T("草稿格式有误：{p}", p=path(key)))
    if [data["host"], data["directory"]] != list(key):
        raise DraftError(T("草稿格式有误：{p}", p=path(key)))
    try:
        same = fingerprint(data["configuration"]) == data["fingerprint"]
    except (KeyError, TypeError, ValueError) as error:
        raise DraftError(T("草稿格式有误：{p}", p=path(key))) from error
    if not same:
        raise DraftError(T("草稿格式有误：{p}", p=path(key)))
    return data


def load(key):
    """没有草稿时返回 None；格式有误时抛出 DraftError。"""
    try:
        raw = path(key).read_bytes()
    except FileNotFoundError:
        return None
    try:
        data = json.loads(raw)
    except (ValueError, RecursionError) as error:
        raise DraftError(T("草稿格式有误：{p}", p=path(key))) from error
    return check(data, key)


def save(key, base_revision, base_fingerprint, cfg, pending=None):
    data = dict(version=VERSION, host=key[0], directory=key[1], base_revision=base_revision,
                base_fingerprint=base_fingerprint, configuration=cfg, fingerprint=fingerprint(cfg),
                updated=datetime.datetime.now().isoformat(sep=" ", timespec="seconds"))
    if pending is not None:
        data["pending"] = pending
    folder = directory()
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(folder, 0o700)
    target = path(key)
    temporary = None
    try:
        # 内容包括服务器路径和流程：只有本人可读；原子替换，崩溃时不留半个文件。
        with tempfile.NamedTemporaryFile(dir=folder, prefix=".draft-", delete=False) as f:
            temporary = f.name
            os.fchmod(f.fileno(), 0o600)
            f.write(json.dumps(data, ensure_ascii=False, indent=1).encode("utf-8"))
        os.replace(temporary, target)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)
    return data


def remove(key):
    try:
        path(key).unlink()
    except FileNotFoundError:
        pass
