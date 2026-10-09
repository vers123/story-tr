#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""兼容入口：等价于 `story-tr chain ...`（实现在 story_tr.chain）。

保留本文件是为了让旧的 `python scripts/translate_chain.py ...` 继续可用；
推荐改用安装后的 `story-tr chain ...`（见 README）。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from story_tr.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(["chain"] + sys.argv[1:]))
