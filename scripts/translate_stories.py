#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""兼容入口：等价于 `story-tr translate ...`（实现在 story_tr.stories）。"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from story_tr.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(["translate"] + sys.argv[1:]))
