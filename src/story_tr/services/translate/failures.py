"""失败登记文件与失败清单的读写。

- `.translate_failures_state.json`：本次运行未解决的失败（不跨运行自动重试）
- `logs/failures_<ts>.json` / `logs/tts_failures_<ts>.json`：每轮失败清单
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Any

from story_tr import models
from story_tr.services import storage

from . import summary

log = logging.getLogger("story_tr.services.translate.failures")

FAILURE_FILENAME = ".translate_failures_state.json"


def now() -> str:
    """当前时间（秒级 ISO 字符串）。"""
    return datetime.now().isoformat(timespec="seconds")


def registry_path(home_: str) -> str:
    return os.path.join(home_, FAILURE_FILENAME)


def read_registry(home_: str) -> models.FailureRegistry | None:
    """纯读登记文件（不写 `.corrupt-*`），供 status / estimate 使用。"""
    try:
        with open(registry_path(home_), encoding="utf-8") as f:
            return models.FailureRegistry.model_validate(json.load(f))
    except (OSError, ValueError):
        return None


def load_registry(home_: str) -> models.FailureRegistry | None:
    """读取登记文件；损坏时尝试 `.bak` 恢复。"""
    raw = storage.load_json_with_backup(registry_path(home_))
    if not raw:
        return None
    return models.FailureRegistry.model_validate(raw)


def save_registry(home_: str, registry: models.FailureRegistry) -> None:
    storage.atomic_write_json(registry_path(home_), registry)


def remove_registry(home_: str) -> None:
    storage.remove_with_backup(registry_path(home_))


def archive_registry(home_: str) -> int:
    """把上次遗留的登记归档为 `.bak`，返回被归档的失败条数。"""
    registry = read_registry(home_)
    count = len(registry.failures) if registry else 0
    if os.path.exists(registry_path(home_)):
        storage.remove_with_backup(registry_path(home_))
    if count:
        log.warning(
            "上次遗留失败 %d 条不会自动继承（仅单次运行内重试）；已归档为 %s.bak。",
            count,
            FAILURE_FILENAME,
        )
    return count


def record_of(cid: str, story: str, key: str, stage: str, exc: Exception, stamp: str) -> dict[str, Any]:
    """构造一条失败记录（供内存态登记使用）。"""
    return {
        "char_id": cid,
        "story": story,
        "key": key,
        "stage": stage,
        "error": f"{type(exc).__name__}: {exc}",
        "attempts": 1,
        "first_at": stamp,
        "last_at": stamp,
    }


def round_logs(home_: str, records: dict[str, dict[str, Any]], round_no: int = 0) -> list[str]:
    """按 stage 分别写本轮失败清单，返回写入的日志路径。"""
    suffix = f"_r{round_no}"
    translate_rows = [_log_row(rec) for rec in records.values() if rec["stage"] == "translate"]
    tts_rows = [_log_row(rec) for rec in records.values() if rec["stage"] == "tts"]
    paths: list[str] = []
    if translate_rows:
        paths.append(summary.write_failures(home_, translate_rows, suffix=suffix))
    if tts_rows:
        paths.append(summary.write_failures(home_, tts_rows, prefix="tts_failures", suffix=suffix))
    return paths


def _log_row(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "stage": record["stage"],
        "char_id": record["char_id"],
        "stories": [record["story"]],
        "keys": [record["key"]],
        "error": record["error"],
        "attempts": record["attempts"],
    }


def build_registry(
    signature: dict[str, Any],
    result_tag: str | None,
    started_at: str,
    rounds: int,
    records: dict[str, dict[str, Any]],
) -> models.FailureRegistry:
    """把内存态失败记录转成登记模型。"""
    failures = [
        models.FailureRecord(
            char_id=rec["char_id"],
            story=rec["story"],
            key=rec["key"],
            stage=rec["stage"],
            error=rec["error"],
            attempts=int(rec["attempts"]),
            first_at=rec["first_at"],
            last_at=rec["last_at"],
        )
        for rec in records.values()
    ]
    failures.sort(key=lambda f: (f.char_id, f.story, f.key))
    return models.FailureRegistry(
        signature=signature,
        result_tag=result_tag,
        started_at=started_at,
        updated_at=now(),
        rounds=rounds,
        failures=failures,
    )
