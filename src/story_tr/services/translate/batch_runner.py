"""批量翻译：把一个故事的全部任务按链路步骤锁步批量执行。

同一故事的所有任务共享同一条语言链，同一步的 src/tgt 相同，因此可以把多个任务的
当前文本打包进一次批量请求（只对支持批量的后端生效，目前是 Google）。批量请求
失败时回退到逐条翻译，保持与单任务路径一致的错误语义。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from story_tr import models
from story_tr.core import build_tasks, has_features, split_chunks
from story_tr.errors import BadOutput, ExitCode, TranslationError

from .. import chain_core, storage
from . import chain_runner
from .errors import Abort

log = logging.getLogger("story_tr.services.translate.batch")

TARGET_ZH = "zh-CN"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _packages(
    items: list[tuple[str, int, str]], max_chars: int, max_items: int
) -> list[list[tuple[str, int, str]]]:
    """按总字符数 / 条数把 (key, chunk_index, text) 切成若干请求包。"""
    packages: list[list[tuple[str, int, str]]] = []
    current: list[tuple[str, int, str]] = []
    chars = 0
    for item in items:
        size = len(item[2])
        if current and (chars + size > max_chars or len(current) >= max_items):
            packages.append(current)
            current = []
            chars = 0
        current.append(item)
        chars += size
    if current:
        packages.append(current)
    return packages


def _split_items(texts: dict[str, str], max_chars: int) -> list[tuple[str, int, str]]:
    """把任务文本切成不超过单条上限的块，返回 ``(key, chunk_index, chunk)`` 列表。"""
    items: list[tuple[str, int, str]] = []
    for key, text in texts.items():
        for index, chunk in enumerate(split_chunks(text, max_chars)):
            items.append((key, index, chunk))
    return items


def _join_pieces(pieces: dict[str, list[tuple[int, str]]]) -> dict[str, str]:
    """按块序拼接每个任务的译文（与后端内部按块拼接语义一致）。"""
    result: dict[str, str] = {}
    for key, parts in pieces.items():
        joined = "".join(text for _, text in sorted(parts)).strip()
        if not joined:
            raise BadOutput("批量翻译返回空结果")
        result[key] = joined
    return result


def batch_translate(translator: Any, texts: dict[str, str], src: str, tgt: str) -> dict[str, str]:
    """用批量后端翻译一组文本，返回 ``key -> 译文``。"""
    max_chars = int(getattr(translator, "max_batch_chars", 1500))
    max_items = int(getattr(translator, "max_batch_items", 32))
    packages = _packages(_split_items(texts, max_chars), max_chars, max_items)
    pieces: dict[str, list[tuple[int, str]]] = {key: [] for key in texts}
    for package in packages:
        outs = translator.translate_batch([item[2] for item in package], src, tgt)
        if not isinstance(outs, list) or len(outs) != len(package):
            raise BadOutput("批量翻译返回数量与请求不符")
        for (key, chunk_index, _chunk), out in zip(package, outs, strict=False):
            pieces[key].append((chunk_index, (out or "").strip()))
    return _join_pieces(pieces)


def _translate_step(
    plan: Any,
    translators: list[Any],
    providers: list[str],
    alive: dict[str, dict[str, Any]],
    step: dict[str, Any],
    index: int,
    cid: str,
    hooks: Any,
) -> None:
    """执行链路中的一步：优先批量，失败回退逐条。"""
    texts = {key: item["text"] for key, item in alive.items()}
    pair = chain_core.batch_translator(translators, providers, plan.balance)
    if pair is not None:
        name, translator = pair
        try:
            outs = batch_translate(translator, texts, str(step["src"]), str(step["tgt"]))
        except TranslationError as exc:
            log.warning("[%s] 批量翻译失败，回退逐条：%s", cid, exc)
        else:
            for key, out in outs.items():
                _append_step(alive[key], step, index, name, out)
            return
    _per_item(plan, translators, providers, alive, step, index, cid, hooks)


def _append_step(
    item: dict[str, Any], step: dict[str, Any], index: int, provider: str, text: str, meta: Any = None
) -> None:
    item["text"] = text
    item["steps"].append(
        models.TranslationStep(
            index=index,
            kind=str(step["kind"]),
            lang=str(step["lang"]),
            provider=provider,
            text=text,
            rwkv=meta,
        )
    )


def _per_item(
    plan: Any,
    translators: list[Any],
    providers: list[str],
    alive: dict[str, dict[str, Any]],
    step: dict[str, Any],
    index: int,
    cid: str,
    hooks: Any,
) -> None:
    """逐条回退：失败任务记录后移出后续步骤，其余任务继续。"""
    for key in list(alive):
        item = alive[key]
        try:
            name, out, meta = chain_core.translate_step(
                item["text"],
                str(step["src"]),
                str(step["tgt"]),
                translators,
                providers,
                plan.balance,
                plan.settings,
            )
        except TranslationError as exc:
            hooks.on_translation_failure(cid, item["task"], exc)
            del alive[key]
            continue
        _append_step(item, step, index, name, out, meta)


def _prepare(
    plan: Any,
    translators: list[Any],
    providers: list[str],
    cid: str,
    task_list: list[dict[str, Any]],
    hooks: Any,
    base: str,
) -> dict[str, dict[str, Any]]:
    """分离 passthrough 任务（直接出结果），其余任务进入锁步批量。"""
    alive: dict[str, dict[str, Any]] = {}
    for task in task_list:
        if not has_features(task["text"]):
            result = chain_runner.run_chain(
                task["text"],
                plan.langs,
                translators,
                providers,
                plan.balance,
                plan.settings,
                source_lang=TARGET_ZH,
            )
            hooks.on_success(task, cid, result, base)
            continue
        alive[task["key"]] = {"task": task, "text": task["text"], "steps": [], "started_at": _now()}
    return alive


def _chain_result(plan: Any, item: dict[str, Any]) -> dict[str, Any]:
    """拼装与 `chain_runner.run_chain` 相同结构的结果字典。"""
    return {
        "source": item["task"]["text"],
        "source_lang": TARGET_ZH,
        "target_lang": TARGET_ZH,
        "languages": list(plan.langs),
        "steps": item["steps"],
        "final": item["text"],
        "passthrough": False,
        "rwkv": chain_core.merge_rwkv(item["steps"]),
        "started_at": item["started_at"],
        "finished_at": _now(),
    }


def run_story(
    plan: Any,
    translators: list[Any],
    providers: list[str],
    cid: str,
    task_list: list[dict[str, Any]],
    hooks: Any,
    cancel: Any = None,
) -> None:
    """对单个故事的任务做锁步批量翻译；每个任务完成后交给 hooks 落盘。"""
    if not task_list:
        return
    base = storage.profile_story_dir(plan.home, cid)
    alive = _prepare(plan, translators, providers, cid, task_list, hooks, base)
    if not alive:
        return
    for index, step in enumerate(build_tasks(TARGET_ZH, plan.langs)):
        if cancel is not None and cancel.is_set():
            raise Abort(ExitCode.CANCELLED)
        _translate_step(plan, translators, providers, alive, step, index, cid, hooks)
        if not alive:
            return
    for item in alive.values():
        hooks.on_success(item["task"], cid, _chain_result(plan, item), base)
