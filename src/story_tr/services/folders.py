"""按 `data/character.json` 的 stories 同步重命名各角色 `profile/story/` 下文件夹（幂等）。"""
from __future__ import annotations

import json
import logging
import os

from story_tr.errors import ExitCode

from . import _io

log = logging.getLogger("story_tr.services.folders")

FIXED = ["character_details"] + [f"character_story_{i}" for i in range(1, 6)]


def run(data_dir: str | None = None) -> ExitCode:
    home_ = _io.home(data_dir)
    char_file = _io.char_index_path(home_)
    if not os.path.exists(char_file):
        log.error("找不到 %s；请先运行 fetch。", char_file)
        return ExitCode.USAGE
    with open(char_file, encoding="utf-8") as f:
        chars = json.load(f)

    renames = 0
    warnings: list[str] = []

    for c in chars:
        sid = c["id"]
        target = list(c.get("stories") or [])
        sdir = _io.profile_story_dir(home_, sid)

        if not os.path.isdir(sdir):
            if target:
                warnings.append(f"[{sid}] 缺少目录 {_io.rel(home_, sdir)}")
            continue

        current = sorted(n for n in os.listdir(sdir) if os.path.isdir(os.path.join(sdir, n)))

        for fx in FIXED:
            if fx in target and fx not in current:
                warnings.append(f"[{sid}] 缺少固定文件夹 {fx}")

        if set(current) == set(target):
            continue  # 已一致，幂等

        leftover_cur = sorted(n for n in current if n not in set(target))
        leftover_tgt = [n for n in target if n not in set(current)]

        if len(leftover_cur) != len(leftover_tgt):
            warnings.append(f"[{sid}] 无法映射：当前多余 {leftover_cur}，目标多余 {leftover_tgt}")
            continue

        for old, new in zip(leftover_cur, leftover_tgt, strict=False):
            src = os.path.join(sdir, old)
            dst = os.path.join(sdir, new)
            if os.path.exists(dst):
                warnings.append(f"[{sid}] 目标已存在，跳过：{old} -> {new}")
                continue
            os.rename(src, dst)
            renames += 1
            log.info("[%s] %s -> %s", sid, old, new)

    log.info("重命名 %d 个文件夹。", renames)
    for w in warnings:
        log.warning("  %s", w)
    return ExitCode.USAGE if warnings else ExitCode.OK
