"""服务器流程副本的本机关联、配置比对及输入默认选择；不修改会话格式。"""

import hashlib
import json
from pathlib import Path

import grasp_config as config
import grasp_core as core
import grasp_session as sessions
import grasp_summary as summary
import grasp_workdir as work
from grasp_core import T

CAPABILITY = "workdir_write_v1"


def association(directory):
    return core.load_config().get("remote_copies", {}).get(str(Path(directory).resolve()))


def bind(directory, value):
    copies = core.load_config().get("remote_copies", {})
    key = str(Path(directory).resolve())
    if value is None:
        copies.pop(key, None)
    else:
        copies[key] = dict(value)
    core.save_config(remote_copies=copies)


def fingerprint(configuration):
    cfg, _ = config.check(configuration)
    jobs = [config.export_job(job) for job in config.session(cfg)["pipeline"]]
    files = sorted(cfg.get("files", []), key=lambda row: row["name"])
    value = json.dumps(dict(jobs=jobs, files=files), sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(value.encode()).hexdigest()


def configuration(directory, pipeline):
    root = Path(directory)
    paths = []
    for name in sorted(config.load_inputs(str(root)) or []):
        path = root / name
        # 本机副本可以缺少服务器输入；缺失由上传预检和最终运行检查处理。
        if path.is_file() and not path.is_symlink():
            paths.append(str(path))
    return config.export(pipeline, dict(title=root.name), files=paths)


def checkout(directory, host, remote, reply, replace=False):
    root = Path(directory).expanduser().resolve()
    previous = association(root)
    same = previous and previous["host"] == host and previous["directory"] == remote
    with sessions.locked(root):
        if root.exists() and any(root.iterdir()):
            if not same or not replace:
                raise ValueError(T("本机目录已有其他内容，请换一个目录。"))
            work.apply_update(work.prepare_update(root, reply["config"]))
        else:
            config.install(reply["config"], str(root))
        bind(root, dict(host=host, directory=remote, revision=reply["revision"]))
    return str(root)


def generated_files(jobs, run=None):
    names = set()
    for job in jobs:
        step, values = summary.page(job)
        if step is None:
            continue
        names.update(step.outputs(values, run or {}) or [])
        for action in [*step.pre(values), *step.post(values)]:
            if action[0] == "cp":
                names.add(action[2])
    return names


def input_rows(rows):
    # 目录记录、运行身份和 rsync 续传文件不能作为计算输入上传。
    return [row for row in rows if not any(part.startswith(".grasp") for part in Path(row["name"]).parts)]
