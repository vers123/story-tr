"""失败重试循环：指数退避 + 轮数上限 + 可取消。

普通批量翻译与 `--tts-only` 共用：调用方提供 `failures` 字典（可原地增删）与
`retry_one(record)` 回调，每轮结束后通过 `on_round_done` 落盘日志/登记。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

from story_tr.errors import ExitCode

from .errors import Abort

log = logging.getLogger("story_tr.services.translate.retry")

BACKOFF_BASE = 5.0
BACKOFF_CAP = 60.0
_CANCEL_POLL = 0.2


def backoff_seconds(round_no: int) -> float:
    """第 n 轮重试前的等待秒数（指数退避，上限 60s）。"""
    return min(BACKOFF_BASE * float(2 ** (round_no - 1)), BACKOFF_CAP)


def sleep_backoff(round_no: int, cancel: Any) -> None:
    """休息退避时间；期间可被取消事件打断。"""
    remaining = backoff_seconds(round_no)
    while remaining > 0:
        if cancel is not None and cancel.is_set():
            raise Abort(ExitCode.CANCELLED)
        step = min(_CANCEL_POLL, remaining)
        time.sleep(step)
        remaining -= step


def run_rounds(
    failures: dict[str, dict[str, Any]],
    retry_one: Callable[[dict[str, Any]], None],
    retry_rounds: int,
    cancel: Any,
    on_round_done: Callable[[int], None] | None = None,
) -> None:
    """重试直到没有失败或轮数耗尽；`retry_one` 负责原地删除/更新记录。"""
    for round_no in range(1, int(retry_rounds) + 1):
        if not failures:
            return
        sleep_backoff(round_no, cancel)
        before = len(failures)
        for record in list(failures.values()):
            if cancel is not None and cancel.is_set():
                raise Abort(ExitCode.CANCELLED)
            retry_one(record)
        if on_round_done is not None:
            on_round_done(round_no)
        log.info(
            "第 %d/%d 轮重试结束：恢复 %d 条 / 仍失败 %d 条",
            round_no,
            retry_rounds,
            before - len(failures),
            len(failures),
        )
        if not failures:
            return
