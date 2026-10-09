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
import argparse
import json
import os
import re
import time

import requests
from tenacity import (Retrying, retry_if_exception_type, stop_after_attempt,
                      wait_incrementing)
from tqdm import tqdm

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = os.path.join(ROOT, "data")
STORY_ROOT = os.path.join(BASE, "story", "character")
CACHE = os.path.join(BASE, "_fetch_all.json")
API = "https://gi.yatta.moe/api/v2/{loc}/{kind}/{cid}"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"


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


def get(loc, kind, cid, tries=4):
    """请求接口；失败按 tenacity 线性退避重试，最终失败返回 None。"""
    try:
        for attempt in Retrying(stop=stop_after_attempt(tries),
                                wait=wait_incrementing(start=0.6, increment=0.6),
                                retry=retry_if_exception_type(Exception),
                                reraise=True):
            with attempt:
                r = requests.get(API.format(loc=loc, kind=kind, cid=cid),
                                 headers={"User-Agent": UA}, timeout=25)
                r.raise_for_status()
                return r.json().get("data")
    except Exception as e:  # noqa: BLE001
        print("  ! %s/%s/%s 失败: %s" % (loc, kind, cid, e))
        return None


def fetch_all(chars, refresh=False):
    recs = {}
    if not refresh and os.path.exists(CACHE):
        with open(CACHE, encoding="utf-8") as f:
            recs = {r["id"]: r for r in json.load(f)}
    ids = [c["id"] for c in chars]

    def ok(r):
        return (r and r.get("en", {}).get("avatar") and r.get("chs", {}).get("avatar")
                and r["en"].get("story") is not None and r["chs"].get("story") is not None)

    todo = ids if refresh else [cid for cid in ids if not ok(recs.get(cid))]
    print("待抓取：%d / %d" % (len(todo), len(ids)))
    for n, cid in enumerate(tqdm(todo, desc="抓取", unit="角色", dynamic_ncols=True), 1):
        rec = recs.get(cid) or {"id": cid}
        for loc in ("en", "chs"):
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
    dump(CACHE, [recs[i] for i in ids if i in recs])
    return recs


def build(chars, recs):
    problems, name_diffs, written, empty_chars, new_index = [], [], 0, [], []
    for c in tqdm(chars, desc="生成", unit="角色", dynamic_ncols=True):
        cid = c["id"]
        rec = recs.get(cid)
        if not rec:
            problems.append("%s: 无数据" % cid)
            continue
        stories = c["stories"]
        en_st = (rec.get("en") or {}).get("story") or []
        zh_st = (rec.get("chs") or {}).get("story") or []
        av_en = (rec.get("en") or {}).get("avatar")
        av_zh = (rec.get("chs") or {}).get("avatar")

        if av_en and av_en.get("name") and av_en["name"] != c.get("name_en"):
            name_diffs.append("%s en: 现=%r 站=%r" % (cid, c.get("name_en"), av_en["name"]))
        if av_zh and av_zh.get("name") and av_zh["name"] != c.get("name_zh"):
            name_diffs.append("%s zh: 现=%r 站=%r" % (cid, c.get("name_zh"), av_zh["name"]))

        en_folders = [slug(pick_title(e.get("title"), e.get("title2"))) for e in en_st]
        if sorted(en_folders) != sorted(stories):
            problems.append("%s: slug 与 stories 不一致 | slug=%s | stories=%s" % (cid, en_folders, stories))
        if not en_st:
            empty_chars.append(cid)

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

        new_index.append({"id": cid, "name_en": c.get("name_en"), "name_zh": c.get("name_zh"),
                          "path": c.get("path"), "stories": stories})

    dump(os.path.join(BASE, "character.json"), new_index)
    print("角色数: %d  写入故事文件: %d  无故事角色: %s" % (len(new_index), written, empty_chars))
    print("问题 %d 条：" % len(problems))
    for p in problems:
        print("  -", p)
    print("名字差异 %d 条：" % len(name_diffs))
    for d in name_diffs:
        print("  *", d)


def main():
    p = argparse.ArgumentParser(description="从 Project Amber 拉取并生成/校正全部角色数据")
    p.add_argument("--refresh", action="store_true",
                   help="忽略缓存 data/_fetch_all.json，强制重新抓取全部角色")
    args = p.parse_args()
    with open(os.path.join(BASE, "character.json"), encoding="utf-8") as f:
        chars = json.load(f)
    recs = fetch_all(chars, refresh=args.refresh)
    build(chars, recs)


if __name__ == "__main__":
    main()
