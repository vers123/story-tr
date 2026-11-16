"""`story-tr export`：把结果目录导出为 jsonl / parquet + manifest。

只读 `data/`，输出写到 `--out`（默认 `out/export`）。parquet 需要可选依赖
`pyarrow`（`pip install "story-tr[export]"`），未安装时返回 USAGE。
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Any

from story_tr import __version__
from story_tr.errors import ExitCode

from . import storage, story_inputs

log = logging.getLogger("story_tr.services.export")

FORMATS = ("jsonl", "parquet")
DEFAULT_OUT = os.path.join("out", "export")


def _select(
    home_: str, char_ids: list[str] | None, all_characters: bool
) -> tuple[list[str] | None, ExitCode | None]:
    """解析导出角色集合；默认全部角色。"""
    if not os.path.exists(storage.char_index_path(home_)):
        log.error("找不到 %s；请先运行 fetch。", storage.char_index_path(home_))
        return None, ExitCode.USAGE
    all_ids = story_inputs.load_char_ids(home_)
    if char_ids and all_characters:
        log.error("不能同时指定角色 id 与 --all-characters，请二选一。")
        return None, ExitCode.USAGE
    try:
        selected = (
            all_ids
            if all_characters or not char_ids
            else story_inputs.parse_char_ids(list(char_ids), all_ids, warn_missing=True)
        )
    except ValueError as exc:
        log.error(str(exc))
        return None, ExitCode.USAGE
    if not selected:
        log.error("没有匹配到任何角色 id。")
        return None, ExitCode.USAGE
    return selected, None


def _int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _record(
    home_: str,
    cid: str,
    char: dict[str, Any],
    story: str,
    key: str,
    row: dict[str, Any],
    story_dir: str,
    with_steps: bool,
) -> dict[str, Any]:
    """把一份结果 JSON 变成扁平记录（故事标题由调用方补齐）。"""
    audio = key + ".mp3"
    result_path = os.path.join(story_dir, key + ".json")
    record: dict[str, Any] = {
        "id": cid,
        "name_zh": char.get("name_zh"),
        "name_en": char.get("name_en"),
        "story": story,
        "story_title_zh": None,
        "story_title_en": None,
        "key": key,
        "kind": "title" if key == "title" else ("full" if key == "full" else "segment"),
        "index": _int(row.get("segment_index")),
        "source": row.get("source"),
        "source_lang": row.get("source_lang"),
        "target_lang": row.get("target_lang"),
        "final": row.get("final"),
        "mode": row.get("mode"),
        "chain": row.get("chain"),
        "providers": row.get("providers") or [],
        "languages": row.get("languages") or [],
        "passthrough": bool(row.get("passthrough")),
        "audio": audio if os.path.exists(os.path.join(story_dir, audio)) else None,
        "source_file": row.get("source_file"),
        "result_file": storage.rel(home_, result_path),
    }
    if with_steps:
        record["steps"] = row.get("steps") or []
        record["rwkv"] = row.get("rwkv")
    return record


def _story_records(
    home_: str,
    cid: str,
    char: dict[str, Any],
    story: str,
    result_tag: str | None,
    with_steps: bool,
) -> list[dict[str, Any]]:
    """读取一个故事结果目录下的全部记录。"""
    story_dir = os.path.join(storage.result_dir(home_, cid, result_tag), story)
    if not os.path.isdir(story_dir):
        return []
    profile_dir = os.path.join(storage.profile_story_dir(home_, cid), story)
    records: list[dict[str, Any]] = []
    for name in sorted(os.listdir(story_dir)):
        if not name.endswith(".json") or name == "segments.json":
            continue
        key = name[:-5]
        try:
            with open(os.path.join(story_dir, name), encoding="utf-8") as f:
                row = json.load(f)
        except (OSError, ValueError):
            log.warning("跳过损坏结果：%s/%s/%s", cid, story, key)
            continue
        record = _record(home_, cid, char, story, key, row, story_dir, with_steps)
        record["story_title_zh"] = story_inputs.read_title(os.path.join(profile_dir, "zh.md"))
        record["story_title_en"] = story_inputs.read_title(os.path.join(profile_dir, "en.md"))
        records.append(record)
    return records


def _collect(
    home_: str,
    selected: list[str],
    stories: str,
    result_tag: str | None,
    with_steps: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """收集选中角色的记录并计算 manifest 计数。"""
    wanted = [s.strip() for s in stories.split(",") if s.strip()]
    chars = {c["id"]: c for c in story_inputs.load_chars(home_) if c.get("id")}
    records: list[dict[str, Any]] = []
    counts = {"characters": 0, "stories": 0, "tasks": 0, "title": 0, "full": 0, "segments": 0, "audio": 0}
    seen_chars: set[str] = set()
    seen_stories: set[tuple[str, str]] = set()
    for cid in selected:
        char = chars.get(cid, {"id": cid})
        rdir = storage.result_dir(home_, cid, result_tag)
        if not os.path.isdir(rdir):
            continue
        names = wanted or sorted(name for name in os.listdir(rdir) if os.path.isdir(os.path.join(rdir, name)))
        for story in names:
            story_records = _story_records(home_, cid, char, story, result_tag, with_steps)
            if not story_records:
                continue
            records.extend(story_records)
            seen_chars.add(cid)
            seen_stories.add((cid, story))
            counts["tasks"] += len(story_records)
            counts["title"] += sum(1 for r in story_records if r["kind"] == "title")
            counts["full"] += sum(1 for r in story_records if r["kind"] == "full")
            counts["segments"] += sum(1 for r in story_records if r["kind"] == "segment")
            counts["audio"] += sum(1 for r in story_records if r["audio"])
    counts["characters"] = len(seen_chars)
    counts["stories"] = len(seen_stories)
    return records, counts


def _fetch_hashes(home_: str) -> dict[str, str]:
    cache = os.path.join(home_, "data", "_fetch_all.json")
    try:
        with open(cache, encoding="utf-8") as f:
            rows = json.load(f)
    except (OSError, ValueError):
        return {}
    return {row["id"]: row["hash"] for row in rows if row.get("id") and row.get("hash")}


def _write_jsonl(path: str, records: list[dict[str, Any]]) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False))
            f.write("\n")
    os.replace(tmp, path)


def _import_pyarrow() -> Any:
    try:
        import pyarrow  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - 取决于可选依赖是否安装
        raise ImportError('未安装 pyarrow；请执行 pip install "story-tr[export]"') from exc
    return pyarrow


def _parquet_rows(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """parquet 不支持嵌套字段：steps / rwkv 序列化为 JSON 字符串。"""
    rows: list[dict[str, Any]] = []
    for record in records:
        row = dict(record)
        for key in ("steps", "rwkv"):
            if key in row:
                row[key] = json.dumps(row[key], ensure_ascii=False) if row[key] is not None else None
        rows.append(row)
    return rows


def _write_parquet(path: str, records: list[dict[str, Any]]) -> None:
    pyarrow = _import_pyarrow()
    pyarrow.parquet.write_table(pyarrow.Table.from_pylist(_parquet_rows(records)), path)


def _default_out(home_: str, out: str | None) -> str:
    value = out or DEFAULT_OUT
    return value if os.path.isabs(value) else os.path.join(home_, value)


def run(
    char_ids: list[str] | None = None,
    all_characters: bool = False,
    stories: str = "",
    result_tag: str | None = None,
    out: str | None = None,
    fmt: str = "jsonl",
    with_steps: bool = False,
    data_dir: str | None = None,
) -> ExitCode:
    """导出结果目录，返回 `ExitCode`。"""
    try:
        result_tag = storage.validate_result_tag(result_tag)
    except ValueError as exc:
        log.error(str(exc))
        return ExitCode.USAGE
    if fmt not in FORMATS:
        log.error("未知导出格式：%s（可选 %s）。", fmt, " / ".join(FORMATS))
        return ExitCode.USAGE
    home_ = storage.home(data_dir)
    selected, code = _select(home_, char_ids, all_characters)
    if code is not None or selected is None:
        return code or ExitCode.USAGE
    records, counts = _collect(home_, selected, stories, result_tag, with_steps)
    if not records:
        log.error("没有可导出的结果；请先运行 fetch / translate。")
        return ExitCode.USAGE

    out_dir = _default_out(home_, out)
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "dataset." + fmt)
    try:
        if fmt == "jsonl":
            _write_jsonl(path, records)
        else:
            _write_parquet(path, records)
    except ImportError as exc:
        log.error(str(exc))
        return ExitCode.USAGE

    manifest = {
        "version": 1,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "tool_version": __version__,
        "result_tag": result_tag,
        "format": fmt,
        "counts": counts,
        "fetch_hashes": _fetch_hashes(home_),
        "files": [os.path.basename(path)],
    }
    manifest_path = os.path.join(out_dir, "manifest.json")
    storage.atomic_write_json(manifest_path, manifest)
    log.info("已导出 %d 条记录 → %s", len(records), storage.rel(home_, path))
    log.info("清单 → %s", storage.rel(home_, manifest_path))
    return ExitCode.OK
