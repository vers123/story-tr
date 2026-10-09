#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""兼容入口：`python main.py <子命令> ...`，等价于安装后的 `story-tr <子命令>`。

未安装（未 `pip install .`）时自动把 `src/` 加入 `sys.path`，因此克隆后可直接运行。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from story_tr.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
