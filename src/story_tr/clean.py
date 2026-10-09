#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""清理 `{id}/result/` 下的翻译产物（full / seg_NN / segments.json 等）。

- 选择与 `translate` 一致：char_ids（id / 范围 / 逗号）或 --all-characters，`--stories` 可细化
- 只删 result/ 里的内容，**保留 `{id}/result/` 目录本身**（维持目录结构约定）
- 默认**交互确认**；`--dry-run` 只预览；`-y/--yes` 跳过确认
- 默认同步移除 `.translate_stories_state.json` 中对应的 `id|story|key` 记录
  （否则重跑 translate 会把这些任务当作「已完成」跳过）；用 `--keep-state` 保留状态
"""
from __future__ import annotations

import os
import shutil

from . import paths, stories


# --------------------------------------------------------------------------- #
# 目标与统计
# --------------------------------------------------------------------------- #
def targets(char_id, names=None):
    """返回该角色 `result/` 下要清理的路径（names 为空则取全部）。"""
    base = stories.story_dir(char_id)
    if not os.path.isdir(base):
        return []
    if names:
        return [os.path.join(base, n) for n in names if os.path.exists(os.path.join(base, n))]
    return [os.path.join(base, n) for n in sorted(os.listdir(base))]


def measure(paths_):
    """统计 (文件数, 字节数)。"""
    n = size = 0
    for p in paths_:
        if os.path.isdir(p):
            for root, _, files in os.walk(p):
                for f in files:
                    n += 1
                    size += os.path.getsize(os.path.join(root, f))
        elif os.path.isfile(p):
            n += 1
            size += os.path.getsize(p)
    return n, size


def human(size):
    """字节数 → 可读字符串。"""
    if size < 1024:
        return "%d B" % size
    for unit in ("KB", "MB", "GB"):
        size /= 1024.0
        if size < 1024 or unit == "GB":
            return "%.1f %s" % (size, unit)
    return "%.1f GB" % size


def remove(paths_):
    """删除文件或目录（目录递归）。"""
    for p in paths_:
        if os.path.isdir(p):
            shutil.rmtree(p)
        elif os.path.exists(p):
            os.remove(p)


# --------------------------------------------------------------------------- #
# 状态记录
# --------------------------------------------------------------------------- #
def state_key_match(key, ids, names):
    """判断 state 里的 `id|story|key` 是否命中（names 为空表示不限故事）。"""
    parts = key.split("|")
    if len(parts) < 2 or parts[0] not in ids:
        return False
    return not names or parts[1] in names


def clean_state(char_ids, names=None, dry_run=False):
    """移除（或仅统计）状态文件中对应记录，返回命中条数。

    同时**重置批次签名**：清理后「已完成集合」已不再对应原批次参数，
    签名作废后下次 `translate` 可直接用新参数运行（否则会因签名不一致要求 --reset）。
    """
    state = stories.load_state()
    if not state or not state.get("completed"):
        return 0
    ids, wanted = set(char_ids), set(names or [])
    hits = [k for k in state["completed"] if state_key_match(k, ids, wanted)]
    if hits and not dry_run:
        dropped = set(hits)
        state["completed"] = [k for k in state["completed"] if k not in dropped]
        state.pop("signature", None)
        stories.save_state(state)
    return len(hits)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def add_parser(sub):
    """注册 `story-tr clean` 子命令。"""
    p = sub.add_parser(
        "clean",
        help="清理 {id}/result/ 下的翻译产物（支持 id / 范围），保留 result 目录本身",
        parents=[paths.data_dir_parent()],
        epilog="示例：story-tr clean 10000021   |   story-tr clean 10000002-10000030   |   "
               "story-tr clean --all-characters --dry-run",
    )
    p.add_argument("char_ids", nargs="*",
                   help="角色 id / 范围（空格或逗号分隔）："
                        "10000021 / 10000002-10000030 / 10000021,10000025-10000030")

    g = p.add_argument_group("角色选择")
    g.add_argument("--all-characters", action="store_true",
                   help="处理 data/character.json 中的全部角色（与 char_ids 互斥）")

    g = p.add_argument_group("故事选择")
    g.add_argument("--stories", default="",
                   help="只清理指定故事，逗号分隔（不指定 = 清理 result/ 下的全部内容）")

    g = p.add_argument_group("安全")
    g.add_argument("--dry-run", action="store_true", help="只预览将删除的内容，不实际删除")
    g.add_argument("-y", "--yes", action="store_true", help="跳过交互确认")
    g.add_argument("--keep-state", action="store_true",
                   help="保留 .translate_stories_state.json 记录（默认同步清理，否则重跑会被跳过）")

    p.set_defaults(func=main)
    return p


def main(args) -> int:
    if args.char_ids and args.all_characters:
        print("不能同时指定角色 id 与 --all-characters，请二选一。")
        return 1
    if not args.char_ids and not args.all_characters:
        print("请指定角色 id / 范围（如 10000021 或 10000002-10000030），"
              "或用 --all-characters 处理全部角色。")
        return 1

    all_ids = stories.load_char_ids()
    char_ids = all_ids if args.all_characters else stories.parse_char_ids(args.char_ids, all_ids)
    if not char_ids:
        print("没有匹配到任何角色 id；请对照 data/character.json 检查。")
        return 1
    names = [s.strip() for s in args.stories.split(",") if s.strip()] or None

    plan, total_n, total_size = [], 0, 0
    for cid in char_ids:
        ts = targets(cid, names)
        if not ts:
            continue
        n, size = measure(ts)
        plan.append((cid, ts, n, size))
        total_n += n
        total_size += size
    state_hits = clean_state(char_ids, names, dry_run=True)

    if not plan and not state_hits:
        print("没有可清理的翻译结果。")
        return 0

    print("将清理以下 result/ 内容：")
    for cid, ts, n, size in plan:
        label = ", ".join(os.path.basename(t) for t in ts)
        print("  %s/result/  [%s]  %d 个文件，%s" % (cid, label, n, human(size)))
    print("合计：%d 个角色，%d 个文件，%s" % (len(plan), total_n, human(total_size)))
    print("状态记录：%s（%d 条）" % ("保留（--keep-state）" if args.keep_state else "同步清理并重置批次签名",
                                  state_hits))

    if args.dry_run:
        print("--dry-run：未做任何删除。")
        return 0
    if not args.yes:
        try:
            ans = input("确认清理？[y/N] ").strip().lower()
        except EOFError:
            ans = ""
        if ans not in ("y", "yes"):
            print("已取消。")
            return 0

    for _, ts, _, _ in plan:
        remove(ts)
    print("已清理 %d 个角色的 result/ 内容（result/ 目录本身保留）。" % len(plan))
    if not args.keep_state:
        print("状态记录：移除 %d 条，并重置批次签名（下次 translate 可直接运行）。"
              % clean_state(char_ids, names))
    return 0
