# -*- coding: utf-8 -*-
"""从 Project Amber(yatta.moe) 拉取全部角色数据并生成/校正仓库文件。

- 抓取：每个角色 en/chs 的 avatar 与 avatarFetter
- 产出：
    {id}/profile/meta.json           语言无关属性
    {id}/profile/text.json           角色文本（_en/_zh 叶子级）
    {id}/profile/story/{story}/en.md, zh.md   各故事正文
    data/character.json              角色索引（保留既有 name_*/path/stories）
- 原始抓取缓存：data/_fetch_all.json（可重复运行，只补缺失项；--refresh 强制重抓）
"""
import hashlib
import json
import os
import re
import time

import requests
from tenacity import Retrying, retry_if_exception_type, stop_after_attempt, wait_incrementing
from tqdm import tqdm

from . import paths

ROOT = paths.home()
BASE = os.path.join(ROOT, "data")
STORY_ROOT = os.path.join(BASE, "story", "character")
CHAR_FILE = os.path.join(BASE, "character.json")
CACHE = os.path.join(BASE, "_fetch_all.json")
API = "https://gi.yatta.moe/api/v2/{loc}/{kind}/{cid}"
LIST_API = "https://gi.yatta.moe/api/v2/{loc}/avatar"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
LOCS = ("en", "chs")


def slug(s):
    s = (s or "").lower()
    s = s.replace("'s", "").replace("'", "")
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return s.strip("_")


def pick_title(t1, t2):
    """主标题可用就用主标题；主标题是占位/空（如 ???/？？？）时才用 title2。"""
    t1 = (t1 or "").strip()
    if t1 and re.search(r"[A-Za-z0-9\u4e00-\u9fff]", t1) and set(t1) - set("?？. "):
        return t1
    return (t2 or t1).strip()


def paragraphs(raw):
    """站点用字面量 \\n 作换行（单换行即一句/一段），据此拆分。"""
    if not raw:
        return []
    return [p.strip() for p in raw.replace("\\n", "\n").split("\n") if p.strip()]


def dump(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")


def get_url(url, label, tries=4):
    """请求任意接口；失败按 tenacity 线性退避重试，最终失败返回 None。"""
    try:
        for attempt in Retrying(stop=stop_after_attempt(tries),
                                wait=wait_incrementing(start=0.6, increment=0.6),
                                retry=retry_if_exception_type(Exception),
                                reraise=True):
            with attempt:
                r = requests.get(url, headers={"User-Agent": UA}, timeout=25)
                r.raise_for_status()
                return r.json().get("data")
    except Exception as e:  # noqa: BLE001
        print("  ! %s 失败: %s" % (label, e))
        return None


def get(loc, kind, cid, tries=4):
    return get_url(API.format(loc=loc, kind=kind, cid=cid), "%s/%s/%s" % (loc, kind, cid), tries)


def base_id(sid):
    """站点 id 归一化：`10000005-anemo` → `10000005`（旅行者的分元素条目归到同一角色）。"""
    return sid.split("-", 1)[0]


def site_ids(loc="en"):
    """拉取站点角色列表，返回 (原始 id 列表, 归一化后的 base id 列表)。"""
    data = get_url(LIST_API.format(loc=loc), "站点角色列表")
    raw = list(((data or {}).get("items") or {}).keys())
    bases = []
    for sid in raw:
        b = base_id(sid)
        if b not in bases:
            bases.append(b)
    return raw, bases


def fingerprint(rec):
    """内容指纹：对「会写入文件」的 avatar + story（en/chs）取 sha1，用于检测变化。"""
    payload = {loc: {"avatar": (rec.get(loc) or {}).get("avatar"),
                     "story": (rec.get(loc) or {}).get("story")} for loc in LOCS}
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def story_diff(old, new):
    """比较两条记录，返回变化描述（供 --update 报告）。"""
    out = []
    for loc in LOCS:
        a = {s["i"]: s for s in ((old.get(loc) or {}).get("story") or [])}
        b = {s["i"]: s for s in ((new.get(loc) or {}).get("story") or [])}
        if set(a) != set(b):
            out.append("%s 故事数 %d→%d" % (loc, len(a), len(b)))
        for i in sorted(set(a) & set(b)):
            if (a[i].get("text"), a[i].get("text2")) != (b[i].get("text"), b[i].get("text2")):
                out.append("%s 故事 %d 文本变化" % (loc, i))
        if ((old.get(loc) or {}).get("avatar") or {}) != ((new.get(loc) or {}).get("avatar") or {}):
            out.append("%s 角色资料变化" % loc)
    seen, uniq = set(), []
    for x in out:
        if x not in seen:
            seen.add(x)
            uniq.append(x)
    return uniq


def make_char_entry(cid, rec):
    """由抓取结果为新角色派生 character.json 条目（stories 用「英文标题 → slug」规则）。"""
    en = (rec.get("en") or {}).get("avatar") or {}
    zh = (rec.get("chs") or {}).get("avatar") or {}
    stories = [slug(pick_title(s.get("title"), s.get("title2")))
               for s in ((rec.get("en") or {}).get("story") or [])]
    return {"id": cid, "name_en": en.get("name"), "name_zh": zh.get("name"),
            "path": "data/story/character/%s" % cid, "stories": stories}


def fetch_all(chars, mode="incremental"):
    """抓取角色数据，返回 (recs, info)。

    mode：
        incremental —— 只补缺失项（读缓存，不联网拉列表）
        update      —— 拉站点列表发现新角色 + 全量重抓以检测内容变化
        refresh     —— 忽略缓存，全量重抓
    """
    recs = {}
    if mode != "refresh" and os.path.exists(CACHE):
        with open(CACHE, encoding="utf-8") as f:
            recs = {r["id"]: r for r in json.load(f)}
    old_recs = json.loads(json.dumps(recs)) if recs else {}
    old_hashes = {cid: r.get("hash") for cid, r in recs.items()}
    local_ids = [c["id"] for c in chars]

    info = {"mode": mode, "site": None, "discovered": [], "removed": [],
            "changed": [], "first": [], "unchanged": [], "detail": [], "added_stories": []}

    if mode == "update":
        raw, bases = site_ids()
        info["site"] = {"raw": len(raw), "bases": len(bases)}
        info["discovered"] = [i for i in bases if i not in local_ids]
        info["removed"] = [i for i in local_ids if i not in bases]

    ids = local_ids + info["discovered"]

    def ok(r):
        return (r and r.get("en", {}).get("avatar") and r.get("chs", {}).get("avatar")
                and r["en"].get("story") is not None and r["chs"].get("story") is not None)

    if mode in ("update", "refresh"):
        todo = ids
    else:
        todo = [cid for cid in ids if not ok(recs.get(cid))]
    print("待抓取：%d / %d" % (len(todo), len(ids)))
    for n, cid in enumerate(tqdm(todo, desc="抓取", unit="角色", dynamic_ncols=True), 1):
        rec = recs.get(cid) or {"id": cid}
        for loc in LOCS:
            rec.setdefault(loc, {})
            av = get(loc, "avatar", cid)
            if av is not None:
                f = av.get("fetter", {}) or {}
                o = av.get("other", {}) or {}
                rec[loc]["avatar"] = {
                    "name": av.get("name"), "rank": av.get("rank"), "element": av.get("element"),
                    "weaponType": av.get("weaponType"), "region": av.get("region"),
                    "bodyType": av.get("bodyType"), "specialProp": av.get("specialProp"),
                    "icon": av.get("icon"), "birthday": av.get("birthday"), "release": av.get("release"),
                    "fetter": {"title": f.get("title"), "detail": f.get("detail"),
                               "constellation": f.get("constellation"), "native": f.get("native"),
                               "cv": f.get("cv")},
                    "other": {"costume": [{"name": c.get("name"), "description": c.get("description")}
                                          for c in (o.get("costume") or [])],
                              "nameCard": ({"name": o["nameCard"].get("name"),
                                            "description": o["nameCard"].get("description")}
                                           if o.get("nameCard") else None),
                              "specialFood": ({"name": o["specialFood"].get("name")}
                                              if o.get("specialFood") else None)}}
            time.sleep(0.15)
            ft = get(loc, "avatarFetter", cid)
            if ft is not None:
                st = ft.get("story") or {}
                rec[loc]["story"] = [{"i": int(k), "title": st[k].get("title"),
                                      "title2": st[k].get("title2"),
                                      "text": st[k].get("text") or "",
                                      "text2": st[k].get("text2") or ""}
                                     for k in sorted(st, key=lambda x: int(x))]
            time.sleep(0.15)
        recs[cid] = rec
        if n % 20 == 0 or n == len(todo):
            dump(CACHE, [recs[i] for i in ids if i in recs])

    # 内容指纹与变化判定（必须在写缓存之前算好并存进去）
    for cid in ids:
        rec = recs.get(cid)
        if not rec:
            continue
        h = fingerprint(rec)
        rec["hash"] = h
        if cid in info["discovered"]:
            info["detail"].append("%s：新增角色（%d 个故事）"
                                  % (cid, len((rec.get("en") or {}).get("story") or [])))
            continue
        old = old_hashes.get(cid)
        if not old:
            info["first"].append(cid)
        elif old != h:
            info["changed"].append(cid)
            info["detail"].append("%s：%s" % (cid, "；".join(story_diff(old_recs.get(cid, {}), rec))))
        else:
            info["unchanged"].append(cid)

    dump(CACHE, [recs[i] for i in ids if i in recs])
    return recs, info


def build(chars, recs, only=None, report=None):
    """生成各角色文件并重写 data/character.json。

    only：只重写这些角色的文件（None = 全部）；character.json 索引始终完整重写。
    report：--update 的报告容器（记录自动追加的故事等）。
    """
    report = report if report is not None else {}
    report.setdefault("added_stories", [])
    problems, name_diffs, written, empty_chars, new_index = [], [], 0, [], []
    for c in tqdm(chars, desc="生成", unit="角色", dynamic_ncols=True):
        cid = c["id"]
        entry = {"id": cid, "name_en": c.get("name_en"), "name_zh": c.get("name_zh"),
                 "path": c.get("path"), "stories": c.get("stories")}
        new_index.append(entry)
        rec = recs.get(cid)
        if not rec:
            problems.append("%s: 无数据" % cid)
            continue
        stories = list(c.get("stories") or [])
        en_st = (rec.get("en") or {}).get("story") or []
        zh_st = (rec.get("chs") or {}).get("story") or []
        av_en = (rec.get("en") or {}).get("avatar")
        av_zh = (rec.get("chs") or {}).get("avatar")
        en_folders = [slug(pick_title(e.get("title"), e.get("title2"))) for e in en_st]

        # 校验信息对所有角色统计（与是否重写文件无关）
        if av_en and av_en.get("name") and av_en["name"] != c.get("name_en"):
            name_diffs.append("%s en: 现=%r 站=%r" % (cid, c.get("name_en"), av_en["name"]))
        if av_zh and av_zh.get("name") and av_zh["name"] != c.get("name_zh"):
            name_diffs.append("%s zh: 现=%r 站=%r" % (cid, c.get("name_zh"), av_zh["name"]))
        if not en_st:
            empty_chars.append(cid)

        added = [f for f in dict.fromkeys(en_folders) if f not in stories]
        if added and (only is None or cid in only):
            stories.extend(added)
            c["stories"] = stories
            entry["stories"] = stories
            report["added_stories"].append("%s：+%s" % (cid, ", ".join(added)))
        if sorted(en_folders) != sorted(stories):
            problems.append("%s: slug 与 stories 不一致 | slug=%s | stories=%s" % (cid, en_folders, stories))

        if only is not None and cid not in only:
            continue  # 未变化：只保留索引，不重写文件

        used = set()
        for idx in range(min(len(en_st), len(zh_st))):
            folder = en_folders[idx]
            if folder not in stories:
                problems.append("%s story[%d]: 无对应文件夹 %s" % (cid, idx, folder))
                continue
            if folder in used:
                problems.append("%s story[%d]: 文件夹重复 %s" % (cid, idx, folder))
            used.add(folder)
            for loc, st in (("en", en_st), ("chs", zh_st)):
                e = st[idx]
                title = pick_title(e.get("title"), e.get("title2"))
                paras = paragraphs((e.get("text") or "") + (e.get("text2") or ""))
                lang = "en" if loc == "en" else "zh"
                content = ('---\nid: "%s"\nstory: %s\nlang: %s\ntitle: %s\n---\n\n%s\n'
                           % (cid, folder, lang, json.dumps(title, ensure_ascii=False),
                              "\n\n".join(paras)))
                d = os.path.join(STORY_ROOT, cid, "profile", "story", folder)
                os.makedirs(d, exist_ok=True)
                with open(os.path.join(d, lang + ".md"), "w", encoding="utf-8") as fh:
                    fh.write(content)
                written += 1

        prof = os.path.join(STORY_ROOT, cid, "profile")
        os.makedirs(prof, exist_ok=True)
        if av_en:
            dump(os.path.join(prof, "meta.json"), {
                "id": cid, "rank": av_en.get("rank"), "element": av_en.get("element"),
                "weaponType": av_en.get("weaponType"), "region": av_en.get("region"),
                "bodyType": av_en.get("bodyType"), "specialProp": av_en.get("specialProp"),
                "icon": av_en.get("icon"), "birthday": av_en.get("birthday"),
                "release": av_en.get("release")})
        if av_en and av_zh:
            fe, fz = av_en.get("fetter", {}), av_zh.get("fetter", {})
            ce, cz = av_en.get("other", {}), av_zh.get("other", {})

            def pair_list(a, b):
                a, b = a or [], b or []
                out = []
                for i in range(max(len(a), len(b))):
                    x = a[i] if i < len(a) else {}
                    y = b[i] if i < len(b) else {}
                    out.append({"name_en": x.get("name"), "name_zh": y.get("name"),
                                "description_en": x.get("description"),
                                "description_zh": y.get("description")})
                return out

            def pair_obj(a, b):
                if not a and not b:
                    return None
                a, b = a or {}, b or {}
                o = {"name_en": a.get("name"), "name_zh": b.get("name")}
                if "description" in a or "description" in b:
                    o["description_en"] = a.get("description")
                    o["description_zh"] = b.get("description")
                return o

            dump(os.path.join(prof, "text.json"), {
                "title_en": fe.get("title"), "title_zh": fz.get("title"),
                "detail_en": fe.get("detail"), "detail_zh": fz.get("detail"),
                "constellation_en": fe.get("constellation"), "constellation_zh": fz.get("constellation"),
                "native_en": fe.get("native"), "native_zh": fz.get("native"),
                "cv": fe.get("cv"),
                "costume": pair_list(ce.get("costume"), cz.get("costume")),
                "nameCard": pair_obj(ce.get("nameCard"), cz.get("nameCard")),
                "specialFood": pair_obj(ce.get("specialFood"), cz.get("specialFood"))})

    dump(os.path.join(BASE, "character.json"), new_index)
    print("角色数: %d  写入故事文件: %d  无故事角色: %s" % (len(new_index), written, empty_chars))
    print("问题 %d 条：" % len(problems))
    for p in problems:
        print("  -", p)
    print("名字差异 %d 条：" % len(name_diffs))
    for d in name_diffs:
        print("  *", d)


def add_parser(sub):
    """注册 `story-tr fetch` 子命令。"""
    p = sub.add_parser("fetch", help="从 Project Amber 拉取并生成/校正角色数据",
                       parents=[paths.data_dir_parent()])
    g = p.add_mutually_exclusive_group()
    g.add_argument("--update", action="store_true",
                   help="联网更新：从站点列表发现新角色 + 重抓检测内容变化，"
                        "只重写变化项并更新 character.json")
    g.add_argument("--refresh", action="store_true",
                   help="忽略缓存 data/_fetch_all.json，强制重新抓取并重写全部角色")
    p.set_defaults(func=main)
    return p


def print_report(info):
    """打印 --update 的变更报告。"""
    site = info.get("site") or {}
    if site:
        print("站点角色列表：%d 条 → 归一 %d 个（忽略旅行者分元素条目 %d 条）"
              % (site["raw"], site["bases"], site["raw"] - site["bases"]))
    print("更新报告：新增角色 %d | 站点已移除 %d（本地保留）| 内容变化 %d | 首次建指纹 %d | 未变化 %d"
          % (len(info["discovered"]), len(info["removed"]), len(info["changed"]),
             len(info["first"]), len(info["unchanged"])))
    if info["discovered"]:
        print("  新角色：%s" % ", ".join(info["discovered"]))
    if info["removed"]:
        print("  站点已无此 id（本地未删除）：%s" % ", ".join(info["removed"]))
    for d in info["detail"]:
        print("  * " + d)
    if info["added_stories"]:
        print("  character.json 新增故事条目：")
        for s in info["added_stories"]:
            print("    + " + s)


def main(args) -> int:
    mode = "refresh" if args.refresh else ("update" if args.update else "incremental")
    if os.path.exists(CHAR_FILE):
        with open(CHAR_FILE, encoding="utf-8") as f:
            chars = json.load(f)
    else:
        print("未找到 data/character.json，将按站点角色列表初始化（等价 --update）。")
        chars = []
        mode = "update"

    recs, info = fetch_all(chars, mode=mode)

    known = {c["id"] for c in chars}
    for cid in info["discovered"]:
        if cid in recs and cid not in known:
            chars.append(make_char_entry(cid, recs[cid]))

    only = None  # --update 只重写「新增 / 变化 / 首次建指纹」的角色，其余仅保留索引
    if mode == "update":
        only = set(info["discovered"]) | set(info["changed"]) | set(info["first"])
    build(chars, recs, only=only, report=info)

    if mode == "update":
        print()
        print_report(info)
    return 0

