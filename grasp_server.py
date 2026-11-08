#!/usr/bin/env python3
"""不含 Qt 的服务器入口；与桌面版共用命令实现，不尝试打开窗口。"""

import sys

import grasp_core as core
from grasp_core import T


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    core.set_lang(core.load_config().get("lang", "zh"))
    if args[:1] == ["--lang"] and len(args) >= 2 and args[1] in ("zh", "en"):
        core.set_lang(args[1])
        args = args[2:]
    if args == ["--version"]:
        from grasp_remote import PROTOCOL
        print("GRASP GUI " + core.VERSION + " (server; remote protocol " + str(PROTOCOL) + ")")
        return 0
    if args[:1] == ["remote"]:
        import grasp_remote
        return grasp_remote.main(args[1:])
    if args[:1] == ["mcp"]:
        import grasp_mcp
        return grasp_mcp.main(args[1:])
    if args[:1] == ["_run"] and len(args) == 2:
        import grasp_bg
        return grasp_bg.serve(args[1])
    import grasp_config
    if args[:1] and args[0] in grasp_config.COMMANDS:
        return grasp_config.cli(args)
    if args in (["--help"], ["-h"]):
        print(T("服务器版命令：remote、mcp、check、script、steps、reference、_run；--version 显示程序和协议版本。"))
        return 0
    print(T("这是服务器版，不能打开窗口；请使用桌面版或旧系统版。"), file=sys.stderr)
    return 2


if __name__ == "__main__":
    # 冻结后的分析子进程先进入 multiprocessing，不能被误当成界面命令。
    import multiprocessing
    multiprocessing.freeze_support()
    sys.exit(main())
