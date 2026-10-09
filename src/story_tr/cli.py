# -*- coding: utf-8 -*-
"""story-tr 命令行入口：子命令 `fetch` / `folders` / `clean` / `chain` / `translate`。"""
from __future__ import annotations

import argparse
import os
import sys

from . import __version__, paths


def build_parser() -> argparse.ArgumentParser:
    """构建完整解析器（调用时各子模块会按已定的工作目录完成导入）。"""
    from . import chain, clean, folders, stories, sync

    p = argparse.ArgumentParser(
        prog="story-tr",
        description="原神角色故事双语文本：数据抓取/校正 与「来回翻译 N 次」链路",
        parents=[paths.data_dir_parent()],
        epilog="示例：story-tr fetch   |   story-tr translate 10000002-10000030 --workers 4   |   "
               "story-tr chain \"Hello, World\"   |   story-tr clean 10000021",
    )
    p.add_argument("--version", action="version", version="story-tr %s" % __version__)
    sub = p.add_subparsers(dest="command", required=True, metavar="<子命令>")
    sync.add_parser(sub)
    folders.add_parser(sub)
    chain.add_parser(sub)
    stories.add_parser(sub)
    clean.add_parser(sub)
    return p


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    # 两段式解析：先取出 --data-dir，再导入子模块（其模块级路径常量依赖工作目录）
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--data-dir", default="")
    known, _ = pre.parse_known_args(argv)
    data_dir = paths.resolve(known.data_dir)
    os.environ["STORY_TR_HOME"] = data_dir

    args = build_parser().parse_args(argv)
    args.data_dir = data_dir  # 子命令解析可能把它覆盖为空，这里统一为已解析值
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
