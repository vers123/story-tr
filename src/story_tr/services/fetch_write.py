"""抓取结果落盘：故事 markdown、角色档案 JSON 与角色索引。"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from story_tr import models

from . import storage
from .fetch_pure import paragraphs, pick_title, slug

log = logging.getLogger("story_tr.services.fetch_write")


def _s(value: Any) -> str:
    return "" if value is None else str(value)


def _opt_str(value: Any) -> str | None:
    return None if value is None else str(value)


def _opt_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _str_map(value: Any) -> dict[str, str]:
    """把接口返回的扁平字典统一转成 `str -> str`（站点部分条目用整数 ID 表示声优）。"""
    if not isinstance(value, dict):
        return {}
    return {str(k): _s(v) for k, v in value.items() if v is not None}


def _opt_int_list(value: Any) -> list[int] | None:
    if not isinstance(value, list):
        return None
    out: list[int] = []
    for x in value:
        try:
            out.append(int(x))
        except (TypeError, ValueError):
            continue
    return out or None


def _story_markdown(cid: str, folder: str, lang: str, entry: dict[str, Any]) -> str:
    """渲染一篇故事的 Markdown（frontmatter + 段落）。"""
    title = pick_title(entry.get("title"), entry.get("title2"))
    paras = paragraphs((entry.get("text") or "") + (entry.get("text2") or ""))
    title_json = json.dumps(title, ensure_ascii=False)
    return (
        f'---\nid: "{cid}"\nstory: {folder}\nlang: {lang}\n'
        f"title: {title_json}\n---\n\n" + "\n\n".join(paras) + "\n"
    )


def write_story_files(
    root: str,
    cid: str,
    folders: list[str],
    stories: list[str],
    en_st: list[dict[str, Any]],
    zh_st: list[dict[str, Any]],
    report: dict[str, Any],
) -> None:
    """写出 en / zh 两份故事 md（内容未变则跳过）。"""
    used: set[str] = set()
    for idx in range(min(len(en_st), len(zh_st))):
        folder = folders[idx]
        if folder not in stories:
            report["problems"].append(f"{cid} story[{idx}]: 无对应文件夹 {folder}")
            continue
        if folder in used:
            report["problems"].append(f"{cid} story[{idx}]: 文件夹重复 {folder}")
        used.add(folder)
        for loc, entries in (("en", en_st), ("chs", zh_st)):
            lang = "en" if loc == "en" else "zh"
            content = _story_markdown(cid, folder, lang, entries[idx])
            d = os.path.join(root, cid, "profile", "story", folder)
            if storage.atomic_write_text(os.path.join(d, lang + ".md"), content):
                report["written"] += 1


def _pair_list(a: Any, b: Any) -> list[models.Costume]:
    a, b = a or [], b or []
    out: list[models.Costume] = []
    for i in range(max(len(a), len(b))):
        x = a[i] if i < len(a) else {}
        y = b[i] if i < len(b) else {}
        out.append(
            models.Costume(
                name_en=_s(x.get("name")),
                name_zh=_s(y.get("name")),
                description_en=_s(x.get("description")),
                description_zh=_s(y.get("description")),
            )
        )
    return out


def _pair_obj(a: Any, b: Any) -> dict[str, str]:
    a, b = a or {}, b or {}
    return {
        "name_en": _s(a.get("name")),
        "name_zh": _s(b.get("name")),
        "description_en": _s(a.get("description")),
        "description_zh": _s(b.get("description")),
    }


def _write_meta(prof: str, cid: str, av_en: dict[str, Any]) -> None:
    storage.atomic_write_json(
        os.path.join(prof, "meta.json"),
        models.CharacterMeta(
            id=cid,
            rank=_opt_int(av_en.get("rank")),
            element=_opt_str(av_en.get("element")),
            weapon_type=_opt_str(av_en.get("weaponType")),
            region=_opt_str(av_en.get("region")),
            body_type=_opt_str(av_en.get("bodyType")),
            special_prop=_opt_str(av_en.get("specialProp")),
            icon=_opt_str(av_en.get("icon")),
            birthday=_opt_int_list(av_en.get("birthday")),
            release=_opt_int(av_en.get("release")),
        ),
    )


def _write_text(prof: str, av_en: dict[str, Any], av_zh: dict[str, Any]) -> None:
    fe, fz = av_en.get("fetter", {}) or {}, av_zh.get("fetter", {}) or {}
    ce, cz = av_en.get("other", {}) or {}, av_zh.get("other", {}) or {}
    name_card = (
        _pair_obj(ce.get("nameCard"), cz.get("nameCard"))
        if (ce.get("nameCard") or cz.get("nameCard"))
        else None
    )
    special_food = (
        _pair_obj(ce.get("specialFood"), cz.get("specialFood"))
        if (ce.get("specialFood") or cz.get("specialFood"))
        else None
    )
    storage.atomic_write_json(
        os.path.join(prof, "text.json"),
        models.CharacterText(
            title_en=_s(fe.get("title")),
            title_zh=_s(fz.get("title")),
            detail_en=_s(fe.get("detail")),
            detail_zh=_s(fz.get("detail")),
            constellation_en=_s(fe.get("constellation")),
            constellation_zh=_s(fz.get("constellation")),
            native_en=_s(fe.get("native")),
            native_zh=_s(fz.get("native")),
            cv=_str_map(fe.get("cv")),
            costume=_pair_list(ce.get("costume"), cz.get("costume")),
            name_card=models.NameCard(**name_card) if name_card else None,
            special_food=models.SpecialFood(name_en=special_food["name_en"], name_zh=special_food["name_zh"])
            if special_food
            else None,
        ),
    )


def _sync_char(
    root: str,
    c: dict[str, Any],
    rec: dict[str, Any],
    only: set[str] | None,
    info: dict[str, Any],
    report: dict[str, Any],
) -> None:
    """同步单个角色：slug / stories 校正 + 故事与档案写盘。"""
    cid = c["id"]
    entry = {
        "id": cid,
        "name_en": c.get("name_en"),
        "name_zh": c.get("name_zh"),
        "path": c.get("path"),
        "stories": c.get("stories"),
    }
    report["index"].append(entry)
    stories = list(c.get("stories") or [])
    en_st = (rec.get("en") or {}).get("story") or []
    zh_st = (rec.get("chs") or {}).get("story") or []
    av_en = (rec.get("en") or {}).get("avatar")
    av_zh = (rec.get("chs") or {}).get("avatar")
    folders = [slug(pick_title(e.get("title"), e.get("title2"))) for e in en_st]

    if av_en and av_en.get("name") and av_en["name"] != c.get("name_en"):
        report["name_diffs"].append("{} en: 现={!r} 站={!r}".format(cid, c.get("name_en"), av_en["name"]))
    if av_zh and av_zh.get("name") and av_zh["name"] != c.get("name_zh"):
        report["name_diffs"].append("{} zh: 现={!r} 站={!r}".format(cid, c.get("name_zh"), av_zh["name"]))
    if not en_st:
        report["empty"].append(cid)

    added = [f for f in dict.fromkeys(folders) if f not in stories]
    if added and (only is None or cid in only):
        stories.extend(added)
        c["stories"] = stories
        entry["stories"] = stories
        info["added_stories"].append("{}：+{}".format(cid, ", ".join(added)))
    if sorted(folders) != sorted(stories):
        report["problems"].append(f"{cid}: slug 与 stories 不一致 | slug={folders} | stories={stories}")

    if only is not None and cid not in only:
        return
    write_story_files(root, cid, folders, stories, en_st, zh_st, report)
    prof = os.path.join(root, cid, "profile")
    os.makedirs(prof, exist_ok=True)
    if av_en:
        _write_meta(prof, cid, av_en)
    if av_en and av_zh:
        _write_text(prof, av_en, av_zh)


def _log_report(report: dict[str, Any]) -> None:
    log.info(
        "角色数: %d  写入故事文件: %d  无故事角色: %s",
        len(report["index"]),
        report["written"],
        report["empty"],
    )
    for problem in report["problems"]:
        log.warning("  - %s", problem)
    for diff in report["name_diffs"]:
        log.warning("  * %s", diff)


def write_dataset(
    home_: str,
    chars: list[dict[str, Any]],
    recs: dict[str, dict[str, Any]],
    only: set[str] | None = None,
    info: dict[str, Any] | None = None,
) -> None:
    """把抓取记录写成语料文件，并回写 `data/character.json`。"""
    info = info if info is not None else {}
    info.setdefault("added_stories", [])
    report: dict[str, Any] = {
        "problems": [],
        "name_diffs": [],
        "empty": [],
        "written": 0,
        "index": [],
    }
    root = storage.story_root(home_)
    for c in chars:
        rec = recs.get(c["id"])
        if not rec:
            report["problems"].append(f"{c['id']}: 无数据")
            report["index"].append(
                {
                    "id": c["id"],
                    "name_en": c.get("name_en"),
                    "name_zh": c.get("name_zh"),
                    "path": c.get("path"),
                    "stories": c.get("stories"),
                }
            )
            continue
        _sync_char(root, c, rec, only, info, report)
    storage.atomic_write_json(
        storage.char_index_path(home_),
        [models.CharacterRef(**e).model_dump(mode="json") for e in report["index"]],
    )
    _log_report(report)
