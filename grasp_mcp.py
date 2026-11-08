"""默认关闭的 MCP stdio 服务；命令行和协议均不需要 Qt。"""

import argparse
import base64
import json
import os
from pathlib import Path
import shlex
import sys

import grasp_config as config
import grasp_core as core
import grasp_service as service
from grasp_core import T

PROTOCOLS = ("2025-11-25", "2025-06-18", "2025-03-26")
MAX_FRAME = config.MAX_CONFIG + 65536
MAX_RESPONSE = 8 << 20


def schema(kind, **kw):
    return dict(type=kind, **kw)


DIRECTORY = schema("string", description="Directory relative to --root, or an absolute path inside it")
CONFIG = schema("object", description="Full GRASP GUI shared configuration")
LIMIT = schema("integer", minimum=1, maximum=service.MAX_ROWS, default=200)
FILES = schema("array", items=schema("string"), maxItems=100)
TOOLS = {
    "list_steps": ("List program pages", {}, []),
    "describe_step": ("Describe fields and defaults of a program page", {"step": schema("string")}, ["step"]),
    "list_examples": ("List bundled manual examples", {}, []),
    "validate_config": ("Check a shared configuration", {"config": CONFIG}, ["config"]),
    "create_workdir": ("Import config or example into a new empty directory",
                       {"directory": DIRECTORY, "config": CONFIG, "example": schema("string")}, ["directory"]),
    "get_pipeline": ("Read a configuration and revision for an existing directory", {"directory": DIRECTORY},
                     ["directory"]),
    "update_workdir": ("Update parameters/pipeline with an expected revision; retain results; do not run",
                       {"directory": DIRECTORY, "revision": schema("string"), "config": CONFIG},
                       ["directory", "revision", "config"]),
    "export_script": ("Return a shell or Slurm script", {"directory": DIRECTORY, "slurm": schema("object")},
                      ["directory"]),
    "run": ("Start the pipeline, optionally from a one-based step number",
            {"directory": DIRECTORY, "start": schema("integer", minimum=1, default=1),
             "force": schema("boolean", default=False)}, ["directory"]),
    "status": ("Read the latest background run state", {"directory": DIRECTORY}, ["directory"]),
    "tail_log": ("Read a bounded log tail", {"directory": DIRECTORY,
                                            "lines": schema("integer", minimum=1, maximum=1000, default=100)},
                 ["directory"]),
    "stop": ("Request stopping the current run", {"directory": DIRECTORY}, ["directory"]),
    "list_results": ("Identify local result files by content", {"directory": DIRECTORY}, ["directory"]),
    "levels": ("Read bounded level energies, purity, leading jj/LSJ components",
               {"directory": DIRECTORY, "files": FILES, "limit": LIMIT,
                "components": schema("integer", minimum=0, maximum=20, default=3)}, ["directory"]),
    "transitions": ("Read computed transitions and branching ratios among known decays",
                    {"directory": DIRECTORY, "files": FILES, "limit": LIMIT}, ["directory"]),
    "orbitals": ("Read orbital properties", {"directory": DIRECTORY, "files": FILES, "limit": LIMIT},
                 ["directory"]),
    "plot_levels": ("Render a level diagram with the existing Qt drawing code",
                    {"directory": DIRECTORY, "files": FILES, "limit": LIMIT, "output": schema("string")},
                    ["directory"]),
    "plot_wavefunctions": ("Render wavefunctions with the existing Qt drawing code",
                           {"directory": DIRECTORY, "files": FILES, "output": schema("string")}, ["directory"]),
}


def client_config(client, root):
    if getattr(sys, "frozen", False):
        command = [os.path.abspath(sys.executable)]
    else:
        command = [os.path.abspath(sys.executable), str(Path(__file__).with_name("grasp_gui.py").resolve())]
    command += ["mcp", "--root", str(Path(root).expanduser().resolve())]
    if client == "claude":
        return shlex.join(["claude", "mcp", "add", "grasp", "--scope", "user", "--transport", "stdio", "--",
                           *command])
    if client == "codex":
        return "[mcp_servers.grasp]\ncommand = " + json.dumps(command[0], ensure_ascii=False) + (
            "\nargs = " + json.dumps(command[1:], ensure_ascii=False))
    return json.dumps(dict(mcpServers=dict(grasp=dict(command=command[0], args=command[1:]))),
                      ensure_ascii=False, indent=2)


def instructions(root):
    return T("MCP 可读取允许目录中的结果、创建和更新方案、启动与停止计算。客户端通过标准输入输出连接，不开放端口。") + (
        "\n\n" + "\n\n".join(name + ":\n" + client_config(name, root) for name in ("claude", "codex", "gemini")))


def arguments(name, values):
    if not isinstance(values, dict):
        raise ValueError("arguments must be an object")
    _, properties, required = TOOLS[name]
    if set(values) - properties.keys() or set(required) - values.keys():
        raise ValueError("unknown or missing tool arguments")
    for key, value in values.items():
        spec = properties[key]
        expected = dict(string=str, object=dict, array=list, integer=int, boolean=bool)[spec["type"]]
        if type(value) is not expected:
            raise ValueError(f"invalid argument type: {key}")
        if expected is int and (value < spec.get("minimum", value) or value > spec.get("maximum", value)):
            raise ValueError(f"argument out of range: {key}")
        if expected is list and (len(value) > spec.get("maxItems", len(value)) or
                                 any(not isinstance(x, str) for x in value)):
            raise ValueError(f"invalid array: {key}")
    return dict(values)


class Server:
    def __init__(self, root, allow_custom=False):
        self.service = service.Service(root, allow_custom)
        self.initialized = False
        self.ready = False
        self.protocol = PROTOCOLS[0]

    def call(self, name, params):
        if not core.load_config().get("mcp_enabled", False):
            raise ValueError(T("MCP 已关闭。只能由用户本人打开（GUI 的“设置”菜单，或用户自己在终端运行命令）；请告知用户，不要代为执行。"))
        values = arguments(name, params)
        if "config" in values:
            values["configuration"] = values.pop("config")
        if name.startswith("plot_"):
            import grasp_render
            return grasp_render.render(self.service, name, **values)
        return getattr(self.service, name)(**values)

    def result(self, name, params):
        try:
            try:
                data = self.call(name, params)
            except service.PreflightError as e:
                data = e.data
            if isinstance(data, dict) and "_image" in data:
                raw = data.pop("_image")
                content = [dict(type="image", mimeType="image/png", data=base64.b64encode(raw).decode("ascii"))]
            else:
                content = []
            encoded = json.dumps(data, ensure_ascii=False, allow_nan=False)
            content.append(dict(type="text", text=encoded))
            result = dict(content=content, isError=data.get("started") is False or data.get("valid") is False)
            if self.protocol != "2025-03-26":
                result["structuredContent"] = data
            if len(json.dumps(result).encode("utf-8")) > MAX_RESPONSE:
                raise ValueError(T("结果太大，请减少文件、能级或成分数量"))
            return result
        except Exception as e:
            # 协议边界返回明确的工具错误，异常不能破坏后续请求或输出非 JSON 文本。
            return dict(content=[dict(type="text", text=str(e)[:service.MAX_TEXT])], isError=True)

    def message(self, message):
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or not isinstance(
                message.get("method"), str):
            return dict(jsonrpc="2.0", id=None, error=dict(code=-32600, message="Invalid Request"))
        method, ident = message["method"], message.get("id")
        if "id" not in message:
            if method == "notifications/initialized" and self.initialized:
                self.ready = True
            return None
        if type(ident) not in (str, int) or isinstance(ident, bool):
            return dict(jsonrpc="2.0", id=None, error=dict(code=-32600, message="Invalid request id"))
        params = message.get("params", {})
        try:
            if not isinstance(params, dict):
                raise ValueError("params must be an object")
            if method == "initialize" and not self.initialized:
                version = params.get("protocolVersion")
                if not isinstance(version, str) or not isinstance(params.get("capabilities"), dict) or not isinstance(
                        params.get("clientInfo"), dict):
                    raise ValueError("invalid initialize parameters")
                self.protocol = version if version in PROTOCOLS else PROTOCOLS[0]
                self.initialized = True
                result = dict(protocolVersion=self.protocol, capabilities=dict(tools=dict(listChanged=False)),
                              serverInfo=dict(name="grasp-gui", version=core.VERSION))
            elif method == "ping":
                result = {}
            elif not self.ready:
                raise ValueError("initialize and notifications/initialized are required")
            elif method == "tools/list":
                if set(params) - {"_meta"}:
                    raise ValueError("tool pagination is not needed")
                result = dict(tools=[dict(name=n, description=desc, inputSchema=dict(type="object", properties=props,
                                                                                   required=required,
                                                                                   additionalProperties=False))
                                     for n, (desc, props, required) in TOOLS.items()])
            elif method == "tools/call":
                name = params.get("name")
                if not isinstance(name, str) or name not in TOOLS:
                    raise ValueError("unknown tool")
                result = self.result(name, params.get("arguments", {}))
            else:
                return dict(jsonrpc="2.0", id=ident, error=dict(code=-32601, message="Method not found"))
            return dict(jsonrpc="2.0", id=ident, result=result)
        except ValueError as e:
            return dict(jsonrpc="2.0", id=ident, error=dict(code=-32602, message=str(e)))

    def serve(self, source=None, target=None):
        source = source if source is not None else sys.stdin.buffer
        target = target if target is not None else sys.stdout
        while True:
            raw = source.readline(MAX_FRAME + 1)
            if not raw:
                return 0
            if len(raw) > MAX_FRAME:
                while raw and not raw.endswith(b"\n"):
                    raw = source.readline(MAX_FRAME + 1)
                response = dict(jsonrpc="2.0", id=None, error=dict(code=-32700, message="Message too large"))
            else:
                try:
                    message = json.loads(raw.decode("utf-8"))
                except (ValueError, RecursionError):
                    response = dict(jsonrpc="2.0", id=None, error=dict(code=-32700, message="Parse error"))
                else:
                    response = self.message(message)
            if response is not None:
                target.write(json.dumps(response, ensure_ascii=True, allow_nan=False) + "\n")
                target.flush()


def main(argv=None):
    parser = argparse.ArgumentParser(description="GRASP GUI MCP stdio")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--enable", action="store_true")
    mode.add_argument("--disable", action="store_true")
    mode.add_argument("--print-config", choices=("claude", "codex", "gemini"))
    parser.add_argument("--root", default="~/grasp_calcs")
    parser.add_argument("--allow-custom", action="store_true")
    args = parser.parse_args(argv)
    core.set_lang(core.load_config().get("lang", core.LANG))
    if args.print_config:
        print(client_config(args.print_config, args.root))
        print(T("MCP 默认关闭；请先由用户运行 grasp_gui mcp --enable"), file=sys.stderr)
        return 0
    if args.enable or args.disable:
        core.save_config(mcp_enabled=args.enable)
        if core.load_config().get("mcp_enabled", False) != args.enable:
            print(T("无法保存 MCP 开关"), file=sys.stderr)
            return 1
        print(T("MCP 已打开") if args.enable else T("MCP 已关闭"))
        return 0
    if not core.load_config().get("mcp_enabled", False):
        print(T("MCP 已关闭；请由用户运行 grasp_gui mcp --enable 打开"), file=sys.stderr)
        return 1
    return Server(args.root, args.allow_custom).serve()
