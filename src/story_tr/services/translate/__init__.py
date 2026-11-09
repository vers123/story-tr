"""批量翻译编排：把 `core` 路线与 `transport` 后端组装成用例。

- `tasks`：故事 → title / full / 段落任务
- `chain_runner`：单份文本的来回翻译
- `runner`：参数校验、批次状态、并发调度
- `tts` / `tts_only`：语音生成与只补语音
- `summary`：`segments.json` 汇总与失败清单
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from story_tr.errors import ExitCode

from . import chain_runner, runner, summary, tasks, tts
from . import tts_only as tts_only_mod
from .runner import STATE_FILENAME, Plan

TARGET_ZH = chain_runner.TARGET_ZH
TTS_ATTEMPTS = tts.TTS_ATTEMPTS
TTS_BACKOFF = tts.TTS_BACKOFF
SAVE_EVERY = runner.SAVE_EVERY
SAVE_INTERVAL = runner.SAVE_INTERVAL

log = logging.getLogger("story_tr.services.translate")

__all__ = [
    "Plan",
    "SAVE_EVERY",
    "SAVE_INTERVAL",
    "STATE_FILENAME",
    "TARGET_ZH",
    "TTS_ATTEMPTS",
    "TTS_BACKOFF",
    "chain_runner",
    "run",
    "runner",
    "summary",
    "tasks",
    "tts",
    "tts_only_mod",
]


def run(
    char_ids: list[str],
    all_characters: bool = False,
    stories: str = "",
    mode: str = "fixed",
    chain: str = "asia",
    languages: str = "",
    steps: int = 20,
    provider: str | None = None,
    workers: int = 1,
    min_interval: float = 0.3,
    balance: bool = False,
    full_only: bool = False,
    segments_only: bool = False,
    overwrite: bool = False,
    reset: bool = False,
    revalidate: bool = False,
    no_cache: bool = False,
    fail_fast: bool = False,
    max_failures: int = 20,
    no_tts: bool = False,
    tts_only: bool = False,
    data_dir: str | None = None,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
    cancel: Any = None,
) -> ExitCode:
    """批量翻译入口，返回 `ExitCode`。"""
    plan, code = runner.build_plan(
        char_ids=char_ids,
        all_characters=all_characters,
        stories=stories,
        mode=mode,
        chain=chain,
        languages=languages,
        steps=steps,
        provider=provider,
        workers=workers,
        min_interval=min_interval,
        balance=balance,
        full_only=full_only,
        segments_only=segments_only,
        overwrite=overwrite,
        reset=reset,
        revalidate=revalidate,
        fail_fast=fail_fast,
        max_failures=max_failures,
        no_tts=no_tts,
        tts_only=tts_only,
        data_dir=data_dir,
        on_progress=on_progress,
    )
    if code is not None or plan is None:
        return code or ExitCode.USAGE
    if tts_only:
        return tts_only_mod.run_tts_only(plan, cancel)
    return runner.run_batch(plan, cancel)
