"""`result/` 目录扫描：文件计数、角色/故事覆盖与源语言分布。"""

from __future__ import annotations

import json
import os
from typing import Any

COUNT_KEYS = ("summaries", "titles", "fulls", "segments", "segment_mp3")


def dir_size(path: str) -> tuple[int, int]:
    """递归统计目录的 `(文件数, 字节数)`；目录不存在返回 `(0, 0)`。"""
    files = size = 0
    if not os.path.isdir(path):
        return 0, 0
    for root, _, names in os.walk(path):
        for name in names:
            try:
                size += os.path.getsize(os.path.join(root, name))
                files += 1
            except OSError:
                pass
    return files, size


def segment_lang(path: str) -> str:
    """读取单个段落结果的源语言（损坏 / 非字典 → `unknown`）。"""
    try:
        with open(path, encoding="utf-8") as f:
            row = json.load(f)
    except (OSError, ValueError):
        return "unknown"
    if not isinstance(row, dict):
        return "unknown"
    return row.get("source_lang") or row.get("source_detected_lang") or "unknown"


def _scan_story_dir(path: str, deep: bool, counts: dict[str, int], langs: dict[str, int]) -> bool:
    """统计单个故事结果目录；返回是否含有段落结果。"""
    try:
        names = os.listdir(path)
    except OSError:
        return False
    has_segment = False
    for name in names:
        if name == "segments.json":
            counts["summaries"] += 1
        elif name == "title.json":
            counts["titles"] += 1
        elif name == "full.json":
            counts["fulls"] += 1
        elif name.startswith("seg_") and name.endswith(".json"):
            counts["segments"] += 1
            has_segment = True
            if deep:
                lang = segment_lang(os.path.join(path, name))
                langs[lang] = langs.get(lang, 0) + 1
        elif name.startswith("seg_") and name.endswith(".mp3"):
            counts["segment_mp3"] += 1
    return has_segment


def scan_results(root: str, deep: bool = False) -> dict[str, Any]:
    """单次遍历 `result/` 树，返回文件计数与覆盖集合。"""
    with_result: set[str] = set()
    stories_with_result: set[tuple[str, str]] = set()
    counts: dict[str, int] = dict.fromkeys(COUNT_KEYS, 0)
    langs: dict[str, int] = {}
    if not os.path.isdir(root):
        return {
            "counts": counts,
            "with_result": with_result,
            "stories_with_result": stories_with_result,
            "langs": langs,
        }
    for cid_entry in os.scandir(root):
        if not cid_entry.is_dir():
            continue
        rdir = os.path.join(cid_entry.path, "result")
        if not os.path.isdir(rdir):
            continue
        try:
            story_entries = list(os.scandir(rdir))
        except OSError:
            continue
        if story_entries:
            with_result.add(cid_entry.name)
        for story_entry in story_entries:
            if story_entry.is_dir() and _scan_story_dir(story_entry.path, deep, counts, langs):
                stories_with_result.add((cid_entry.name, story_entry.name))
    return {
        "counts": counts,
        "with_result": with_result,
        "stories_with_result": stories_with_result,
        "langs": langs,
    }


def coverage(chars: list[dict[str, Any]], scanned: dict[str, Any]) -> dict[str, int]:
    """按 `character.json` 计算角色 / 故事的结果覆盖率。"""
    with_result: set[str] = scanned["with_result"]
    stories_with_result: set[tuple[str, str]] = scanned["stories_with_result"]
    valid = [c for c in chars if c.get("id")]
    with_count = sum(1 for c in valid if c["id"] in with_result)
    stories_with = 0
    stories_without = 0
    for c in chars:
        cid = c.get("id")
        if not cid:
            continue
        for story in c.get("stories") or []:
            if (cid, story) in stories_with_result:
                stories_with += 1
            else:
                stories_without += 1
    return {
        "characters_with_result": with_count,
        "characters_without_result": len(valid) - with_count,
        "stories_with_result": stories_with,
        "stories_without_result": stories_without,
    }
