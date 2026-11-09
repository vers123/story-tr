"""批量任务构建：故事选择、单份结果就绪判断。"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from .. import storage, story_inputs

log = logging.getLogger("story_tr.services.translate.tasks")


def parts_label(full_only: bool, segments_only: bool) -> str:
    """`--full-only` / `--segments-only` → `full` / `segments` / `both`。"""
    if full_only:
        return "full"
    if segments_only:
        return "segments"
    return "both"


def story_names(home_: str, cid: str, stories: str, src_file: str) -> list[str]:
    """解析本次要处理的故事名（`--stories` 优先，否则取含正文的来源文件）。"""
    names = [s.strip() for s in stories.split(",") if s.strip()]
    return story_inputs.resolve_stories(home_, cid, names, src_file)


def build_tasks(
    home_: str, cid: str, stories: list[str], parts: str, src_file: str = "zh.md"
) -> list[dict[str, Any]]:
    """把故事拆成 title / full / 段落三类翻译任务。"""
    base = storage.profile_story_dir(home_, cid)
    tasks: list[dict[str, Any]] = []
    for story in stories:
        md = os.path.join(base, story, src_file)
        if not os.path.exists(md):
            log.warning("跳过 %s/%s：找不到 %s", cid, story, storage.rel(home_, md))
            continue
        paras = story_inputs.read_paragraphs(md)
        if not paras:
            log.warning("跳过 %s/%s：%s 无正文", cid, story, src_file)
            continue
        title = story_inputs.read_title(md)
        if title:
            tasks.append({"story": story, "key": "title", "index": None, "text": title})
        if parts in ("both", "full"):
            tasks.append({"story": story, "key": "full", "index": None, "text": "\n\n".join(paras)})
        if parts in ("both", "segments"):
            for i, para in enumerate(paras, 1):
                tasks.append({"story": story, "key": f"seg_{i:02d}", "index": i, "text": para})
    return tasks


def result_is_stale(json_path: str, source: str) -> bool:
    """已有结果是否与当前源文不一致（读不到也算过期）。"""
    try:
        with open(json_path, encoding="utf-8") as f:
            row = json.load(f)
    except (OSError, ValueError):
        return True
    return (row.get("source") or "").strip() != (source or "").strip()
