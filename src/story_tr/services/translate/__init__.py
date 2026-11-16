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

from . import chain_runner, estimate, runner, summary, tasks, tts
from . import tts_only as tts_only_mod
from .runner import STATE_FILENAME, Plan

TARGET_ZH = chain_runner.TARGET_ZH
TTS_ATTEMPTS = tts.TTS_ATTEMPTS
TTS_BACKOFF = tts.TTS_BACKOFF
SAVE_EVERY = runner.SAVE_EVERY
SAVE_INTERVAL = runner.SAVE_INTERVAL

# `runner.build_plan` 的关键字参数与 `run()` 的同名形参一一对应
_BUILD_KEYS = (
    "char_ids",
    "all_characters",
    "stories",
    "mode",
    "chain",
    "languages",
    "steps",
    "provider",
    "workers",
    "min_interval",
    "balance",
    "full_only",
    "segments_only",
    "overwrite",
    "reset",
    "revalidate",
    "fail_fast",
    "max_failures",
    "no_tts",
    "tts_only",
    "data_dir",
    "on_progress",
    "no_cache",
    "result_tag",
    "batch",
    "reconcile",
    "retry_rounds",
)

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
    result_tag: str | None = None,
    batch: bool = True,
    reconcile: bool = False,
    retry_rounds: int = 5,
    plan_only: bool = False,
    fail_fast: bool = False,
    max_failures: int = 20,
    no_tts: bool = False,
    tts_only: bool = False,
    data_dir: str | None = None,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
    cancel: Any = None,
) -> ExitCode:
    """批量翻译入口，返回 `ExitCode`。"""
    # 快照后再推导：兼容 Python 3.10/3.11 的推导式独立作用域
    frame = locals()
    options = {k: v for k, v in frame.items() if k in _BUILD_KEYS}
    plan, code = runner.build_plan(**options)
    if code is not None or plan is None:
        return code or ExitCode.USAGE
    if plan_only:
        if reconcile:
            log.warning("--plan 为只读模式，忽略 --reconcile。")
        print(estimate.render(estimate.estimate(plan, tts_only=tts_only)))
        return ExitCode.OK
    if tts_only:
        return tts_only_mod.run_tts_only(plan, cancel)
    return runner.run_batch(plan, cancel)
