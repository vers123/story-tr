"""services 层共享输入解析：角色 id 选择、故事 Markdown 读取与故事发现。

`translate` / `clean` / `eval` 共用这些函数，避免同一套解析逻辑重复实现。
"""

from __future__ import annotations

import json
import logging
import os

from story_tr.core import parse_frontmatter, split_paragraphs

from . import storage

log = logging.getLogger("story_tr.services.story_inputs")


def load_char_ids(home_: str) -> list[str]:
    """读取 `data/character.json` 中的角色 id 列表（保持文件顺序）。"""
    with open(storage.char_index_path(home_), encoding="utf-8") as f:
        data = json.load(f)
    return [c["id"] for c in data if c.get("id")]


def parse_char_ids(tokens: list[str], all_ids: list[str], *, warn_missing: bool = False) -> list[str]:
    """把 `id` / `id-id` / 逗号分隔的 token 展开为 `all_ids` 顺序的子集。

    无法解析的范围抛 `ValueError`；`warn_missing` 为真时对不在索引中的 id 记警告。
    """
    want: set[str] = set()
    for tok in tokens:
        for part in tok.replace(" ", "").split(","):
            if not part:
                continue
            if "-" in part:
                a, b = part.split("-", 1)
                try:
                    start, end = int(a), int(b)
                except ValueError:
                    raise ValueError(f"无法解析 id 范围：{part}") from None
                if start > end:
                    start, end = end, start
                want.update(str(i) for i in range(start, end + 1))
            else:
                want.add(part)
    missing = sorted(want - set(all_ids))
    if missing and warn_missing:
        log.warning("提示：以下 id 不在 character.json 中，已跳过：%s", ",".join(missing))
    return [i for i in all_ids if i in want]


def read_paragraphs(md_path: str) -> list[str]:
    """读取故事 Markdown 的正文段落（忽略 frontmatter）。"""
    with open(md_path, encoding="utf-8") as f:
        raw = f.read()
    _, body = parse_frontmatter(raw)
    return split_paragraphs(body)


def read_title(md_path: str) -> str | None:
    """读取故事 Markdown frontmatter 中的 `title`（缺失返回 `None`）。"""
    with open(md_path, encoding="utf-8") as f:
        raw = f.read()
    fm, _ = parse_frontmatter(raw)
    if not fm:
        return None
    return fm.get("title") or None


def resolve_stories(home_: str, cid: str, names: list[str], src_file: str = "zh.md") -> list[str]:
    """按名称取故事；未指定名称时返回含正文的来源文件对应的故事目录。"""
    base = storage.profile_story_dir(home_, cid)
    if names:
        return names
    if not os.path.isdir(base):
        return []
    out: list[str] = []
    for n in sorted(os.listdir(base)):
        if os.path.isdir(os.path.join(base, n)):
            md = os.path.join(base, n, src_file)
            if os.path.exists(md) and read_paragraphs(md):
                out.append(n)
    return out
