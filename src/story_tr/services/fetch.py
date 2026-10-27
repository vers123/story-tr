"""从 Project Amber(yatta.moe) 拉取角色数据并生成 / 校正 `data/` 文件。

- 抓取：每个角色 en/chs 的 avatar 与 avatarFetter
- 产出：`data/character.json`、`profile/{meta,text}.json`、故事 `en.md`/`zh.md`
- 缓存：`data/_fetch_all.json`（带 hash 指纹，可重复运行只补缺失；`--refresh` 强制重抓）
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import re
import time
from collections.abc import Callable
from typing import Any

import httpx

from story_tr import models
from story_tr.errors import ExitCode

from . import _io

log = logging.getLogger("story_tr.services.fetch")

API = "https://gi.yatta.moe/api/v2/{loc}/{kind}/{cid}"
LIST_API = "https://gi.yatta.moe/api/v2/{loc}/avatar"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
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
    payload = {loc: {"avatar": (rec.get(loc) or {}).get("avatar"),
                     "story": (rec.get(loc) or {}).get("story")} for loc in LOCS}
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def story_diff(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
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
    seen: set[str] = set()
    uniq: list[str] = []
    for x in out:
        if x not in seen:
            seen.add(x)
            uniq.append(x)
    return uniq


def make_char_entry(cid: str, rec: dict[str, Any]) -> dict[str, Any]:
    en = (rec.get("en") or {}).get("avatar") or {}
    zh = (rec.get("chs") or {}).get("avatar") or {}
    stories = [slug(pick_title(s.get("title"), s.get("title2")))
               for s in ((rec.get("en") or {}).get("story") or [])]
    return {"id": cid, "name_en": en.get("name"), "name_zh": zh.get("name"),
            "path": f"data/story/character/{cid}", "stories": stories}


def _get_json(client: httpx.Client, url: str, tries: int = 4) -> Any:
    last: Exception | None = None
    for attempt in range(tries):
        try:
            resp = client.get(url, headers={"User-Agent": UA}, timeout=25.0)
            resp.raise_for_status()
            return resp.json().get("data")
        except Exception as exc:  # noqa: BLE001 - 网络抓取失败逐次重试
            last = exc
            if attempt < tries - 1:
                time.sleep(0.6 * (attempt + 1))
    log.warning("  ! %s 失败: %s", url, last)
    return None


def _get(loc: str, kind: str, cid: str, client: httpx.Client) -> Any:
    return _get_json(client, API.format(loc=loc, kind=kind, cid=cid))


def _site_ids(client: httpx.Client, loc: str = "en") -> tuple[list[str], list[str]]:
    data = _get_json(client, LIST_API.format(loc=loc))
    raw = list(((data or {}).get("items") or {}).keys())
    bases: list[str] = []
    for sid in raw:
        b = sid.split("-", 1)[0]
        if b not in bases:
            bases.append(b)
    return raw, bases


def _ok(rec: dict[str, Any] | None) -> bool:
    if not rec:
        return False
    return (bool((rec.get("en") or {}).get("avatar")) and bool((rec.get("chs") or {}).get("avatar"))
            and (rec.get("en") or {}).get("story") is not None
            and (rec.get("chs") or {}).get("story") is not None)


def _fetch_all(
    home_: str,
    chars: list[dict[str, Any]],
    mode: str,
    on_progress: Callable[[dict[str, Any]], None] | None,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    cache_path = os.path.join(home_, "data", "_fetch_all.json")
    recs: dict[str, dict[str, Any]] = {}
    if mode != "refresh" and os.path.exists(cache_path):
        recs = {r["id"]: r for r in (_io.load_json_with_backup(cache_path) or [])}
    old_recs = {cid: dict(r) for cid, r in recs.items()}
    old_hashes = {cid: r.get("hash") for cid, r in recs.items()}
    local_ids = [c["id"] for c in chars]

    info: dict[str, Any] = {"mode": mode, "site": None, "discovered": [], "removed": [],
                            "changed": [], "first": [], "unchanged": [], "detail": [],
                            "added_stories": []}

    client = httpx.Client(follow_redirects=True, timeout=30.0)
    try:
        if mode == "update":
            raw, bases = _site_ids(client)
            info["site"] = {"raw": len(raw), "bases": len(bases)}
            info["discovered"] = [i for i in bases if i not in local_ids]
            info["removed"] = [i for i in local_ids if i not in bases]

        ids = local_ids + info["discovered"]
        todo = ids if mode in ("update", "refresh") else [cid for cid in ids if not _ok(recs.get(cid))]

        log.info("待抓取：%d / %d", len(todo), len(ids))
        for n, cid in enumerate(todo, 1):
            rec = recs.get(cid) or {"id": cid}
            for loc in LOCS:
                rec.setdefault(loc, {})
                av = _get(loc, "avatar", cid, client)
                if av is not None:
                    f = av.get("fetter", {}) or {}
                    o = av.get("other", {}) or {}
                    rec[loc]["avatar"] = {
                        "name": av.get("name"), "rank": av.get("rank"), "element": av.get("element"),
                        "weaponType": av.get("weaponType"), "region": av.get("region"),
                        "bodyType": av.get("bodyType"), "specialProp": av.get("specialProp"),
                        "icon": av.get("icon"), "birthday": av.get("birthday"),
                        "release": av.get("release"),
                        "fetter": {"title": f.get("title"), "detail": f.get("detail"),
                                   "constellation": f.get("constellation"), "native": f.get("native"),
                                   "cv": f.get("cv")},
                        "other": {"costume": [{"name": c.get("name"), "description": c.get("description")}
                                              for c in (o.get("costume") or [])],
                                  "nameCard": ({"name": o["nameCard"].get("name"),
                                                "description": o["nameCard"].get("description")}
                                               if o.get("nameCard") else None),
                                  "specialFood": ({"name": o["specialFood"].get("name")}
                                                  if o.get("specialFood") else None)},
                    }
                time.sleep(0.15)
                ft = _get(loc, "avatarFetter", cid, client)
                if ft is not None:
                    st = ft.get("story") or {}
                    rec[loc]["story"] = [{"i": int(k), "title": st[k].get("title"),
                                          "title2": st[k].get("title2"),
                                          "text": st[k].get("text") or "",
                                          "text2": st[k].get("text2") or ""}
                                         for k in sorted(st, key=lambda x: int(x))]
                time.sleep(0.15)
            recs[cid] = rec
            log.info("[%s] 已抓取 en %d 故事 / chs %d 故事", cid,
                     len((rec.get("en") or {}).get("story") or []),
                     len((rec.get("chs") or {}).get("story") or []))
            if on_progress is not None:
                with contextlib.suppress(Exception):
                    on_progress({"event": "progress", "done": n, "total": len(todo), "char_id": cid})
            if n % 20 == 0 or n == len(todo):
                _io.atomic_write_json(cache_path, [recs[i] for i in ids if i in recs])

        for cid in ids:
            entry = recs.get(cid)
            if not entry:
                continue
            h = fingerprint(entry)
            entry["hash"] = h
            if cid in info["discovered"]:
                info["detail"].append(
                    f"{cid}：新增角色（{len((entry.get('en') or {}).get('story') or [])} 个故事）")
                continue
            old = old_hashes.get(cid)
            if not old:
                info["first"].append(cid)
            elif old != h:
                info["changed"].append(cid)
                info["detail"].append("{}：{}".format(
                    cid, "；".join(story_diff(old_recs.get(cid, {}), entry))))
            else:
                info["unchanged"].append(cid)

        _io.atomic_write_json(cache_path, [recs[i] for i in ids if i in recs])
        return recs, info
    finally:
        client.close()


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


def _build(
    home_: str,
    chars: list[dict[str, Any]],
    recs: dict[str, dict[str, Any]],
    only: set[str] | None = None,
    info: dict[str, Any] | None = None,
) -> None:
    info = info if info is not None else {}
    info.setdefault("added_stories", [])
    problems: list[str] = []
    name_diffs: list[str] = []
    empty_chars: list[str] = []
    written = 0
    new_index: list[dict[str, Any]] = []
    root = _io.story_root(home_)

    for c in chars:
        cid = c["id"]
        entry = {"id": cid, "name_en": c.get("name_en"), "name_zh": c.get("name_zh"),
                 "path": c.get("path"), "stories": c.get("stories")}
        new_index.append(entry)
        rec = recs.get(cid)
        if not rec:
            problems.append(f"{cid}: 无数据")
            continue
        stories = list(c.get("stories") or [])
        en_st = (rec.get("en") or {}).get("story") or []
        zh_st = (rec.get("chs") or {}).get("story") or []
        av_en = (rec.get("en") or {}).get("avatar")
        av_zh = (rec.get("chs") or {}).get("avatar")
        en_folders = [slug(pick_title(e.get("title"), e.get("title2"))) for e in en_st]

        if av_en and av_en.get("name") and av_en["name"] != c.get("name_en"):
            name_diffs.append("{} en: 现={!r} 站={!r}".format(cid, c.get("name_en"), av_en["name"]))
        if av_zh and av_zh.get("name") and av_zh["name"] != c.get("name_zh"):
            name_diffs.append("{} zh: 现={!r} 站={!r}".format(cid, c.get("name_zh"), av_zh["name"]))
        if not en_st:
            empty_chars.append(cid)

        added = [f for f in dict.fromkeys(en_folders) if f not in stories]
        if added and (only is None or cid in only):
            stories.extend(added)
            c["stories"] = stories
            entry["stories"] = stories
            info["added_stories"].append("{}：+{}".format(cid, ", ".join(added)))
        if sorted(en_folders) != sorted(stories):
            problems.append(f"{cid}: slug 与 stories 不一致 | slug={en_folders} | stories={stories}")

        if only is not None and cid not in only:
            continue

        used: set[str] = set()
        for idx in range(min(len(en_st), len(zh_st))):
            folder = en_folders[idx]
            if folder not in stories:
                problems.append(f"{cid} story[{idx}]: 无对应文件夹 {folder}")
                continue
            if folder in used:
                problems.append(f"{cid} story[{idx}]: 文件夹重复 {folder}")
            used.add(folder)
            for loc, st in (("en", en_st), ("chs", zh_st)):
                e = st[idx]
                title = pick_title(e.get("title"), e.get("title2"))
                paras = paragraphs((e.get("text") or "") + (e.get("text2") or ""))
                lang = "en" if loc == "en" else "zh"
                title_json = json.dumps(title, ensure_ascii=False)
                content = (f'---\nid: "{cid}"\nstory: {folder}\nlang: {lang}\n'
                           f"title: {title_json}\n---\n\n" + "\n\n".join(paras) + "\n")
                d = os.path.join(root, cid, "profile", "story", folder)
                os.makedirs(d, exist_ok=True)
                with open(os.path.join(d, lang + ".md"), "w", encoding="utf-8") as fh:
                    fh.write(content)
                written += 1

        prof = os.path.join(root, cid, "profile")
        os.makedirs(prof, exist_ok=True)
        if av_en:
            _io.atomic_write_json(os.path.join(prof, "meta.json"), models.CharacterMeta(
                id=cid, rank=_opt_int(av_en.get("rank")), element=_opt_str(av_en.get("element")),
                weapon_type=_opt_str(av_en.get("weaponType")), region=_opt_str(av_en.get("region")),
                body_type=_opt_str(av_en.get("bodyType")),
                special_prop=_opt_str(av_en.get("specialProp")), icon=_opt_str(av_en.get("icon")),
                birthday=_opt_int_list(av_en.get("birthday")), release=_opt_int(av_en.get("release"))))
        if av_en and av_zh:
            fe, fz = av_en.get("fetter", {}) or {}, av_zh.get("fetter", {}) or {}
            ce, cz = av_en.get("other", {}) or {}, av_zh.get("other", {}) or {}

            def pair_list(a: Any, b: Any) -> list[models.Costume]:
                a, b = a or [], b or []
                out: list[models.Costume] = []
                for i in range(max(len(a), len(b))):
                    x = a[i] if i < len(a) else {}
                    y = b[i] if i < len(b) else {}
                    out.append(models.Costume(name_en=_s(x.get("name")), name_zh=_s(y.get("name")),
                                              description_en=_s(x.get("description")),
                                              description_zh=_s(y.get("description"))))
                return out

            def pair_obj(a: Any, b: Any) -> dict[str, str]:
                a, b = a or {}, b or {}
                return {"name_en": _s(a.get("name")), "name_zh": _s(b.get("name")),
                        "description_en": _s(a.get("description")),
                        "description_zh": _s(b.get("description"))}

            name_card = pair_obj(ce.get("nameCard"), cz.get("nameCard")) \
                if (ce.get("nameCard") or cz.get("nameCard")) else None
            special_food = pair_obj(ce.get("specialFood"), cz.get("specialFood")) \
                if (ce.get("specialFood") or cz.get("specialFood")) else None
            _io.atomic_write_json(os.path.join(prof, "text.json"), models.CharacterText(
                title_en=_s(fe.get("title")), title_zh=_s(fz.get("title")),
                detail_en=_s(fe.get("detail")), detail_zh=_s(fz.get("detail")),
                constellation_en=_s(fe.get("constellation")), constellation_zh=_s(fz.get("constellation")),
                native_en=_s(fe.get("native")), native_zh=_s(fz.get("native")),
                cv=_str_map(fe.get("cv")), costume=pair_list(ce.get("costume"), cz.get("costume")),
                name_card=models.NameCard(name_en=name_card["name_en"], name_zh=name_card["name_zh"],
                                          description_en=name_card["description_en"],
                                          description_zh=name_card["description_zh"]) if name_card else None,
                special_food=models.SpecialFood(name_en=special_food["name_en"],
                                                name_zh=special_food["name_zh"]) if special_food else None))

    _io.atomic_write_json(_io.char_index_path(home_),
                          [models.CharacterRef(**e).model_dump(mode="json") for e in new_index])
    log.info("角色数: %d  写入故事文件: %d  无故事角色: %s", len(new_index), written, empty_chars)
    for p in problems:
        log.warning("  - %s", p)
    for d in name_diffs:
        log.warning("  * %s", d)


def run(data_dir: str | None = None, mode: str = "incremental",
        on_progress: Callable[[dict[str, Any]], None] | None = None) -> ExitCode:
    """数据抓取入口，返回 `ExitCode`。

    mode：`incremental`（只补缺失）/ `update`（发现新角色 + 检测变化，只重写变化项）/
    `refresh`（忽略缓存全量重抓）。
    """
    home_ = _io.home(data_dir)
    char_path = _io.char_index_path(home_)
    if os.path.exists(char_path):
        chars = _io.load_json_with_backup(char_path) or []
    else:
        log.warning("未找到 data/character.json，将按站点角色列表初始化（等价 update）。")
        chars = []
        mode = "update"

    recs, info = _fetch_all(home_, chars, mode=mode, on_progress=on_progress)

    known = {c["id"] for c in chars}
    for cid in info["discovered"]:
        if cid in recs and cid not in known:
            chars.append(make_char_entry(cid, recs[cid]))

    only: set[str] | None = None
    if mode == "update":
        only = set(info["discovered"]) | set(info["changed"]) | set(info["first"])
    _build(home_, chars, recs, only=only, info=info)

    if mode == "update":
        site = info.get("site") or {}
        if site:
            log.info("站点角色列表：%d 条 → 归一 %d 个", site["raw"], site["bases"])
        log.info("更新报告：新增角色 %d | 站点已移除 %d | 内容变化 %d | 首次建指纹 %d | 未变化 %d",
                 len(info["discovered"]), len(info["removed"]), len(info["changed"]),
                 len(info["first"]), len(info["unchanged"]))
        for d in info["detail"]:
            log.info("  * %s", d)
    return ExitCode.OK
