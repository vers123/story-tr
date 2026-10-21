# -*- coding: utf-8 -*-
"""状态文件（`.translate_state.json` / `.translate_stories_state.json`）的原子读写。

- 写入：先写 `.tmp`，保留上一版 `.bak`，再 `os.replace`，避免崩溃留下截断 JSON
- 读取：主文件损坏时回退 `.bak`；都不可用则把损坏文件改名为 `.corrupt-<时间戳>`
- 清理：同时删除主文件与 `.bak`
"""
from __future__ import annotations

import json
import logging
import os
import shutil
from datetime import datetime

log = logging.getLogger("translate_chain")


def atomic_write_json(path: str, obj) -> None:
    """原子写 JSON：临时文件 + 备份 + `os.replace`。"""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    if os.path.exists(path):
        try:
            shutil.copy2(path, path + ".bak")
        except OSError:  # 备份失败不影响主写入
            pass
    os.replace(tmp, path)


def load_json_with_backup(path: str):
    """读取 JSON；损坏时尝试 `.bak`，再失败则隔离损坏文件并返回 None。"""
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        backup = path + ".bak"
        if os.path.exists(backup):
            try:
                with open(backup, encoding="utf-8") as f:
                    state = json.load(f)
                log.warning("状态文件损坏（%s），已从备份 %s 恢复。",
                            exc, os.path.basename(backup))
                return state
            except (OSError, json.JSONDecodeError):
                pass
        corrupt = path + ".corrupt-" + datetime.now().strftime("%Y%m%d_%H%M%S")
        try:
            os.replace(path, corrupt)
        except OSError:
            pass
        log.warning("状态文件无法读取（%s），已保留为 %s，本次按无进度启动。",
                    exc, os.path.basename(corrupt))
        return None


def remove_with_backup(path: str) -> None:
    """删除状态文件及其备份（正常完成后调用）。"""
    for p in (path, path + ".bak"):
        if os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass
