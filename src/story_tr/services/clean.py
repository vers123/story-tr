"""清理 `{id}/result/` 下的翻译产物，保留 `result/` 目录本身。

- 选择与 `translate` 一致：char_ids（id / 范围 / 逗号）或 --all-characters，`--stories` 可细化
- `result/video/` 默认保留；只有显式 `--stories video` 才清理
- 默认交互确认；`--dry-run` 只预览；`-y/--yes` 跳过确认
- 默认同步移除状态记录并重置批次签名；`--keep-state` 保留
"""

from __future__ import annotations

import contextlib
import logging
import os
import shutil

from story_tr import models
from story_tr.errors import ExitCode

from . import storage, story_inputs

log = logging.getLogger("story_tr.services.clean")

STATE_FILENAME = ".translate_stories_state.json"
PROTECTED = {"video"}


def _targets(home_: str, cid: str, names: list[str] | None) -> list[str]:
    base = storage.result_dir(home_, cid)
    if not os.path.isdir(base):
        return []
    if names:
        return [os.path.join(base, n) for n in names if os.path.exists(os.path.join(base, n))]
    return [os.path.join(base, n) for n in sorted(os.listdir(base)) if n not in PROTECTED]


def _measure(paths: list[str]) -> tuple[int, int]:
    n = size = 0
    for p in paths:
        if os.path.isdir(p):
            for root, _, files in os.walk(p):
                for f in files:
                    n += 1
                    with contextlib.suppress(OSError):
                        size += os.path.getsize(os.path.join(root, f))
        elif os.path.isfile(p):
            n += 1
            with contextlib.suppress(OSError):
                size += os.path.getsize(p)
    return n, size


_human = storage.human_size


def _remove(paths: list[str]) -> None:
    for p in paths:
        if os.path.isdir(p):
            shutil.rmtree(p)
        elif os.path.exists(p):
            os.remove(p)


def _state_key_match(key: str, ids: set[str], names: list[str] | None) -> bool:
    parts = key.split("|")
    if len(parts) < 2 or parts[0] not in ids:
        return False
    return not names or parts[1] in names


def _clean_state(home_: str, char_ids: list[str], names: list[str] | None, dry_run: bool = False) -> int:
    state_path = os.path.join(home_, STATE_FILENAME)
    raw = storage.load_json_with_backup(state_path)
    state = models.BatchState.model_validate(raw) if raw else None
    if not state or not state.completed:
        return 0
    ids = set(char_ids)
    hits = [k for k in state.completed if _state_key_match(k, ids, names)]
    if hits and not dry_run:
        dropped = set(hits)
        state.completed = [k for k in state.completed if k not in dropped]
        state.signature = {}
        state.updated_at = None
        storage.atomic_write_json(state_path, state)
    return len(hits)


def _select(
    home_: str,
    char_ids: list[str],
    all_characters: bool,
) -> tuple[list[str] | None, ExitCode | None]:
    """校验并解析要清理的角色 id。"""
    if char_ids and all_characters:
        log.error("不能同时指定角色 id 与 --all-characters，请二选一。")
        return None, ExitCode.USAGE
    if not char_ids and not all_characters:
        log.error("请指定角色 id / 范围，或用 --all-characters 处理全部角色。")
        return None, ExitCode.USAGE
    if not os.path.exists(storage.char_index_path(home_)):
        log.error("找不到 %s；请先运行 fetch。", storage.char_index_path(home_))
        return None, ExitCode.USAGE
    all_ids = story_inputs.load_char_ids(home_)
    try:
        selected = all_ids if all_characters else story_inputs.parse_char_ids(list(char_ids), all_ids)
    except ValueError as exc:
        log.error(str(exc))
        return None, ExitCode.USAGE
    if not selected:
        log.error("没有匹配到任何角色 id。")
        return None, ExitCode.USAGE
    return selected, None


def _build_plan(
    home_: str, selected: list[str], names: list[str] | None
) -> tuple[list[tuple[str, list[str], int, int]], int, int]:
    """生成 `(角色, 目标路径, 文件数, 字节数)` 计划与合计。"""
    plan: list[tuple[str, list[str], int, int]] = []
    total_n = total_size = 0
    for cid in selected:
        ts = _targets(home_, cid, names)
        if not ts:
            continue
        n, size = _measure(ts)
        plan.append((cid, ts, n, size))
        total_n += n
        total_size += size
    return plan, total_n, total_size


def _report(
    plan: list[tuple[str, list[str], int, int]],
    total_n: int,
    total_size: int,
    state_hits: int,
    keep_state: bool,
) -> None:
    """输出待清理清单与状态记录说明。"""
    for cid, ts, n, size in plan:
        log.info(
            "  %s/result/  [%s]  %d 个文件，%s",
            cid,
            ", ".join(os.path.basename(t) for t in ts),
            n,
            _human(size),
        )
    log.info("合计：%d 个角色，%d 个文件，%s", len(plan), total_n, _human(total_size))
    log.info(
        "状态记录：%s（%d 条）",
        "保留（--keep-state）" if keep_state else "同步清理并重置批次签名",
        state_hits,
    )


def _confirmed(yes: bool) -> bool:
    """交互确认；`yes` 为真时直接通过。"""
    if yes:
        return True
    try:
        ans = input("确认清理？[y/N] ").strip().lower()
    except EOFError:
        ans = ""
    if ans in ("y", "yes"):
        return True
    log.info("已取消。")
    return False


def run(
    char_ids: list[str],
    all_characters: bool = False,
    stories: str = "",
    dry_run: bool = False,
    yes: bool = False,
    keep_state: bool = False,
    data_dir: str | None = None,
) -> ExitCode:
    """清理 `result/` 内容（保留目录本身），返回 `ExitCode`。"""
    home_ = storage.home(data_dir)
    selected, code = _select(home_, char_ids, all_characters)
    if code is not None or selected is None:
        return code or ExitCode.USAGE
    names = [s.strip() for s in stories.split(",") if s.strip()] or None

    plan, total_n, total_size = _build_plan(home_, selected, names)
    state_hits = _clean_state(home_, selected, names, dry_run=True)
    if not plan and not state_hits:
        log.info("没有可清理的翻译结果。")
        return ExitCode.OK

    _report(plan, total_n, total_size, state_hits, keep_state)
    if dry_run:
        log.info("--dry-run：未做任何删除。")
        return ExitCode.OK
    if not _confirmed(yes):
        return ExitCode.OK

    for _, ts, _, _ in plan:
        _remove(ts)
    log.info("已清理 %d 个角色的 result/ 内容（result/ 目录本身保留）。", len(plan))
    if not keep_state:
        hits = _clean_state(home_, selected, names)
        log.info("状态记录：移除 %d 条，并重置批次签名。", hits)
    return ExitCode.OK
