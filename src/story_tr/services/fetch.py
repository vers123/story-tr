"""从 Project Amber(yatta.moe) 拉取角色数据并生成 / 校正 `data/` 文件。

- 抓取：每个角色 en/chs 的 avatar 与 avatarFetter（并发，保留请求间隔）
- 产出：`data/character.json`、`profile/{meta,text}.json`、故事 `en.md`/`zh.md`
- 缓存：`data/_fetch_all.json`（带 hash 指纹，可重复运行只补缺失；`--refresh` 强制重抓）
"""

from __future__ import annotations

import contextlib
import copy
import logging
import os
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import httpx

from story_tr.errors import ExitCode

from . import fetch_write, storage
from .fetch_pure import (  # noqa: F401 - 对外沿用 fetch.<helper> 的公开名字
    LOCS,
    fingerprint,
    make_char_entry,
    paragraphs,
    pick_title,
    slug,
    story_diff,
)

log = logging.getLogger("story_tr.services.fetch")

API = "https://gi.yatta.moe/api/v2/{loc}/{kind}/{cid}"
LIST_API = "https://gi.yatta.moe/api/v2/{loc}/avatar"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
REQUEST_DELAY = 0.15


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
    raw: list[str] = list(((data or {}).get("items") or {}).keys())
    bases: list[str] = []
    for sid in raw:
        b = sid.split("-", 1)[0]
        if b not in bases:
            bases.append(b)
    return raw, bases


def _ok(rec: dict[str, Any] | None) -> bool:
    if not rec:
        return False
    return (
        bool((rec.get("en") or {}).get("avatar"))
        and bool((rec.get("chs") or {}).get("avatar"))
        and (rec.get("en") or {}).get("story") is not None
        and (rec.get("chs") or {}).get("story") is not None
    )


def _avatar_block(av: dict[str, Any]) -> dict[str, Any]:
    """把站点 avatar 响应收敛成写入缓存的最小集合。"""
    fetter = av.get("fetter", {}) or {}
    other = av.get("other", {}) or {}
    name_card = other.get("nameCard")
    special_food = other.get("specialFood")
    return {
        "name": av.get("name"),
        "rank": av.get("rank"),
        "element": av.get("element"),
        "weaponType": av.get("weaponType"),
        "region": av.get("region"),
        "bodyType": av.get("bodyType"),
        "specialProp": av.get("specialProp"),
        "icon": av.get("icon"),
        "birthday": av.get("birthday"),
        "release": av.get("release"),
        "fetter": {
            "title": fetter.get("title"),
            "detail": fetter.get("detail"),
            "constellation": fetter.get("constellation"),
            "native": fetter.get("native"),
            "cv": fetter.get("cv"),
        },
        "other": {
            "costume": [
                {"name": c.get("name"), "description": c.get("description")}
                for c in (other.get("costume") or [])
            ],
            "nameCard": (
                {"name": name_card.get("name"), "description": name_card.get("description")}
                if name_card
                else None
            ),
            "specialFood": {"name": special_food.get("name")} if special_food else None,
        },
    }


def _story_block(ft: dict[str, Any]) -> list[dict[str, Any]]:
    """把 avatarFetter 的 story 映射转为有序列表。"""
    st = ft.get("story") or {}
    return [
        {
            "i": int(k),
            "title": st[k].get("title"),
            "title2": st[k].get("title2"),
            "text": st[k].get("text") or "",
            "text2": st[k].get("text2") or "",
        }
        for k in sorted(st, key=lambda x: int(x))
    ]


def _fetch_one(cid: str, client: httpx.Client, previous: dict[str, Any] | None = None) -> dict[str, Any]:
    """抓取单个角色的 en / chs 资料与故事；失败时保留缓存中的上一版。"""
    rec: dict[str, Any] = dict(previous) if previous else {}
    rec["id"] = cid
    for loc in LOCS:
        rec.setdefault(loc, {})
        av = _get(loc, "avatar", cid, client)
        if av is not None:
            rec[loc]["avatar"] = _avatar_block(av)
        time.sleep(REQUEST_DELAY)
        ft = _get(loc, "avatarFetter", cid, client)
        if ft is not None:
            rec[loc]["story"] = _story_block(ft)
        time.sleep(REQUEST_DELAY)
    return rec


def _todo_ids(mode: str, ids: list[str], recs: dict[str, dict[str, Any]]) -> list[str]:
    if mode in ("update", "refresh"):
        return ids
    return [cid for cid in ids if not _ok(recs.get(cid))]


def _fetch_many(
    todo: list[str],
    recs: dict[str, dict[str, Any]],
    client: httpx.Client,
    workers: int,
    on_commit: Callable[[dict[str, Any], int], None],
) -> None:
    """按并发度抓取；`on_commit` 按完成顺序回调。"""
    if workers > 1 and len(todo) > 1:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_fetch_one, cid, client, recs.get(cid)): cid for cid in todo}
            for n, future in enumerate(as_completed(futures), 1):
                on_commit(future.result(), n)
        return
    for n, cid in enumerate(todo, 1):
        on_commit(_fetch_one(cid, client, recs.get(cid)), n)


def _classify(
    ids: list[str],
    recs: dict[str, dict[str, Any]],
    old_recs: dict[str, dict[str, Any]],
    old_hashes: dict[str, Any],
    info: dict[str, Any],
) -> None:
    """按内容指纹归类：新增 / 首次建指纹 / 变化 / 未变化。"""
    for cid in ids:
        entry = recs.get(cid)
        if not entry:
            continue
        h = fingerprint(entry)
        entry["hash"] = h
        if cid in info["discovered"]:
            info["detail"].append(
                f"{cid}：新增角色（{len((entry.get('en') or {}).get('story') or [])} 个故事）"
            )
            continue
        old = old_hashes.get(cid)
        if not old:
            info["first"].append(cid)
        elif old != h:
            info["changed"].append(cid)
            info["detail"].append("{}：{}".format(cid, "；".join(story_diff(old_recs.get(cid, {}), entry))))
        else:
            info["unchanged"].append(cid)


def _emit(handler: Callable[[dict[str, Any]], None] | None, payload: dict[str, Any]) -> None:
    if handler is None:
        return
    with contextlib.suppress(Exception):  # 进度回调异常不影响抓取
        handler(payload)


def _cache_write(cache_path: str, ids: list[str], recs: dict[str, dict[str, Any]]) -> None:
    storage.atomic_write_json(cache_path, [recs[i] for i in ids if i in recs], skip_if_unchanged=False)


def _discover(client: httpx.Client, mode: str, local_ids: list[str], info: dict[str, Any]) -> list[str]:
    """`update` 模式下发现新角色 / 站点已移除角色，返回全部待处理 id。"""
    if mode != "update":
        return local_ids
    raw, bases = _site_ids(client)
    info["site"] = {"raw": len(raw), "bases": len(bases)}
    discovered = [i for i in bases if i not in local_ids]
    info["discovered"] = discovered
    info["removed"] = [i for i in local_ids if i not in bases]
    return local_ids + discovered


def _collect(
    home_: str,
    chars: list[dict[str, Any]],
    mode: str,
    on_progress: Callable[[dict[str, Any]], None] | None,
    workers: int = 1,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """执行抓取与分类，返回 `(records, info)`。"""
    cache_path = os.path.join(home_, "data", "_fetch_all.json")
    recs: dict[str, dict[str, Any]] = {}
    if mode != "refresh" and os.path.exists(cache_path):
        recs = {r["id"]: r for r in (storage.load_json_with_backup(cache_path) or [])}
    # 深拷贝：抓取会原地改写嵌套 avatar / story，浅拷贝会让「旧版本」跟随变化而无法 diff。
    old_recs = copy.deepcopy(recs)
    old_hashes = {cid: r.get("hash") for cid, r in recs.items()}
    local_ids = [c["id"] for c in chars]
    info: dict[str, Any] = {
        "mode": mode,
        "site": None,
        "discovered": [],
        "removed": [],
        "changed": [],
        "first": [],
        "unchanged": [],
        "detail": [],
        "added_stories": [],
    }

    client = httpx.Client(follow_redirects=True, timeout=30.0)
    try:
        ids = _discover(client, mode, local_ids, info)
        todo = _todo_ids(mode, ids, recs)
        log.info("待抓取：%d / %d（并发 %d）", len(todo), len(ids), max(1, int(workers)))

        def _commit(rec: dict[str, Any], n: int) -> None:
            recs[rec["id"]] = rec
            log.info(
                "[%s] 已抓取 en %d 故事 / chs %d 故事",
                rec["id"],
                len((rec.get("en") or {}).get("story") or []),
                len((rec.get("chs") or {}).get("story") or []),
            )
            _emit(
                on_progress,
                {"event": "progress", "done": n, "total": len(todo), "char_id": rec["id"]},
            )
            if n % 20 == 0 or n == len(todo):
                _cache_write(cache_path, ids, recs)

        _fetch_many(todo, recs, client, max(1, int(workers)), _commit)
        _classify(ids, recs, old_recs, old_hashes, info)
        _cache_write(cache_path, ids, recs)
        return recs, info
    finally:
        client.close()


def run(
    data_dir: str | None = None,
    mode: str = "incremental",
    on_progress: Callable[[dict[str, Any]], None] | None = None,
    workers: int = 4,
) -> ExitCode:
    """数据抓取入口，返回 `ExitCode`。

    mode：`incremental`（只补缺失）/ `update`（发现新角色 + 检测变化，只重写变化项）/
    `refresh`（忽略缓存全量重抓）；`workers` 控制并发抓取角色数。
    """
    home_ = storage.home(data_dir)
    char_path = storage.char_index_path(home_)
    if os.path.exists(char_path):
        chars = storage.load_json_with_backup(char_path) or []
    else:
        log.warning("未找到 data/character.json，将按站点角色列表初始化（等价 update）。")
        chars = []
        mode = "update"

    recs, info = _collect(home_, chars, mode=mode, on_progress=on_progress, workers=workers)

    known = {c["id"] for c in chars}
    for cid in info["discovered"]:
        if cid in recs and cid not in known:
            chars.append(make_char_entry(cid, recs[cid]))

    only: set[str] | None = None
    if mode == "update":
        only = set(info["discovered"]) | set(info["changed"]) | set(info["first"])
    fetch_write.write_dataset(home_, chars, recs, only=only, info=info)

    if mode == "update":
        site = info.get("site") or {}
        if site:
            log.info("站点角色列表：%d 条 → 归一 %d 个", site["raw"], site["bases"])
        log.info(
            "更新报告：新增角色 %d | 站点已移除 %d | 内容变化 %d | 首次建指纹 %d | 未变化 %d",
            len(info["discovered"]),
            len(info["removed"]),
            len(info["changed"]),
            len(info["first"]),
            len(info["unchanged"]),
        )
        for d in info["detail"]:
            log.info("  * %s", d)
    return ExitCode.OK
