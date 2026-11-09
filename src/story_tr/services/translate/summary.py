"""结果落盘与汇总：`result/{story}/*.json`、`segments.json` 与失败清单。"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any

from .. import storage


def write_failures(home_: str, failures: list[dict[str, Any]], prefix: str = "failures") -> str:
    """把失败清单写入 `logs/{prefix}_{ts}.json` 并返回路径。"""
    os.makedirs(storage.logs_dir(home_), exist_ok=True)
    path = os.path.join(
        storage.logs_dir(home_), "{}_{}.json".format(prefix, datetime.now().strftime("%Y%m%d_%H%M%S"))
    )
    storage.atomic_write_json(path, failures)
    return path


def _entry(d: str, key: str) -> dict[str, Any] | None:
    """读取单个结果文件并生成汇总条目；文件不存在返回 None。"""
    path = os.path.join(d, key + ".json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        row = json.load(f)
    return {
        "source": row["source"],
        "languages": row["languages"],
        "final": row["final"],
        "json": key + ".json",
        "mp3": (key + ".mp3") if os.path.exists(os.path.join(d, key + ".mp3")) else None,
    }


def _segment_entry(d: str, name: str) -> dict[str, Any]:
    """读取段落结果并生成汇总条目（含 `segment_index`）。"""
    key = name[:-5]
    with open(os.path.join(d, name), encoding="utf-8") as f:
        row = json.load(f)
    return {
        "index": row.get("segment_index"),
        "source": row["source"],
        "languages": row["languages"],
        "final": row["final"],
        "json": name,
        "mp3": (key + ".mp3") if os.path.exists(os.path.join(d, key + ".mp3")) else None,
    }


def write_summary(
    home_: str, cid: str, story: str, provider_label: str, mode_label: str, src_file: str = "zh.md"
) -> None:
    """重建单个故事的 `segments.json` 汇总。"""
    d = os.path.join(storage.result_dir(home_, cid), story)
    if not os.path.isdir(d):
        return
    summary: dict[str, Any] = {
        "id": cid,
        "story": story,
        "source_file": storage.rel(
            home_, os.path.join(storage.profile_story_dir(home_, cid), story, src_file)
        ),
        "provider": provider_label,
        "mode": mode_label,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "full": _entry(d, "full"),
        "title": _entry(d, "title"),
        "segments": [
            _segment_entry(d, name)
            for name in sorted(n for n in os.listdir(d) if n.startswith("seg_") and n.endswith(".json"))
        ],
    }
    storage.atomic_write_json(os.path.join(d, "segments.json"), summary)


def story_labels(d: str) -> tuple[str, str]:
    """从已有 `segments.json` / 结果文件推出 `(provider 标签, mode 标签)`。"""
    summary_path = os.path.join(d, "segments.json")
    if os.path.exists(summary_path):
        try:
            with open(summary_path, encoding="utf-8") as f:
                summary = json.load(f)
            provider = str(summary.get("provider") or "")
            mode = str(summary.get("mode") or "")
            if provider or mode:
                return provider, mode
        except (OSError, ValueError):
            pass
    for name in sorted(os.listdir(d)):
        if not name.endswith(".json") or name == "segments.json":
            continue
        try:
            with open(os.path.join(d, name), encoding="utf-8") as f:
                row = json.load(f)
        except (OSError, ValueError):
            continue
        provider = " → ".join(row.get("providers") or [])
        mode = str(row.get("mode") or "")
        if row.get("chain"):
            mode += "/" + str(row["chain"])
        return provider, mode
    return "", ""
