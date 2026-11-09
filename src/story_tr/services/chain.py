"""单文本「来回翻译 N 次」链路的编排（供 `story-tr chain` 使用）。

- detect 语言 → resolve 路线 → 逐步翻译（`core.routes.build_tasks`）
- 结果写 `out/{ts}.json`（`TranslationResult`）+ 可选 mp3
- `.translate_state.json` 续跑（`ChainState`），签名不一致要求 `--reset`
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from story_tr import models, transport
from story_tr.config import Settings, load_settings
from story_tr.core import (
    build_tasks,
    describe_diff,
    detect_language,
    has_han,
    lang_label,
    resolve_languages,
    signatures_match,
)
from story_tr.errors import (
    BadOutput,
    ConnectionFailed,
    ExitCode,
    ModelNotLoaded,
    QuotaExceeded,
    RateLimited,
    UnsupportedLanguage,
)
from story_tr.transport.tts import tts_bytes

from . import chain_core, storage

log = logging.getLogger("story_tr.services.chain")

TARGET_ZH = "zh-CN"
STATE_FILENAME = ".translate_state.json"

# 共享链路实现（内部调用点直接使用这些名字）
_merge_rwkv = chain_core.merge_rwkv
_providers_of = chain_core.providers_of
_translate_step = chain_core.translate_step


def _signature(
    text: str,
    detected: str,
    mode: str,
    chain: str,
    languages: str,
    steps: int,
    providers: list[str],
    settings: Settings,
) -> dict[str, Any]:
    sig = chain_core.signature(mode, chain, languages, steps, providers, settings)
    sig["text"] = text
    sig["detected"] = detected
    return sig


def _setup_file_log(home_: str, no_file_log: bool) -> logging.Handler | None:
    if no_file_log:
        return None
    os.makedirs(storage.logs_dir(home_), exist_ok=True)
    path = os.path.join(storage.logs_dir(home_), datetime.now().strftime("%Y%m%d_%H%M%S") + ".log")
    handler: logging.Handler = logging.FileHandler(path, encoding="utf-8")
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    return handler


@dataclass
class _Context:
    """一次单文本链路运行的不可变上下文。"""

    home: str
    settings: Settings
    text: str
    detected: str
    mode: str
    chain_label: str
    langs: list[str]
    providers: list[str]
    balance: bool
    translators: list[transport.Translator]
    state_path: str
    no_tts: bool


def _prepare(
    text: str,
    mode: str,
    chain: str,
    languages: str,
    steps: int,
    provider: str | None,
    min_interval: float,
    balance: bool,
    no_tts: bool,
    data_dir: str | None,
) -> tuple[_Context | None, ExitCode | None]:
    """解析路线、构造后端并记录结果预览；失败返回退出码。"""
    home_ = storage.home(data_dir)
    settings = load_settings()
    providers, err = storage.resolve_providers(provider, mode)
    if err:
        log.error(err)
        return None, ExitCode.USAGE
    try:
        langs, chain_label = resolve_languages(mode, chain, languages, steps)
    except ValueError as exc:
        log.error(str(exc))
        return None, ExitCode.USAGE

    detected = detect_language(text)
    log.info("原文：%s", text)
    log.info("检出语言：%s", detected)
    if mode == "rwkv20" and has_han(text):
        log.error("--mode rwkv20 需要英文原文；批量翻译会自动读取 en.md。")
        return None, ExitCode.USAGE
    log.info("路线：%s（%d 次）：%s", chain_label, len(langs), " → ".join(lang_label(x) for x in langs))
    log.info("后端：%s", " → ".join(providers))

    translators = storage.build_translators(providers, settings)
    for tr in translators:
        tr.min_interval = max(  # type: ignore[attr-defined]
            float(getattr(tr, "min_interval", 0.0) or 0.0), float(min_interval)
        )
    return (
        _Context(
            home=home_,
            settings=settings,
            text=text,
            detected=detected,
            mode=mode,
            chain_label=chain_label,
            langs=langs,
            providers=providers,
            balance=balance,
            translators=translators,
            state_path=os.path.join(home_, STATE_FILENAME),
            no_tts=no_tts,
        ),
        None,
    )


def _resume(
    ctx: _Context, languages: str, steps: int, reset: bool
) -> tuple[models.ChainState | None, list[models.TranslationStep], int, str, str, ExitCode | None]:
    """加载续跑状态；签名不一致时返回退出码。"""
    sig = _signature(
        ctx.text, ctx.detected, ctx.mode, ctx.chain_label, languages, steps, ctx.providers, ctx.settings
    )
    raw = storage.load_json_with_backup(ctx.state_path)
    prev = models.ChainState.model_validate(raw) if raw else None
    if prev and not reset and not signatures_match(prev.signature, sig):
        log.error("检测到未完成的进度，但参数与本次不一致；如确认从头开始，请加 --reset。")
        log.error("差异：\n%s", describe_diff(prev.signature, sig))
        return None, [], 0, ctx.text, "", ExitCode.SIGNATURE

    done_steps: list[models.TranslationStep] = []
    next_index = 0
    cur_text = ctx.text
    started_at = datetime.now().isoformat(timespec="seconds")
    if prev and not reset:
        done_steps = [models.TranslationStep.model_validate(s) for s in prev.steps]
        next_index = prev.next_index
        cur_text = prev.text
        started_at = prev.started_at
    state = models.ChainState(
        signature=sig,
        source=ctx.text,
        detected=ctx.detected,
        languages=list(ctx.langs),
        text=cur_text,
        next_index=next_index,
        steps=[s.model_dump(mode="json") for s in done_steps],
        started_at=started_at,
    )
    return state, done_steps, next_index, cur_text, started_at, None


def _step_error(exc: Exception, state_path: str, state: models.ChainState) -> ExitCode:
    """把单步异常映射为退出码（必要时保存进度）。"""
    if isinstance(exc, UnsupportedLanguage):
        log.error("语言不受支持：%s", exc)
        return ExitCode.UNSUPPORTED_LANGUAGE
    _save(state_path, state)
    if isinstance(exc, QuotaExceeded):
        log.error("今日配额已耗尽：%s", exc)
        return ExitCode.QUOTA
    if isinstance(exc, ModelNotLoaded):
        log.error("本地 RWKV 模型未加载：%s", exc)
        return ExitCode.PAUSED
    if isinstance(exc, ConnectionFailed):
        log.error("所有后端均连接失败：\n%s", exc)
        return ExitCode.CONNECTION
    if isinstance(exc, RateLimited):
        log.error("所有后端均被限流：%s", exc)
        return ExitCode.PAUSED
    log.error("已暂停：%s", exc)
    return ExitCode.PAUSED


def _translate_loop(
    ctx: _Context,
    tasks: list[dict[str, str | int]],
    state: models.ChainState,
    done_steps: list[models.TranslationStep],
    next_index: int,
    cur_text: str,
) -> tuple[str, ExitCode | None]:
    """逐步翻译并保存进度；返回 `(最终文本, 退出码或 None)`。"""
    for idx in range(next_index, len(tasks)):
        task = tasks[idx]
        try:
            provider_name, out, meta = _translate_step(
                cur_text,
                str(task["src"]),
                str(task["tgt"]),
                ctx.translators,
                ctx.providers,
                ctx.balance,
                ctx.settings,
            )
        except (
            QuotaExceeded,
            UnsupportedLanguage,
            ModelNotLoaded,
            ConnectionFailed,
            RateLimited,
            BadOutput,
        ) as exc:
            return cur_text, _step_error(exc, ctx.state_path, state)
        cur_text = out
        done_steps.append(
            models.TranslationStep(
                index=idx,
                kind=str(task["kind"]),
                lang=str(task["lang"]),
                provider=provider_name,
                text=out,
                rwkv=meta,
            )
        )
        state.text = cur_text
        state.next_index = idx + 1
        state.steps = [s.model_dump(mode="json") for s in done_steps]
        _save(ctx.state_path, state)
    return cur_text, None


def _write_output(
    ctx: _Context, done_steps: list[models.TranslationStep], cur_text: str, started_at: str
) -> str:
    """写结果 JSON（并可选生成语音），返回输出路径。"""
    result = models.TranslationResult(
        source=ctx.text,
        source_lang=ctx.detected,
        target_lang=TARGET_ZH,
        mode=ctx.mode,
        providers=_providers_of(done_steps),
        languages=list(ctx.langs),
        steps=done_steps,
        final=cur_text,
        chain=ctx.chain_label,
        rwkv=_merge_rwkv(done_steps),
        started_at=started_at,
        finished_at=datetime.now().isoformat(timespec="seconds"),
    )
    os.makedirs(storage.out_dir(ctx.home), exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(storage.out_dir(ctx.home), stamp + ".json")
    storage.atomic_write_json(out_path, result)
    log.info("最终译文（%s）：%s", TARGET_ZH, cur_text)
    log.info("结果已保存：%s", storage.rel(ctx.home, out_path))
    if not ctx.no_tts:
        _save_tts(ctx, cur_text, stamp)
    return out_path


def _save_tts(ctx: _Context, cur_text: str, stamp: str) -> None:
    """生成并保存语音；失败只告警，不影响翻译结果。"""
    try:
        audio = tts_bytes(cur_text, TARGET_ZH, ctx.settings.proxy or "")
        mp3_path = os.path.join(storage.out_dir(ctx.home), stamp + ".mp3")
        with open(mp3_path, "wb") as f:
            f.write(audio)
        log.info("语音已保存：%s（%d 字节）", storage.rel(ctx.home, mp3_path), len(audio))
    except Exception as exc:  # noqa: BLE001 - 语音失败不影响翻译结果
        log.warning("语音生成失败（不影响翻译结果）：%s", exc)


def run(
    text: str = "Hello, World",
    mode: str = "random",
    chain: str = "asia",
    languages: str = "",
    steps: int = 20,
    provider: str | None = None,
    min_interval: float = 0.3,
    balance: bool = False,
    no_tts: bool = False,
    reset: bool = False,
    dry_run: bool = False,
    data_dir: str | None = None,
    no_file_log: bool = False,
) -> ExitCode:
    """对单个文本执行来回翻译，返回 `ExitCode`。"""
    ctx, code = _prepare(
        text, mode, chain, languages, steps, provider, min_interval, balance, no_tts, data_dir
    )
    if code is not None or ctx is None:
        return code or ExitCode.USAGE
    if dry_run:
        if mode == "rwkv20":
            log.info("本地一次成型：读取英文原文，1 次本地生成（不访问云端）")
        log.info("--dry-run：仅展示路线，未执行翻译。")
        return ExitCode.OK

    state, done_steps, next_index, cur_text, started_at, code = _resume(ctx, languages, steps, reset)
    if code is not None or state is None:
        return code or ExitCode.SIGNATURE

    handler = _setup_file_log(ctx.home, no_file_log)
    try:
        cur_text, code = _translate_loop(
            ctx, build_tasks(ctx.detected, ctx.langs), state, done_steps, next_index, cur_text
        )
        if code is not None:
            return code
        _write_output(ctx, done_steps, cur_text, started_at)
        storage.remove_with_backup(ctx.state_path)
        return ExitCode.OK
    finally:
        if handler is not None:
            log.removeHandler(handler)
            handler.close()


def _save(state_path: str, state: models.ChainState) -> None:
    state.updated_at = datetime.now().isoformat(timespec="seconds")
    storage.atomic_write_json(state_path, state)
