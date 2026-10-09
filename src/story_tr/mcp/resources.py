# -*- coding: utf-8 -*-
"""只读 Resource 函数（纯 Python，**不依赖 mcp**）：返回各资源 URI 对应的文本内容。

URI 约定（与 `server.py` 的注册保持一致；首段为命名空间，避免模板互相歧义）：
    story://characters                              全部角色索引
    story://character/{id}                          角色 meta / 资料 / 故事清单
    story://story/{id}/{story}/{lang}               故事原始 Markdown（lang = zh / en）
    story://result/{id}/{story}                     翻译结果概要（segments.json）
"""
from __future__ import annotations

import json
import os

from .. import stories
from . import tools

VALID_LANGS = ("zh", "en")


def characters_index() -> str:
    """`story://characters`。"""
    return json.dumps(tools.load_character_index(), ensure_ascii=False, indent=2)


def character_profile(char_id: str) -> str:
    """`story://character/{id}` —— 角色条目 + 故事清单 + `meta.json` / `text.json`。"""
    char = tools.find_character(char_id)
    prof = os.path.join(stories.STORY_ROOT, char_id, "profile")
    data = {"character": char, "stories": tools.list_stories(char_id)}
    for name in ("meta.json", "text.json"):
        path = os.path.join(prof, name)
        if os.path.exists(path):
            data[name[:-5]] = tools.read_json(path)
    return json.dumps(data, ensure_ascii=False, indent=2)


def story_markdown(char_id: str, story: str, lang: str) -> str:
    """`story://character/{id}/{story}/{lang}` —— 故事原始 Markdown（保留 frontmatter）。"""
    if lang not in VALID_LANGS:
        raise ValueError("lang 只能是 %s：%s" % (" / ".join(VALID_LANGS), lang))
    path = os.path.join(tools.profile_story_dir(char_id), story, lang + ".md")
    if not os.path.exists(path):
        raise FileNotFoundError("故事文件不存在：%s/%s/%s.md" % (char_id, story, lang))
    with open(path, encoding="utf-8") as f:
        return f.read()


def result_summary(char_id: str, story: str) -> str:
    """`story://character/{id}/result/{story}` —— 翻译结果概要（segments.json 原文）。"""
    return json.dumps(tools.get_result_summary(char_id, story), ensure_ascii=False, indent=2)
