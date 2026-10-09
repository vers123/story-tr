# -*- coding: utf-8 -*-
"""只读 Tool 函数（纯 Python，**不依赖 mcp**）。

`server.py` 把这里的函数注册成 MCP Tool；单独导入本模块不会引入 mcp 依赖，
因此这些函数可在全部受支持的 Python 版本下测试。所有路径都取自 `story_tr.stories`
的模块级常量（`STORY_ROOT` / `CHAR_FILE`），便于测试时 monkeypatch。
"""
from __future__ import annotations

import json
import os
import re

from .. import stories

# 结果明细的合法 key：full / title / seg_NN
RESULT_KEY_RE = re.compile(r"(?:full|title|seg_\d+)")


# --------------------------------------------------------------------------- #
# 数据访问（tools / resources 共用）
# --------------------------------------------------------------------------- #
def load_character_index() -> list[dict]:
    """读取 `data/character.json`（元素含 id / name_en / name_zh / path / stories）。"""
    with open(stories.CHAR_FILE, encoding="utf-8") as f:
        return json.load(f)


def find_character(char_id: str) -> dict:
    """按 id 取角色条目；找不到抛 `LookupError`。"""
    for c in load_character_index():
        if c.get("id") == char_id:
            return c
    raise LookupError("未知角色 id：%s（可用 list_characters 查询全部 id）" % char_id)


def profile_story_dir(char_id: str) -> str:
    """角色的**故事源**目录：`{id}/profile/story`。"""
    return os.path.join(stories.STORY_ROOT, char_id, "profile", "story")


def read_json(path: str):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# --------------------------------------------------------------------------- #
# Tools
# --------------------------------------------------------------------------- #
def list_characters() -> list[dict]:
    """全部角色：id / 中英名 / 故事数。"""
    return [{"id": c.get("id"), "name_zh": c.get("name_zh"), "name_en": c.get("name_en"),
             "story_count": len(c.get("stories") or [])}
            for c in load_character_index()]


def list_stories(char_id: str) -> list[dict]:
    """某角色的故事列表：文件夹名 + 中英标题（frontmatter）+ 段落数。"""
    char = find_character(char_id)
    base = profile_story_dir(char_id)
    out = []
    for story in char.get("stories") or []:
        zh = os.path.join(base, story, "zh.md")
        en = os.path.join(base, story, "en.md")
        if not os.path.exists(zh):
            continue
        out.append({"story": story,
                    "title_zh": stories.read_title(zh),
                    "title_en": stories.read_title(en) if os.path.exists(en) else None,
                    "paragraph_count": len(stories.read_paragraphs(zh))})
    return out


def get_result_summary(char_id: str, story: str) -> dict:
    """翻译结果**概要**：读取 `result/{story}/segments.json`（标题 / 全文 / 各段的源文与最终译文）。"""
    path = os.path.join(stories.story_dir(char_id), story, "segments.json")
    if not os.path.exists(path):
        raise FileNotFoundError(
            "尚无翻译结果：%s/%s（请先运行：story-tr translate %s --stories %s）"
            % (char_id, story, char_id, story))
    return read_json(path)


def read_result_item(char_id: str, story: str, key: str) -> dict:
    """翻译结果**明细**：key 取 `full` / `title` / `seg_NN`。"""
    if not RESULT_KEY_RE.fullmatch(key):
        raise ValueError("key 只能是 full / title / seg_NN（如 seg_01）：%s" % key)
    path = os.path.join(stories.story_dir(char_id), story, key + ".json")
    if not os.path.exists(path):
        raise FileNotFoundError("结果不存在：%s/%s/%s.json" % (char_id, story, key))
    return read_json(path)
