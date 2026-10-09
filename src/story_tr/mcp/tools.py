# -*- coding: utf-8 -*-
"""MCP Tool 函数（纯 Python，**不依赖 mcp**）。

- 只读：`list_characters` / `list_stories` / `get_result_summary` / `read_result_item`
- 同步（含联网）：`chain`（单条文本翻译链）、`clean`（默认 `dry_run` 只预览）
- 长任务：`translate_argv` / `fetch_argv`（生成命令行，交给 `jobs` 以子进程执行）

`server.py` 把这里的函数注册成 MCP Tool；单独导入本模块不会引入 mcp 依赖，
因此这些函数可在全部受支持的 Python 版本下测试。所有路径都取自 `story_tr.stories`
的模块级常量（`STORY_ROOT` / `CHAR_FILE`），便于测试时 monkeypatch。

注意：本模块内**绝不向 stdout 打印**（MCP stdio 的 stdout 是协议通道）。
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import types

from .. import chain as tc
from .. import paths, stories

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


# --------------------------------------------------------------------------- #
# 角色 id 解析（不打印：见模块 docstring）
# --------------------------------------------------------------------------- #
def parse_char_ids(char_ids: str, all_ids=None) -> tuple:
    """解析 `id / 起始-结束 / 逗号组合`，返回 `(存在的 id 列表, 不在列表中的 id)`。

    与 `stories.parse_char_ids` 语义一致，但**不向 stdout 打印**提示。
    """
    if all_ids is None:
        all_ids = [c.get("id") for c in load_character_index() if c.get("id")]
    want = set()
    for tok in str(char_ids).replace(" ", "").split(","):
        if not tok:
            continue
        if "-" in tok:
            a, b = tok.split("-", 1)
            try:
                start, end = int(a), int(b)
            except ValueError as exc:
                raise ValueError("无法解析 id 范围：%s（应形如 10000002-10000030）" % tok) from exc
            if start > end:
                start, end = end, start
            want.update(str(i) for i in range(start, end + 1))
        else:
            want.add(tok)
    have = set(all_ids)
    return [i for i in all_ids if i in want], sorted(want - have)


def resolve_ids(char_ids: str) -> list:
    """解析出本次要处理的角色 id 列表（不存在的不报错，交由业务决定）。"""
    return parse_char_ids(char_ids)[0]


# --------------------------------------------------------------------------- #
# 长任务：生成子进程命令行 + 进度
# --------------------------------------------------------------------------- #
def _base_argv() -> list:
    """`python -m story_tr --data-dir <home>` —— 不依赖 PATH 上的 story-tr。"""
    return [sys.executable, "-m", "story_tr", "--data-dir", paths.home()]


def translate_argv(char_ids: str, stories_filter: str = "", mode: str = "fixed",
                   chain: str = "asia", workers: int = 1, provider: str = "",
                   full_only: bool = False, segments_only: bool = False) -> list:
    """构造 `story-tr translate` 的命令行（安全子集，不含 --overwrite / --reset）。"""
    argv = _base_argv() + ["translate", char_ids]
    if stories_filter:
        argv += ["--stories", stories_filter]
    argv += ["--mode", mode]
    if mode == "fixed":
        argv += ["--chain", chain]
    argv += ["--workers", str(int(workers))]
    if provider:
        argv += ["--provider", provider]
    if full_only:
        argv.append("--full-only")
    if segments_only:
        argv.append("--segments-only")
    return argv


def fetch_argv(update: bool = True) -> list:
    """构造 `story-tr fetch` 的命令行（update=True → `--update`，否则仅用本地缓存）。"""
    argv = _base_argv() + ["fetch"]
    if update:
        argv.append("--update")
    return argv


def count_translate_tasks(ids, stories_filter: str = "", parts: str = "both") -> int:
    """预算本次 translate 会产出多少份结果（供 job 进度用）。"""
    names = [s.strip() for s in stories_filter.split(",") if s.strip()]
    fake = types.SimpleNamespace(stories=",".join(names), all=not names)
    log = logging.getLogger("story_tr.mcp.count")
    total = 0
    for cid in ids:
        base = os.path.join(stories.STORY_ROOT, cid, "profile", "story")
        selected = stories.resolve_stories(fake, cid)
        total += len(stories.build_tasks(base, cid, selected, log, parts))
    return total


def make_translate_progress(ids, total: int):
    """返回一个进度读取函数：已完成数取自 `.translate_stories_state.json`。"""
    scope = set(ids)

    def read() -> dict:
        state = stories.load_state() or {}
        done = sum(1 for key in state.get("completed", [])
                   if key.split("|", 1)[0] in scope)
        return {"done": min(done, total), "total": total}

    return read


# --------------------------------------------------------------------------- #
# 同步：单条文本翻译链（联网）
# --------------------------------------------------------------------------- #
def _mcp_logger() -> logging.Logger:
    """专用 logger：只写 stderr（绝不写 stdout，避免污染 MCP stdio 协议）。"""
    log = logging.getLogger("story_tr.mcp.chain")
    if not log.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("%(message)s"))
        log.addHandler(handler)
    log.setLevel(logging.INFO)
    log.propagate = False
    return log


def chain(text: str, mode: str = "fixed", chain: str = "asia", languages: str = "",
          steps: int = 20, provider: str = "") -> dict:
    """单条文本跑「来回翻译 N 次」链路（同步、联网），返回逐步与最终译文。"""
    args = types.SimpleNamespace(mode=mode, chain=chain, steps=steps, languages=languages)
    langs, label = tc.resolve_languages(args)
    cfg = tc.build_cfg(types.SimpleNamespace(proxy="", min_interval=tc.GOOGLE_MIN_INTERVAL,
                                             balance=False))
    providers = [provider] if provider else ["google", "bing", "baidu"]
    result = stories.run_chain(text, langs, providers, cfg, _mcp_logger(),
                               {"mode": mode, "chain": label})
    return {"source": result["source"], "detected": result["source_detected_lang"],
            "languages": langs, "provider": result["provider"],
            "steps": result["steps"], "final": result["final"]}


# --------------------------------------------------------------------------- #
# 同步：清理（默认只预览）
# --------------------------------------------------------------------------- #
def clean(char_ids: str, stories_filter: str = "", dry_run: bool = True,
          confirm: bool = False, keep_state: bool = False) -> dict:
    """清理 `result/` 翻译产物。

    **默认只预览**（`dry_run=True`）；只有同时传 `dry_run=False` 与 `confirm=True` 才真正删除。
    """
    from .. import clean as clean_mod

    ids, missing = parse_char_ids(char_ids, stories.load_char_ids())
    if not ids:
        raise ValueError("没有匹配到任何角色 id：%s" % char_ids)
    names = [s.strip() for s in stories_filter.split(",") if s.strip()] or None

    plan = []
    for cid in ids:
        targets = clean_mod.targets(cid, names)
        if not targets:
            continue
        files, size = clean_mod.measure(targets)
        plan.append({"char_id": cid, "targets": [os.path.basename(t) for t in targets],
                     "files": files, "bytes": size, "human": clean_mod.human(size)})
    files = sum(p["files"] for p in plan)
    size = sum(p["bytes"] for p in plan)

    if dry_run or not confirm:
        return {"dry_run": True, "plan": plan, "missing_ids": missing,
                "characters": len(plan), "files": files, "bytes": size,
                "human": clean_mod.human(size),
                "note": "未删除。确认无误后再调用，传 dry_run=false 且 confirm=true。"}

    for cid in ids:
        clean_mod.remove(clean_mod.targets(cid, names))
    removed = 0 if keep_state else clean_mod.clean_state(ids, names)
    return {"dry_run": False, "characters": len(plan), "files": files, "bytes": size,
            "human": clean_mod.human(size), "state_records_removed": removed}
