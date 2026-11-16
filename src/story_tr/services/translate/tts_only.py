"""只补生成缺失语音（不重译文本、不触碰批次状态）。

失败会进入统一的失败登记并自动重试（指数退避、`--retry-rounds` 轮上限）。
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
from typing import Any

from story_tr.errors import BadOutput, ExitCode

from .. import storage
from . import failures as failures_mod
from . import retry, summary
from . import tts as tts_mod
from .errors import Abort
from .runner import Plan, plan_signature

log = logging.getLogger("story_tr.services.translate.tts_only")


def _wanted_keys(story_dir: str, parts: str) -> list[str]:
    """某个故事结果目录中、按 `parts` 需要补语音的结果键。"""
    keys = ["title"] if os.path.exists(os.path.join(story_dir, "title.json")) else []
    if parts in ("both", "full") and os.path.exists(os.path.join(story_dir, "full.json")):
        keys.append("full")
    if parts in ("both", "segments"):
        keys += [
            name[:-5]
            for name in sorted(os.listdir(story_dir))
            if name.startswith("seg_") and name.endswith(".json")
        ]
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


def _record(failures: dict[str, dict[str, Any]], cid: str, story: str, key: str, exc: Exception) -> None:
    """登记（或累加）一条 TTS 失败。"""
    failure_key = f"{cid}|{story}|{key}"
    stamp = failures_mod.now()
    record = failures.get(failure_key)
    if record is None:
        failures[failure_key] = failures_mod.record_of(cid, story, key, "tts", exc, stamp)
    else:
        record["error"] = f"{type(exc).__name__}: {exc}"
        record["attempts"] = int(record["attempts"]) + 1
        record["last_at"] = stamp


def _write_audio(plan: Plan, cid: str, story: str, key: str, failures: dict[str, dict[str, Any]]) -> bool:
    """为单份结果生成语音；成功返回 True，失败登记后返回 False。"""
    d = os.path.join(storage.result_dir(plan.home, cid, plan.result_tag), story)
    json_path = os.path.join(d, key + ".json")
    try:
        with open(json_path, encoding="utf-8") as f:
            row = json.load(f)
        text = (row.get("final") or "").strip()
        if row.get("passthrough"):
            return False
        if not text:
            raise BadOutput("结果没有可朗读文本")
        audio = tts_mod.tts_with_retry(text, plan.settings)
        with open(os.path.join(d, key + ".mp3"), "wb") as f:
            f.write(audio)
    except Exception as exc:  # noqa: BLE001 - 语音失败只记录并等待重试
        _record(failures, cid, story, key, exc)
        log.error("语音失败 %s/%s/%s：%s", cid, story, key, exc)
        return False
    failures.pop(f"{cid}|{story}|{key}", None)
    log.info("[%s] %s/%s  语音 %d 字节", cid, story, key, len(audio))
    return True


def _tts_char(plan: Plan, cid: str, char_index: int, failures: dict[str, dict[str, Any]], cancel: Any) -> int:
    """为单个角色补语音；返回写入份数。"""
    rdir = storage.result_dir(plan.home, cid, plan.result_tag)
    if not os.path.isdir(rdir):
        log.warning("跳过 %s：还没有翻译结果", cid)
        return 0
    wanted = [s.strip() for s in plan.stories.split(",") if s.strip()]
    story_names = wanted or sorted(
        name for name in os.listdir(rdir) if os.path.isdir(os.path.join(rdir, name))
    )
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
        if needs_audio and _write_audio(plan, cid, story, key, failures):
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
        summary.write_summary(plan.home, cid, story, provider_label, mode_label, result_tag=plan.result_tag)
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


def _persist(plan: Plan, failures: dict[str, dict[str, Any]], started_at: str, rounds: int) -> None:
    """落盘本轮失败清单与登记文件（无失败则删除登记）。"""
    if not failures:
        failures_mod.remove_registry(plan.home)
        return
    failures_mod.round_logs(plan.home, failures, rounds)
    failures_mod.save_registry(
        plan.home,
        failures_mod.build_registry(plan_signature(plan), plan.result_tag, started_at, rounds, failures),
    )


def run_tts_only(plan: Plan, cancel: Any = None) -> ExitCode:
    """补语音入口（含失败自动重试），返回 `ExitCode`。"""
    failures: dict[str, dict[str, Any]] = {}
    started_at = failures_mod.now()
    written = 0
    try:
        for char_index, cid in enumerate(plan.selected, 1):
            written += _tts_char(plan, cid, char_index, failures, cancel)
    except Abort as exc:
        _persist(plan, failures, started_at, 0)
        log.warning("已取消：语音补生成中止。")
        return exc.code

    failures_mod.archive_registry(plan.home)
    if failures and plan.retry_rounds > 0 and not (cancel is not None and cancel.is_set()):
        _persist(plan, failures, started_at, 0)
        try:
            retry.run_rounds(
                failures,
                lambda record: _retry_one(plan, record, failures),
                plan.retry_rounds,
                cancel,
                lambda round_no: _persist(plan, failures, started_at, round_no),
            )
        except Abort as exc:
            _persist(plan, failures, started_at, 0)
            return exc.code
    elif failures:
        _persist(plan, failures, started_at, 0)

    if failures:
        log.error(
            "语音补生成完成 %d 份，仍失败 %d 项（登记 %s）。",
            written,
            len(failures),
            failures_mod.FAILURE_FILENAME,
        )
        return ExitCode.PARTIAL
    failures_mod.remove_registry(plan.home)
    log.info("语音补生成完成 %d 份。", written)
    return ExitCode.OK


def _retry_one(plan: Plan, record: dict[str, Any], failures: dict[str, dict[str, Any]]) -> None:
    _write_audio(plan, record["char_id"], record["story"], record["key"], failures)
