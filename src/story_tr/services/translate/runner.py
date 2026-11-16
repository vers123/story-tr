"""批量翻译执行：参数校验、批次状态、单任务/单角色调度与收尾。"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from story_tr import models, transport
from story_tr.config import Settings, load_settings
from story_tr.core import describe_diff, resolve_languages, signatures_match
from story_tr.errors import (
    BadOutput,
    ExitCode,
    ModelNotLoaded,
    TranslationError,
)

from .. import chain_core, storage, story_inputs
from . import batch_runner, chain_runner, retry, summary
from . import failures as failures_mod
from . import tasks as tasks_mod
from . import tts as tts_mod
from .errors import Abort

log = logging.getLogger("story_tr.services.translate")

TARGET_ZH = "zh-CN"
STATE_FILENAME = ".translate_stories_state.json"
SAVE_EVERY = 10
SAVE_INTERVAL = 5.0
# `_make_plan` 的关键字参数（与 build_plan 的同名局部变量一一对应）
_PLAN_KEYS = (
    "stories",
    "mode",
    "languages",
    "steps",
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
    "on_progress",
    "no_cache",
    "result_tag",
    "batch",
    "reconcile",
    "retry_rounds",
)


@dataclass
class Plan:
    """一次批量翻译的已校验参数。"""

    home: str
    settings: Settings
    selected: list[str]
    providers: list[str]
    langs: list[str]
    chain_label: str
    parts: str
    stories: str
    mode: str
    languages: str
    steps: int
    overwrite: bool
    reset: bool
    revalidate: bool
    no_tts: bool
    fail_fast: bool
    max_failures: int
    workers: int
    balance: bool
    min_interval: float
    src_file: str
    on_progress: Callable[[dict[str, Any]], None] | None = field(default=None)
    no_cache: bool = False
    result_tag: str | None = None
    batch: bool = True
    reconcile: bool = False
    retry_rounds: int = 5


# --------------------------------------------------------------------------- #
# 参数校验
# --------------------------------------------------------------------------- #
def _validate_scope(char_ids: list[str], all_characters: bool) -> ExitCode | None:
    if char_ids and all_characters:
        log.error("不能同时指定角色 id 与 --all-characters，请二选一。")
        return ExitCode.USAGE
    if not char_ids and not all_characters:
        log.error("请指定角色 id / 范围，或用 --all-characters 处理全部角色。")
        return ExitCode.USAGE
    return None


def _select(
    home_: str, char_ids: list[str], all_characters: bool
) -> tuple[list[str] | None, ExitCode | None]:
    char_file = storage.char_index_path(home_)
    if not os.path.exists(char_file):
        log.error("找不到 %s；请先运行 fetch。", char_file)
        return None, ExitCode.USAGE
    all_ids = story_inputs.load_char_ids(home_)
    try:
        selected = (
            all_ids
            if all_characters
            else story_inputs.parse_char_ids(list(char_ids), all_ids, warn_missing=True)
        )
    except ValueError as exc:
        log.error(str(exc))
        return None, ExitCode.USAGE
    if not selected:
        log.error("没有匹配到任何角色 id；请对照 data/character.json 检查。")
        return None, ExitCode.USAGE
    return selected, None


def _validate_request(
    char_ids: list[str], all_characters: bool, tts_only: bool, no_tts: bool
) -> ExitCode | None:
    """校验角色选择与互斥选项。"""
    if tts_only and no_tts:
        log.error("--tts-only 与 --no-tts 互斥，只能二选一。")
        return ExitCode.USAGE
    return _validate_scope(char_ids, all_characters)


def _resolve_route(
    mode: str, chain: str, languages: str, steps: int, provider: str | None
) -> tuple[list[str], list[str], str] | ExitCode:
    """解析后端与语言路线；失败时返回退出码。"""
    providers, err = storage.resolve_providers(provider, mode)
    if err:
        log.error(err)
        return ExitCode.USAGE
    try:
        langs, chain_label = resolve_languages(mode, chain, languages, steps)
    except ValueError as exc:
        log.error(str(exc))
        return ExitCode.USAGE
    return providers, langs, chain_label


def _make_plan(
    home_: str,
    selected: list[str],
    providers: list[str],
    langs: list[str],
    chain_label: str,
    *,
    stories: str,
    mode: str,
    languages: str,
    steps: int,
    workers: int,
    min_interval: float,
    balance: bool,
    full_only: bool,
    segments_only: bool,
    overwrite: bool,
    reset: bool,
    revalidate: bool,
    fail_fast: bool,
    max_failures: int,
    no_tts: bool,
    on_progress: Callable[[dict[str, Any]], None] | None,
    no_cache: bool = False,
    result_tag: str | None = None,
    batch: bool = True,
    reconcile: bool = False,
    retry_rounds: int = 5,
) -> Plan:
    """把已校验的参数组装成 `Plan`。"""
    return Plan(
        home=home_,
        settings=load_settings(),
        selected=selected,
        providers=providers,
        langs=langs,
        chain_label=chain_label,
        parts=tasks_mod.parts_label(full_only, segments_only),
        stories=stories,
        mode=mode,
        languages=languages,
        steps=steps,
        overwrite=overwrite,
        reset=reset,
        revalidate=revalidate,
        no_tts=no_tts,
        fail_fast=fail_fast,
        max_failures=max(1, int(max_failures)),
        workers=max(1, int(workers)),
        balance=balance,
        min_interval=min_interval,
        src_file="en.md" if mode == "rwkv20" else "zh.md",
        on_progress=on_progress,
        no_cache=no_cache,
        result_tag=result_tag,
        batch=batch,
        reconcile=reconcile,
        retry_rounds=max(0, int(retry_rounds)),
    )


def _plan_context(
    char_ids: list[str],
    all_characters: bool,
    mode: str,
    chain: str,
    languages: str,
    steps: int,
    provider: str | None,
    tts_only: bool,
    no_tts: bool,
    data_dir: str | None,
) -> tuple[tuple[str, list[str], list[str], list[str], str] | None, ExitCode | None]:
    """校验选项并解析 `(工作目录, 角色, 后端, 语言, 路线标签)`。"""
    code = _validate_request(char_ids, all_characters, tts_only, no_tts)
    if code is not None:
        return None, code
    route = _resolve_route(mode, chain, languages, steps, provider)
    if isinstance(route, ExitCode):
        return None, route
    providers, langs, chain_label = route
    home_ = storage.home(data_dir)
    selected, code = _select(home_, char_ids, all_characters)
    if code is not None or selected is None:
        return None, code
    return (home_, selected, providers, langs, chain_label), None


def build_plan(
    char_ids: list[str],
    all_characters: bool,
    stories: str,
    mode: str,
    chain: str,
    languages: str,
    steps: int,
    provider: str | None,
    workers: int,
    min_interval: float,
    balance: bool,
    full_only: bool,
    segments_only: bool,
    overwrite: bool,
    reset: bool,
    revalidate: bool,
    fail_fast: bool,
    max_failures: int,
    no_tts: bool,
    tts_only: bool,
    data_dir: str | None,
    on_progress: Callable[[dict[str, Any]], None] | None,
    no_cache: bool = False,
    result_tag: str | None = None,
    batch: bool = True,
    reconcile: bool = False,
    retry_rounds: int = 5,
) -> tuple[Plan | None, ExitCode | None]:
    """校验参数并解析角色选择；失败时返回 `(None, 退出码)`。"""
    try:
        result_tag = storage.validate_result_tag(result_tag)
    except ValueError as exc:
        log.error(str(exc))
        return None, ExitCode.USAGE
    if int(retry_rounds) < 0:
        log.error("--retry-rounds 不能为负数。")
        return None, ExitCode.USAGE
    ctx, code = _plan_context(
        char_ids, all_characters, mode, chain, languages, steps, provider, tts_only, no_tts, data_dir
    )
    if code is not None or ctx is None:
        return None, code
    home_, selected, providers, langs, chain_label = ctx
    # Python < 3.12 的推导式有独立作用域：必须先快照 locals() 再在推导式中取参数，
    # 否则 `locals()` 会返回推导式自身的作用域（3.10/3.11 下 KeyError）。
    frame = locals()
    opts = {k: frame[k] for k in _PLAN_KEYS}
    plan = _make_plan(home_, selected, providers, langs, chain_label, **opts)
    return plan, None


# --------------------------------------------------------------------------- #
# 批次执行
# --------------------------------------------------------------------------- #
def should_skip_task(
    plan: Plan,
    completed: set[str],
    json_path: str,
    state_key: str,
    task: dict[str, Any],
) -> tuple[bool, str]:
    """判断单个任务是否可以跳过（--plan 估算与批量执行共用同一套规则）。

    返回 `(是否跳过, 原因)`，原因为 `exists` / `state` / `stale` / 空字符串。
    """
    exists = os.path.exists(json_path)
    stale = bool(exists and plan.revalidate and tasks_mod.result_is_stale(json_path, task["text"]))
    if plan.overwrite:
        return False, "stale" if stale else ""
    if stale:
        return False, "stale"
    if exists:
        return True, "exists"
    if not plan.no_cache and state_key in completed:
        return True, "state"
    return False, ""


def read_result_row(path: str) -> dict[str, Any] | None:
    """读取结果 JSON（损坏或非对象返回 `None`）。"""
    try:
        with open(path, encoding="utf-8") as f:
            row = json.load(f)
    except (OSError, ValueError):
        return None
    return row if isinstance(row, dict) else None


def disk_completed(plan: Plan) -> tuple[set[str], int]:
    """按磁盘结果重建选中角色的完成集合；返回 `(keys, 忽略数)`。

    只统计与本次 `mode` / `chain` 一致的结果，避免把其他链路的结果算作已完成。
    """
    keys: set[str] = set()
    ignored = 0
    for cid in plan.selected:
        rdir = storage.result_dir(plan.home, cid, plan.result_tag)
        if not os.path.isdir(rdir):
            continue
        for story in sorted(os.listdir(rdir)):
            story_dir = os.path.join(rdir, story)
            if not os.path.isdir(story_dir):
                continue
            for name in sorted(os.listdir(story_dir)):
                stem, ext = os.path.splitext(name)
                if ext != ".json" or stem == "segments":
                    continue
                row = read_result_row(os.path.join(story_dir, name))
                if row is None or row.get("mode") != plan.mode or row.get("chain") != plan.chain_label:
                    ignored += 1
                    continue
                keys.add(f"{cid}|{story}|{stem}")
    return keys, ignored


class _Batch:
    """一次批量翻译的运行时状态与调度。"""

    def __init__(
        self,
        plan: Plan,
        translators: list[transport.Translator],
        state: models.BatchState,
        completed: set[str],
    ) -> None:
        self.plan = plan
        self.translators = translators
        self.state = state
        self.completed = completed
        self.state_path = os.path.join(plan.home, STATE_FILENAME)
        self.state_lock = threading.Lock()
        self.done_lock = threading.Lock()
        self.failures: dict[str, dict[str, Any]] = {}
        self.stats = {"n": 0}
        self.save_counter = 0
        self.save_last = time.monotonic()
        self.rounds = 0
        self.retrying = False
        self.started_at = datetime.now().isoformat(timespec="seconds")
        self.provider_label = plan.providers[0] if len(plan.providers) == 1 else " → ".join(plan.providers)
        self.mode_label = plan.mode + ("/" + plan.chain_label if plan.mode == "fixed" else "")
        self.batch_enabled = bool(
            plan.batch
            and plan.mode != "rwkv20"
            and chain_core.batch_translator(translators, plan.providers, plan.balance) is not None
        )

    @classmethod
    def create(
        cls, plan: Plan, translators: list[transport.Translator]
    ) -> tuple[_Batch | None, ExitCode | None]:
        """加载续跑状态；签名不一致时要求 `--reset`。"""
        sig = plan_signature(plan)
        raw = storage.load_json_with_backup(os.path.join(plan.home, STATE_FILENAME))
        prev_state = models.BatchState.model_validate(raw) if raw else None
        if prev_state and not plan.reset and not signatures_match(prev_state.signature, sig):
            log.error(
                "参数与上次批量任务不一致，无法续跑（会与已有进度冲突）；如需按新参数重跑，请加 --reset。"
            )
            log.error("差异：\n%s", describe_diff(prev_state.signature, sig))
            return None, ExitCode.SIGNATURE
        completed = set(prev_state.completed) if (prev_state and not plan.reset) else set()
        started_at = prev_state.started_at if prev_state else datetime.now().isoformat(timespec="seconds")
        state = models.BatchState(signature=sig, completed=sorted(completed), started_at=started_at)
        return cls(plan, translators, state, completed), None

    # ---- 基础设施 ----
    def emit(self, **kw: Any) -> None:
        if self.plan.on_progress is not None:
            with contextlib.suppress(Exception):
                self.plan.on_progress(kw)

    def save(self, force: bool = True) -> None:
        """保存批次状态；非强制时按「每 N 条 / 每 T 秒」节流。"""
        with self.state_lock:
            self.save_counter += 1
            now = time.monotonic()
            if not force and self.save_counter < SAVE_EVERY and now - self.save_last < SAVE_INTERVAL:
                return
            self.save_counter = 0
            self.save_last = now
            self.state.updated_at = datetime.now().isoformat(timespec="seconds")
            self.state.completed = sorted(self.completed)
            storage.atomic_write_json(self.state_path, self.state)

    # ---- 状态对账 ----
    def reconcile(self) -> None:
        """以磁盘结果为准重建选中角色的完成记录（不删除任何结果文件）。"""
        disk, ignored = disk_completed(self.plan)
        if not disk and not os.path.exists(self.state_path):
            log.info("对账：磁盘上没有可识别的结果，未改动状态。")
            return
        selected = set(self.plan.selected)
        before = set(self.completed)
        kept = {k for k in before if k.split("|", 1)[0] not in selected}
        self.completed = kept | disk
        self.state.completed = sorted(self.completed)
        self.state.updated_at = datetime.now().isoformat(timespec="seconds")
        with self.state_lock:
            storage.atomic_write_json(self.state_path, self.state)
        log.info(
            "对账：%d 条记录 → %d 条（新增 %d / 移除 %d / 忽略 %d 个不可用结果）",
            len(before),
            len(self.completed),
            len(disk - before),
            len(before - self.completed),
            ignored,
        )

    # ---- 单任务 ----
    def _should_skip(self, json_path: str, state_key: str, task: dict[str, Any]) -> bool:
        with self.state_lock:
            skip, reason = should_skip_task(self.plan, self.completed, json_path, state_key, task)
            if reason == "stale":
                log.info("  重新翻译（源文已变化）%s/%s", task["story"], task["key"])
        return skip

    def _failure_key(self, cid: str, task: dict[str, Any]) -> str:
        return "{}|{}|{}".format(cid, task["story"], task["key"])

    def _record_failure(self, cid: str, task: dict[str, Any], stage: str, exc: Exception) -> None:
        """登记（或累加）一条失败记录：`stage` 为 `translate` / `tts`。"""
        key = self._failure_key(cid, task)
        stamp = failures_mod.now()
        with self.done_lock:
            record = self.failures.get(key)
            if record is None:
                self.failures[key] = failures_mod.record_of(
                    cid, task["story"], task["key"], stage, exc, stamp
                )
            else:
                record["stage"] = stage
                record["error"] = f"{type(exc).__name__}: {exc}"
                record["attempts"] = int(record["attempts"]) + 1
                record["last_at"] = stamp
        log.error("失败 %s/%s（%s）：%s；已登记，稍后重试。", cid, task["story"], task["key"], exc)

    def on_translation_failure(self, cid: str, task: dict[str, Any], exc: Exception) -> None:
        """翻译失败：可重试错误记失败继续；模型未加载立即中止。"""
        if isinstance(exc, ModelNotLoaded):
            raise Abort(ExitCode.PAUSED, str(exc)) from None
        self._record_failure(cid, task, "translate", exc)
        if self.plan.fail_fast and not self.retrying:
            raise Abort(ExitCode.PARTIAL, str(exc)) from None

    def _translate(self, task: dict[str, Any], cid: str, base: str) -> models.TranslationResult | None:
        try:
            result = chain_runner.run_chain(
                task["text"],
                self.plan.langs,
                self.translators,
                self.plan.providers,
                self.plan.balance,
                self.plan.settings,
                source_lang=TARGET_ZH,
            )
        except TranslationError as exc:
            self.on_translation_failure(cid, task, exc)
            return None
        return self._result_from_chain(task, cid, result, base)

    def _result_from_chain(
        self, task: dict[str, Any], cid: str, result: dict[str, Any], base: str
    ) -> models.TranslationResult:
        return models.TranslationResult(
            source=result["source"],
            source_lang=result["source_lang"],
            target_lang=result["target_lang"],
            mode=self.plan.mode,
            providers=chain_core.providers_of(result["steps"]),
            languages=result["languages"],
            steps=result["steps"],
            final=result["final"],
            chain=self.plan.chain_label,
            passthrough=result.get("passthrough", False),
            rwkv=result.get("rwkv"),
            story=task["story"],
            segment_index=task["index"],
            source_file=storage.rel(self.plan.home, os.path.join(base, task["story"], self.plan.src_file)),
            started_at=result.get("started_at"),
            finished_at=result.get("finished_at"),
        )

    def _make_tts(self, tr: models.TranslationResult, cid: str, task: dict[str, Any], mp3_path: str) -> None:
        try:
            audio = tts_mod.tts_with_retry(tr.final, self.plan.settings)
            with open(mp3_path, "wb") as f:
                f.write(audio)
        except Exception as exc:  # noqa: BLE001 - 语音失败不影响翻译结果，登记后重试
            self._record_failure(cid, task, "tts", exc)
            log.warning("语音生成失败（%s/%s/%s）：%s", cid, task["story"], task["key"], exc)

    def _write_result(self, tr: models.TranslationResult, task: dict[str, Any], cid: str) -> None:
        d = os.path.join(storage.result_dir(self.plan.home, cid, self.plan.result_tag), task["story"])
        os.makedirs(d, exist_ok=True)
        storage.atomic_write_json(os.path.join(d, task["key"] + ".json"), tr)
        if not self.plan.no_tts and not tr.passthrough:
            self._make_tts(tr, cid, task, os.path.join(d, task["key"] + ".mp3"))
        with self.state_lock:
            self.completed.add("{}|{}|{}".format(cid, task["story"], task["key"]))
        with self.done_lock:
            self.stats["n"] += 1
        log.info("[%s] %s/%s  %d 字", cid, task["story"], task["key"], len(tr.final))

    def on_success(self, task: dict[str, Any], cid: str, result: dict[str, Any], base: str) -> None:
        """批量路径的结果落盘入口（与单任务路径共用 `_write_result`）。"""
        self._write_result(self._result_from_chain(task, cid, result, base), task, cid)

    def process_task(self, task: dict[str, Any], cid: str, base: str) -> None:
        d = os.path.join(storage.result_dir(self.plan.home, cid, self.plan.result_tag), task["story"])
        json_path = os.path.join(d, task["key"] + ".json")
        state_key = "{}|{}|{}".format(cid, task["story"], task["key"])
        if self._should_skip(json_path, state_key, task):
            with self.done_lock:
                self.stats["n"] += 1
            return
        tr = self._translate(task, cid, base)
        if tr is not None:
            self._write_result(tr, task, cid)

    # ---- 单角色 ----
    def _progress(self, done: int, cid: str, char_index: int, task_list: list[dict[str, Any]]) -> None:
        self.emit(
            event="progress",
            char_id=cid,
            char_index=char_index,
            chars=len(self.plan.selected),
            tasks=len(task_list),
            done=done,
        )

    def _run_tasks(
        self, task_list: list[dict[str, Any]], cid: str, char_index: int, base: str, cancel: Any
    ) -> None:
        if self.plan.workers > 1:
            with ThreadPoolExecutor(max_workers=self.plan.workers) as ex:
                futs = [ex.submit(self.process_task, t, cid, base) for t in task_list]
                for done, fut in enumerate(as_completed(futs), 1):
                    fut.result()  # 可能抛 Abort
                    self.save(force=False)
                    self._progress(done, cid, char_index, task_list)
            return
        for done, task in enumerate(task_list, 1):
            if cancel is not None and cancel.is_set():
                raise Abort(ExitCode.CANCELLED)
            self.process_task(task, cid, base)
            self.save(force=False)
            self._progress(done, cid, char_index, task_list)

    def _run_tasks_batched(
        self, task_list: list[dict[str, Any]], cid: str, char_index: int, base: str, cancel: Any
    ) -> None:
        pending: list[dict[str, Any]] = []
        for task in task_list:
            d = os.path.join(storage.result_dir(self.plan.home, cid, self.plan.result_tag), task["story"])
            json_path = os.path.join(d, task["key"] + ".json")
            state_key = "{}|{}|{}".format(cid, task["story"], task["key"])
            if self._should_skip(json_path, state_key, task):
                with self.done_lock:
                    self.stats["n"] += 1
                continue
            pending.append(task)
        if pending:
            batch_runner.run_story(
                self.plan, self.translators, self.plan.providers, cid, pending, self, cancel
            )
        self.save(force=False)
        self._progress(len(task_list), cid, char_index, task_list)

    def _write_summaries(self, task_list: list[dict[str, Any]], cid: str) -> None:
        result_dir = storage.result_dir(self.plan.home, cid, self.plan.result_tag)
        done_stories = {
            t["story"]
            for t in task_list
            if os.path.exists(os.path.join(result_dir, t["story"], t["key"] + ".json"))
        }
        for story in done_stories:
            summary.write_summary(
                self.plan.home,
                cid,
                story,
                self.provider_label,
                self.mode_label,
                self.plan.src_file,
                self.plan.result_tag,
            )

    def run_char(self, cid: str, char_index: int, cancel: Any) -> None:
        if cancel is not None and cancel.is_set():
            raise Abort(ExitCode.CANCELLED)
        base = storage.profile_story_dir(self.plan.home, cid)
        names = tasks_mod.story_names(self.plan.home, cid, self.plan.stories, self.plan.src_file)
        task_list = (
            tasks_mod.build_tasks(self.plan.home, cid, names, self.plan.parts, self.plan.src_file)
            if names
            else []
        )
        if not task_list:
            log.warning("跳过 %s：没有可处理的故事", cid)
            return
        log.info(
            "角色 %s，故事 %s，共 %d 份结果（%s）",
            cid,
            ",".join(names),
            len(task_list),
            self.mode_label,
        )
        self.emit(
            event="char_start",
            char_id=cid,
            char_index=char_index,
            chars=len(self.plan.selected),
            tasks=len(task_list),
            done=0,
        )
        if self.batch_enabled:
            if self.plan.workers > 1:
                log.warning("批量模式下 --workers 不生效（按故事串行）；可用 --no-batch 恢复并发。")
            self._run_tasks_batched(task_list, cid, char_index, base, cancel)
        else:
            self._run_tasks(task_list, cid, char_index, base, cancel)
        self._write_summaries(task_list, cid)
        self.emit(
            event="char_done",
            char_id=cid,
            char_index=char_index,
            chars=len(self.plan.selected),
            tasks=len(task_list),
            done=len(task_list),
        )
        self.save()  # 角色边界强制落盘，缩小异常退出时的进度丢失窗口
        if self.failures and len(self.failures) >= self.plan.max_failures:
            log.error(
                "失败任务达到上限 %d（--max-failures），提前结束；已完成进度已保存。",
                self.plan.max_failures,
            )
            raise Abort(ExitCode.PARTIAL)

    # ---- 失败登记与重试 ----
    def prepare_failures(self) -> None:
        """归档上次遗留的失败登记（不跨运行自动继承）。"""
        failures_mod.archive_registry(self.plan.home)

    def _registry(self) -> models.FailureRegistry:
        return failures_mod.build_registry(
            plan_signature(self.plan),
            self.plan.result_tag,
            self.started_at,
            self.rounds,
            self.failures,
        )

    def on_round_done(self, round_no: int) -> None:
        """一轮结束：落盘失败清单、登记文件与批次状态。"""
        self.rounds = round_no
        if self.failures:
            failures_mod.round_logs(self.plan.home, self.failures, round_no)
            failures_mod.save_registry(self.plan.home, self._registry())
        else:
            failures_mod.remove_registry(self.plan.home)
        self.save(force=True)

    def persist_failures(self) -> None:
        """非重试类中止（取消 / 模型未加载）时保存当前失败登记。"""
        if self.failures:
            self.on_round_done(self.rounds)

    def _find_task(self, cid: str, story: str, key: str) -> dict[str, Any] | None:
        names = tasks_mod.story_names(self.plan.home, cid, story, self.plan.src_file)
        tasks = tasks_mod.build_tasks(self.plan.home, cid, names, self.plan.parts, self.plan.src_file)
        for task in tasks:
            if task["key"] == key:
                return task
        return None

    def retry_one(self, record: dict[str, Any]) -> None:
        """重试一条失败记录：翻译失败重译整条，TTS 失败只补语音。"""
        if record["stage"] == "tts":
            self.retry_tts(record)
        else:
            self.retry_translate(record)

    def retry_translate(self, record: dict[str, Any]) -> None:
        cid = record["char_id"]
        task = self._find_task(cid, record["story"], record["key"])
        if task is None:
            self._record_failure(cid, record, "translate", RuntimeError("任务已不存在（数据可能已更新）"))
            return
        base = storage.profile_story_dir(self.plan.home, cid)
        self.retrying = True
        try:
            tr = self._translate(task, cid, base)
        finally:
            self.retrying = False
        if tr is None:
            return
        self.failures.pop(self._failure_key(cid, task), None)
        self._write_result(tr, task, cid)
        self._write_summaries([task], cid)

    def retry_tts(self, record: dict[str, Any]) -> None:
        cid = record["char_id"]
        d = os.path.join(storage.result_dir(self.plan.home, cid, self.plan.result_tag), record["story"])
        json_path = os.path.join(d, record["key"] + ".json")
        if not os.path.exists(json_path):
            self.retry_translate(record)
            return
        try:
            row = read_result_row(json_path) or {}
            text = (row.get("final") or "").strip()
            if row.get("passthrough") or not text:
                raise BadOutput("结果没有可朗读文本")
            audio = tts_mod.tts_with_retry(text, self.plan.settings)
            with open(os.path.join(d, record["key"] + ".mp3"), "wb") as f:
                f.write(audio)
        except Exception as exc:  # noqa: BLE001 - 失败继续登记等待下一轮
            self._record_failure(cid, record, "tts", exc)
            return
        self.failures.pop(self._failure_key(cid, record), None)
        self._write_summaries([record], cid)

    # ---- 收尾 ----
    def finish(self) -> ExitCode:
        if self.failures:
            log.error(
                "完成 %d 份结果，仍失败 %d 项（登记 %s；已重试 %d 轮）。",
                self.stats["n"],
                len(self.failures),
                failures_mod.FAILURE_FILENAME,
                self.rounds,
            )
            return ExitCode.PARTIAL
        failures_mod.remove_registry(self.plan.home)
        storage.remove_with_backup(self.state_path)
        log.info("完成。处理 %d 个角色、%d 份结果。", len(self.plan.selected), self.stats["n"])
        return ExitCode.OK


def plan_signature(plan: Plan) -> dict[str, Any]:
    """批次签名：参数相同则相同，用于续跑校验（含结果目录 tag）。

    `result_tag` 仅在非空时加入签名，保证旧状态文件（无该键）仍可续跑。
    """
    sig = chain_core.signature(
        plan.mode, plan.chain_label, plan.languages, plan.steps, plan.providers, plan.settings
    )
    if plan.result_tag:
        sig["result_tag"] = plan.result_tag
    return sig


def _build_translators(plan: Plan) -> list[transport.Translator]:
    translators = storage.build_translators(plan.providers, plan.settings)
    for tr in translators:
        tr.min_interval = max(  # type: ignore[attr-defined]
            float(getattr(tr, "min_interval", 0.0) or 0.0), float(plan.min_interval)
        )
    return translators


def preflight_rwkv(translators: list[transport.Translator]) -> ExitCode | None:
    """本地 RWKV 启动预检：不可达 → 5；模型未加载（Offline）→ 4；其余继续。

    只对带 `probe()` 的本地后端生效，因此测试替身与云端后端会被自然跳过。
    """
    for translator in translators:
        if getattr(translator, "name", None) != "rwkv":
            continue
        probe = getattr(translator, "probe", None)
        if not callable(probe):
            continue
        try:
            info = probe()
        except Exception as exc:  # noqa: BLE001 - 探测失败按连接失败快速结束
            log.error("本地 RWKV 服务探测失败：%s；请启动 RWKV-Runner 并检查 RWKV_API_URL。", exc)
            return ExitCode.CONNECTION
        if not info.get("reachable"):
            detail = info.get("error") or "未知错误"
            log.error("本地 RWKV 服务不可达（%s）；请启动 RWKV-Runner 后重试。", detail)
            return ExitCode.CONNECTION
        status = info.get("status")
        if status == "Offline":
            log.error("本地 RWKV 模型未加载（服务可达但状态 Offline）；请在 RWKV-Runner 中加载模型后重试。")
            return ExitCode.PAUSED
        log.info("本地 RWKV 预检通过（状态 %s，模型 %s）。", status or "未知", info.get("model") or "未知")
        return None
    return None


def run_batch(plan: Plan, cancel: Any) -> ExitCode:
    translators = _build_translators(plan)
    batch, code = _Batch.create(plan, translators)
    if code is not None or batch is None:
        return code or ExitCode.OK
    if plan.reconcile:
        batch.reconcile()
    batch.prepare_failures()
    code = preflight_rwkv(translators)
    if code is not None:
        return code
    try:
        for char_index, cid in enumerate(plan.selected, 1):
            batch.run_char(cid, char_index, cancel)
    except Abort as exc:
        batch.save()
        if exc.code != ExitCode.PARTIAL:
            log.warning("已中止：%s", exc.reason)
            batch.persist_failures()
            return exc.code
        log.warning("首轮提前结束：%s；仍会进入自动重试。", exc.reason)
    if batch.failures:
        batch.on_round_done(0)
        if plan.retry_rounds > 0 and not (cancel is not None and cancel.is_set()):
            try:
                retry.run_rounds(
                    batch.failures,
                    batch.retry_one,
                    plan.retry_rounds,
                    cancel,
                    batch.on_round_done,
                )
            except Abort as exc:
                batch.save()
                batch.persist_failures()
                log.warning("重试已中止：%s", exc.reason)
                return exc.code
    return batch.finish()
