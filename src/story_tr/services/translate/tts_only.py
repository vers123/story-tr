"""只补生成缺失语音（不重译文本、不触碰批次状态）。"""

from __future__ import annotations

import contextlib
import json
import logging
import os
from typing import Any

from story_tr.errors import BadOutput, ExitCode

from .. import storage
from . import summary
from . import tts as tts_mod
from .errors import Abort
from .runner import Plan

log = logging.getLogger("story_tr.services.translate.tts_only")


def _wanted_keys(d: str, parts: str) -> list[str]:
    """某个故事结果目录中、按 `parts` 需要补语音的结果键。"""
    keys = ["title"] if os.path.exists(os.path.join(d, "title.json")) else []
    if parts in ("both", "full") and os.path.exists(os.path.join(d, "full.json")):
        keys.append("full")
    if parts in ("both", "segments"):
        keys += [n[:-5] for n in sorted(os.listdir(d)) if n.startswith("seg_") and n.endswith(".json")]
    return keys


def _char_jobs(rdir: str, story_names: list[str], parts: str) -> list[tuple[str, str]]:
    """汇总某角色下需要补语音的 `(story, key)` 列表。"""
    jobs: list[tuple[str, str]] = []
    for story in story_names:
        d = os.path.join(rdir, story)
        if not os.path.isdir(d):
            log.warning("跳过 %s：没有结果目录", story)
            continue
        jobs += [(story, key) for key in _wanted_keys(d, parts)]
    return jobs


def _write_one(plan: Plan, cid: str, story: str, key: str, failures: list[dict[str, Any]]) -> bool:
    """为单份结果生成语音；返回是否写入成功。"""
    d = os.path.join(storage.result_dir(plan.home, cid), story)
    json_path = os.path.join(d, key + ".json")
    mp3_path = os.path.join(d, key + ".mp3")
    try:
        with open(json_path, encoding="utf-8") as f:
            row = json.load(f)
        text = (row.get("final") or "").strip()
        if row.get("passthrough"):
            return False
        if not text:
            raise BadOutput("结果没有可朗读文本")
        audio = tts_mod.tts_with_retry(text, plan.settings)
        with open(mp3_path, "wb") as audio_file:
            audio_file.write(audio)
    except Exception as exc:  # noqa: BLE001 - 语音失败只记录
        failures.append(
            {
                "stage": "tts",
                "char_id": cid,
                "stories": [story],
                "keys": [key],
                "error": f"{type(exc).__name__}: {exc}",
            }
        )
        log.error("语音失败 %s/%s/%s：%s", cid, story, key, exc)
        return False
    log.info("[%s] %s/%s  语音 %d 字节", cid, story, key, len(audio))
    return True


def _tts_char(plan: Plan, cid: str, char_index: int, failures: list[dict[str, Any]], cancel: Any) -> int:
    """为单个角色补语音；返回写入份数。"""
    rdir = storage.result_dir(plan.home, cid)
    if not os.path.isdir(rdir):
        log.warning("跳过 %s：还没有翻译结果", cid)
        return 0
    wanted = [s.strip() for s in plan.stories.split(",") if s.strip()]
    story_names = wanted or sorted(n for n in os.listdir(rdir) if os.path.isdir(os.path.join(rdir, n)))
    jobs = _char_jobs(rdir, story_names, plan.parts)
    log.info("角色 %s：%d 份结果需要补语音", cid, len(jobs))
    _emit(
        plan,
        event="char_start",
        char_id=cid,
        char_index=char_index,
        chars=len(plan.selected),
        tasks=len(jobs),
        done=0,
    )

    written = 0
    touched: list[str] = []
    for done, (story, key) in enumerate(jobs, 1):
        if cancel is not None and cancel.is_set():
            raise Abort(ExitCode.CANCELLED)
        needs_audio = plan.overwrite or not os.path.exists(os.path.join(rdir, story, key + ".mp3"))
        if needs_audio and _write_one(plan, cid, story, key, failures):
            written += 1
            touched.append(story)
        _emit(
            plan,
            event="progress",
            char_id=cid,
            char_index=char_index,
            chars=len(plan.selected),
            tasks=len(jobs),
            done=done,
        )

    for story in dict.fromkeys(touched):
        provider_label, mode_label = summary.story_labels(os.path.join(rdir, story))
        summary.write_summary(plan.home, cid, story, provider_label, mode_label)
    _emit(
        plan,
        event="char_done",
        char_id=cid,
        char_index=char_index,
        chars=len(plan.selected),
        tasks=len(jobs),
        done=len(jobs),
    )
    return written


def _emit(plan: Plan, **kw: Any) -> None:
    if plan.on_progress is None:
        return
    with contextlib.suppress(Exception):
        plan.on_progress(kw)


def run_tts_only(plan: Plan, cancel: Any = None) -> ExitCode:
    """补语音入口，返回 `ExitCode`。"""
    failures: list[dict[str, Any]] = []
    written = 0
    try:
        for char_index, cid in enumerate(plan.selected, 1):
            written += _tts_char(plan, cid, char_index, failures, cancel)
    except Abort as exc:
        log.warning("已取消：语音补生成中止。")
        return exc.code

    if failures:
        path = summary.write_failures(plan.home, failures, prefix="tts_failures")
        log.error(
            "语音补生成完成 %d 份，失败 %d 项（已记录）。清单：%s",
            written,
            len(failures),
            storage.rel(plan.home, path),
        )
        return ExitCode.PARTIAL
    log.info("语音补生成完成 %d 份。", written)
    return ExitCode.OK
