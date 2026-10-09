# -*- coding: utf-8 -*-
"""工作目录解析：`data/`、`logs/`、`out/`、`.env`、`*.state.json` 都以它为根。

优先级：
    1. `--data-dir`（CLI 会在导入各子模块**之前**写入 `$STORY_TR_HOME`）
    2. `$STORY_TR_HOME`
    3. 从当前目录**向上查找**含 `data/character.json` 的最近目录
    4. 当前目录（CWD）
"""
from __future__ import annotations

import argparse
import os

MARKER = os.path.join("data", "character.json")


def home() -> str:
    """返回工作目录（绝对路径）。模块级路径常量在导入时调用本函数取值。"""
    env = os.environ.get("STORY_TR_HOME")
    if env:
        return os.path.abspath(env)
    cwd = os.path.abspath(os.getcwd())
    cur = cwd
    while True:
        if os.path.exists(os.path.join(cur, MARKER)):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            return cwd
        cur = parent


def resolve(cli_value=None) -> str:
    """按优先级解析工作目录（供 CLI 使用）。"""
    if cli_value:
        return os.path.abspath(cli_value)
    return home()


def data_dir_parent():
    """`--data-dir` 参数定义（父解析器），供根解析器与各子命令复用。"""
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--data-dir", default="",
                   help="工作目录（data/ logs/ out/ .env 的根）；"
                        "默认 $STORY_TR_HOME，其次向上查找 data/character.json，最后当前目录")
    return p
