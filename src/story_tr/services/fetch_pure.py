"""抓取数据的纯函数：slug、标题选择、段落拆分、内容指纹与差异描述。"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

LOCS = ("en", "chs")


def slug(s: str | None) -> str:
    s = (s or "").lower()
    s = s.replace("'s", "").replace("'", "")
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return s.strip("_")


def pick_title(t1: str | None, t2: str | None) -> str:
    """主标题可用就用主标题；主标题是占位/空（如 ???/？？？）时才用 title2。"""
    t1 = (t1 or "").strip()
    if t1 and re.search(r"[A-Za-z0-9\u4e00-\u9fff]", t1) and set(t1) - set("?？. "):
        return t1
    return (t2 or t1).strip()


def paragraphs(raw: str | None) -> list[str]:
    """站点用字面量 `\\n` 作换行，据此拆分。"""
    if not raw:
        return []
    return [p.strip() for p in raw.replace("\\n", "\n").split("\n") if p.strip()]


def fingerprint(rec: dict[str, Any]) -> str:
    """内容指纹：对会写入文件的 avatar + story（en/chs）取 sha1。"""
    payload = {
        loc: {"avatar": (rec.get(loc) or {}).get("avatar"), "story": (rec.get(loc) or {}).get("story")}
        for loc in LOCS
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def story_diff(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    """比较两版抓取记录，返回人类可读的变化点（去重、保持出现顺序）。"""
    out: list[str] = []
    for loc in LOCS:
        a = {s["i"]: s for s in ((old.get(loc) or {}).get("story") or [])}
        b = {s["i"]: s for s in ((new.get(loc) or {}).get("story") or [])}
        if set(a) != set(b):
            out.append(f"{loc} 故事数 {len(a)}→{len(b)}")
        for i in sorted(set(a) & set(b)):
            if (a[i].get("text"), a[i].get("text2")) != (b[i].get("text"), b[i].get("text2")):
                out.append(f"{loc} 故事 {i} 文本变化")
        if ((old.get(loc) or {}).get("avatar") or {}) != ((new.get(loc) or {}).get("avatar") or {}):
            out.append(f"{loc} 角色资料变化")
    return list(dict.fromkeys(out))


def make_char_entry(cid: str, rec: dict[str, Any]) -> dict[str, Any]:
    """把抓取记录转成 `data/character.json` 条目。"""
    en = (rec.get("en") or {}).get("avatar") or {}
    zh = (rec.get("chs") or {}).get("avatar") or {}
    stories = [
        slug(pick_title(s.get("title"), s.get("title2"))) for s in ((rec.get("en") or {}).get("story") or [])
    ]
    return {
        "id": cid,
        "name_en": en.get("name"),
        "name_zh": zh.get("name"),
        "path": f"data/story/character/{cid}",
        "stories": stories,
    }
