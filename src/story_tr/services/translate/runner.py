"""批量翻译执行：参数校验、批次状态、单任务/单角色调度与收尾。"""

from __future__ import annotations

import contextlib
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
    ConnectionFailed,
    ExitCode,
    ModelNotLoaded,
    QuotaExceeded,
    RateLimited,
    UnsupportedLanguage,
)

from .. import chain_core, storage, story_inputs
from . import chain_runner, summary
from . import tasks as tasks_mod
from . import tts as tts_mod
from .errors import Abort

log = logging.getLogger("story_tr.services.translate")

TARGET_ZH = "zh-CN"
STATE_FILENAME = ".translate_stories_state.json"
SAVE_EVERY = 10
SAVE_INTERVAL = 5.0


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
) -> tuple[Plan | None, ExitCode | None]:
    """校验参数并解析角色选择；失败时返回 `(None, 退出码)`。"""
    ctx, code = _plan_context(
        char_ids, all_characters, mode, chain, languages, steps, provider, tts_only, no_tts, data_dir
    )
    if code is not None or ctx is None:
        return None, code
    home_, selected, providers, langs, chain_label = ctx
    plan = _make_plan(
        home_,
        selected,
        providers,
        langs,
        chain_label,
        stories=stories,
        mode=mode,
        languages=languages,
        steps=steps,
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
        on_progress=on_progress,
    )
    return plan, None


# --------------------------------------------------------------------------- #
# 批次执行
# --------------------------------------------------------------------------- #
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
        self.failures: list[dict[str, Any]] = []
        self.tts_failures: list[dict[str, Any]] = []
        self.stats = {"n": 0}
        self.save_counter = 0
        self.save_last = time.monotonic()
        self.provider_label = plan.providers[0] if len(plan.providers) == 1 else " → ".join(plan.providers)
        self.mode_label = plan.mode + ("/" + plan.chain_label if plan.mode == "fixed" else "")

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

    # ---- 单任务 ----
    def _should_skip(self, json_path: str, state_key: str, task: dict[str, Any]) -> bool:
        with self.state_lock:
            exists = os.path.exists(json_path)
            stale = bool(
                exists and self.plan.revalidate and tasks_mod.result_is_stale(json_path, task["text"])
            )
            if stale:
                log.info("  重新翻译（源文已变化）%s/%s", task["story"], task["key"])
            if not self.plan.overwrite and not stale and (exists or state_key in self.completed):
                return True
        return False

    def _record_failure(self, cid: str, task: dict[str, Any], exc: BadOutput) -> None:
        with self.done_lock:
            self.failures.append(
                {
                    "char_id": cid,
                    "stories": [task["story"]],
                    "keys": [task["key"]],
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
        log.error("失败 %s/%s（%s）：%s；已记录，继续后续任务。", cid, task["story"], task["key"], exc)

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
        except QuotaExceeded as exc:
            raise Abort(ExitCode.QUOTA, str(exc)) from None
        except UnsupportedLanguage as exc:
            raise Abort(ExitCode.UNSUPPORTED_LANGUAGE, str(exc)) from None
        except ModelNotLoaded as exc:
            raise Abort(ExitCode.PAUSED, str(exc)) from None
        except ConnectionFailed as exc:
            raise Abort(ExitCode.CONNECTION, str(exc)) from None
        except RateLimited as exc:
            raise Abort(ExitCode.PAUSED, str(exc)) from None
        except BadOutput as exc:
            self._record_failure(cid, task, exc)
            if self.plan.fail_fast:
                raise Abort(ExitCode.PARTIAL, str(exc)) from None
            return None
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
        except Exception as exc:  # noqa: BLE001 - 语音失败不影响翻译结果
            with self.done_lock:
                self.tts_failures.append(
                    {
                        "stage": "tts",
                        "char_id": cid,
                        "stories": [task["story"]],
                        "keys": [task["key"]],
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
            log.warning("语音生成失败（%s/%s/%s）：%s", cid, task["story"], task["key"], exc)

    def process_task(self, task: dict[str, Any], cid: str, base: str) -> None:
        d = os.path.join(storage.result_dir(self.plan.home, cid), task["story"])
        os.makedirs(d, exist_ok=True)
        json_path = os.path.join(d, task["key"] + ".json")
        state_key = "{}|{}|{}".format(cid, task["story"], task["key"])

        if self._should_skip(json_path, state_key, task):
            with self.done_lock:
                self.stats["n"] += 1
            return
        tr = self._translate(task, cid, base)
        if tr is None:
            return
        storage.atomic_write_json(json_path, tr)
        if not self.plan.no_tts and not tr.passthrough:
            self._make_tts(tr, cid, task, os.path.join(d, task["key"] + ".mp3"))
        with self.state_lock:
            self.completed.add(state_key)
        with self.done_lock:
            self.stats["n"] += 1
        log.info("[%s] %s/%s  %d 字", cid, task["story"], task["key"], len(tr.final))

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

    def _write_summaries(self, task_list: list[dict[str, Any]], cid: str) -> None:
        result_dir = storage.result_dir(self.plan.home, cid)
        done_stories = {
            t["story"]
            for t in task_list
            if os.path.exists(os.path.join(result_dir, t["story"], t["key"] + ".json"))
        }
        for story in done_stories:
            summary.write_summary(
                self.plan.home, cid, story, self.provider_label, self.mode_label, self.plan.src_file
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

    # ---- 收尾 ----
    def _report_tts_failures(self) -> None:
        if not self.tts_failures:
            return
        path = summary.write_failures(self.plan.home, self.tts_failures, prefix="tts_failures")
        ids = ",".join(sorted({str(f["char_id"]) for f in self.tts_failures}))
        log.warning(
            "有 %d 项语音未生成（已记录）；可用 `translate %s --tts-only` 补跑。清单：%s",
            len(self.tts_failures),
            ids,
            storage.rel(self.plan.home, path),
        )

    def finish(self) -> ExitCode:
        if self.failures:
            path = summary.write_failures(self.plan.home, self.failures)
            log.info(
                "完成 %d 份结果，失败 %d 项（已记录）。失败清单：%s",
                self.stats["n"],
                len(self.failures),
                storage.rel(self.plan.home, path),
            )
            return ExitCode.PARTIAL
        storage.remove_with_backup(self.state_path)
        self._report_tts_failures()
        log.info("完成。处理 %d 个角色、%d 份结果。", len(self.plan.selected), self.stats["n"])
        return ExitCode.OK


def plan_signature(plan: Plan) -> dict[str, Any]:
    """批次签名：参数相同则相同，用于续跑校验。"""
    return chain_core.signature(
        plan.mode, plan.chain_label, plan.languages, plan.steps, plan.providers, plan.settings
    )


def _build_translators(plan: Plan) -> list[transport.Translator]:
    translators = storage.build_translators(plan.providers, plan.settings)
    for tr in translators:
        tr.min_interval = max(  # type: ignore[attr-defined]
            float(getattr(tr, "min_interval", 0.0) or 0.0), float(plan.min_interval)
        )
    return translators


def run_batch(plan: Plan, cancel: Any) -> ExitCode:
    translators = _build_translators(plan)
    batch, code = _Batch.create(plan, translators)
    if code is not None or batch is None:
        return code or ExitCode.OK
    try:
        for char_index, cid in enumerate(plan.selected, 1):
            batch.run_char(cid, char_index, cancel)
    except Abort as exc:
        batch.save()
        log.warning("已中止：%s", exc.reason)
        return exc.code
    return batch.finish()
