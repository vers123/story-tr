"""单文本「来回翻译 N 次」链路的编排（供 `story-tr chain` 使用）。

- detect 语言 → resolve 路线 → 逐步翻译（`core.routes.build_tasks`）
- 结果写 `out/{ts}.json`（`TranslationResult`）+ 可选 mp3
- `.translate_state.json` 续跑（`ChainState`），签名不一致要求 `--reset`
"""
from __future__ import annotations

import hashlib
import itertools
import logging
import os
import threading
from datetime import datetime
from typing import Any

from story_tr import models, transport
from story_tr.config import Settings, load_settings
from story_tr.core import (
    build_signature,
    build_tasks,
    detect_language,
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

from . import _io

log = logging.getLogger("story_tr.services.chain")

TARGET_ZH = "zh-CN"
STATE_FILENAME = ".translate_state.json"

_balance_cursor = itertools.count()
_balance_lock = threading.Lock()


def _rwkv_signature(settings: Settings) -> dict[str, Any]:
    template = settings.rwkv_prompt_template or ""
    return {
        "url": (settings.rwkv_api_url or "http://127.0.0.1:8000").rstrip("/"),
        "model": "rwkv",
        "template_sha1": hashlib.sha1(template.encode("utf-8")).hexdigest(),
        "max_tokens": settings.rwkv_max_tokens,
        "temperature": settings.rwkv_temperature,
        "top_p": settings.rwkv_top_p,
        "chunk_chars": settings.rwkv_chunk_chars,
    }


def _signature(text: str, detected: str, mode: str, chain: str, languages: str, steps: int,
               providers: list[str], settings: Settings) -> dict[str, Any]:
    rwkv_sig = _rwkv_signature(settings) if (mode == "rwkv20" or "rwkv" in providers) else None
    sig = build_signature(mode=mode, chain=chain, languages=languages, steps=steps,
                          providers=providers, rwkv_signature=rwkv_sig)
    sig["text"] = text
    sig["detected"] = detected
    return sig


def _order(translators: list[transport.Translator], providers: list[str],
           balance: bool) -> list[tuple[str, transport.Translator]]:
    pairs = list(zip(providers, translators, strict=False))
    if not balance or len(pairs) < 2:
        return pairs
    with _balance_lock:
        i = next(_balance_cursor) % len(pairs)
    return pairs[i:] + pairs[:i]


def _rwkv_meta(translator: transport.Translator, settings: Settings) -> models.RwkvMeta | None:
    if getattr(translator, "name", None) != "rwkv":
        return None
    sig = _rwkv_signature(settings)
    return models.RwkvMeta(
        url=sig["url"], model=sig["model"], template_sha1=sig["template_sha1"],
        chunks=int(getattr(translator, "last_chunks", 0) or 0),
        prompt_tokens=int(getattr(translator, "last_prompt_tokens", 0) or 0),
        completion_tokens=int(getattr(translator, "last_completion_tokens", 0) or 0),
    )


def _translate_step(text: str, src: str, tgt: str, translators: list[transport.Translator],
                    providers: list[str], balance: bool,
                    settings: Settings) -> tuple[str, str, models.RwkvMeta | None]:
    reasons: list[str] = []
    last_bad: BadOutput | None = None
    for pname, translator in _order(translators, providers, balance):
        ok, reason = translator.available()
        if not ok:
            reasons.append(f"{pname} 不可用：{reason}")
            continue
        try:
            out = translator.translate(text, src, tgt)
        except UnsupportedLanguage:
            raise
        except QuotaExceeded:
            raise
        except ModelNotLoaded:
            raise
        except ConnectionFailed as exc:
            reasons.append(f"{pname} 连接失败：{exc}")
            continue
        except RateLimited as exc:
            reasons.append(f"{pname} 被限流：{exc}")
            continue
        except BadOutput as exc:
            last_bad = exc
            reasons.append(f"{pname} 业务失败：{exc}")
            continue
        out = (out or "").strip()
        if not out:
            last_bad = BadOutput(f"{pname} 返回空结果")
            reasons.append(str(last_bad))
            continue
        return pname, out, _rwkv_meta(translator, settings)
    if last_bad is not None:
        raise BadOutput("所有后端业务失败：\n    - " + "\n    - ".join(reasons))
    raise ConnectionFailed("所有后端连接失败：\n    - " + "\n    - ".join(reasons))


def _merge_rwkv(steps: list[models.TranslationStep]) -> models.RwkvMeta | None:
    metas = [s.rwkv for s in steps if s.rwkv is not None]
    if not metas:
        return None
    first = metas[0]
    return models.RwkvMeta(
        url=first.url, model=first.model, template_sha1=first.template_sha1,
        chunks=sum(m.chunks for m in metas),
        prompt_tokens=sum(m.prompt_tokens for m in metas),
        completion_tokens=sum(m.completion_tokens for m in metas),
    )


def _providers_of(steps: list[models.TranslationStep]) -> list[str]:
    seen: list[str] = []
    for s in steps:
        if s.provider and s.provider not in seen:
            seen.append(s.provider)
    return seen


def _setup_file_log(home_: str, no_file_log: bool) -> logging.Handler | None:
    if no_file_log:
        return None
    os.makedirs(_io.logs_dir(home_), exist_ok=True)
    path = os.path.join(_io.logs_dir(home_), datetime.now().strftime("%Y%m%d_%H%M%S") + ".log")
    handler: logging.Handler = logging.FileHandler(path, encoding="utf-8")
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    return handler


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
    home_ = _io.home(data_dir)
    settings = load_settings()
    providers, err = _io.resolve_providers(provider, mode)
    if err:
        log.error(err)
        return ExitCode.USAGE
    try:
        langs, chain_label = resolve_languages(mode, chain, languages, steps)
    except ValueError as exc:
        log.error(str(exc))
        return ExitCode.USAGE

    detected = detect_language(text)
    log.info("原文：%s", text)
    log.info("检出语言：%s", detected)
    log.info("路线：%s（%d 次）：%s", chain_label, len(langs), " → ".join(langs))
    log.info("后端：%s", " → ".join(providers))

    if dry_run:
        if mode == "rwkv20":
            log.info("本地一次成型：每份 1 次本地生成（不访问云端）")
        log.info("--dry-run：仅展示路线，未执行翻译。")
        return ExitCode.OK

    translators = _io.build_translators(providers, settings)
    for tr in translators:
        tr.min_interval = max(  # type: ignore[attr-defined]
            float(getattr(tr, "min_interval", 0.0) or 0.0), float(min_interval))

    sig = _signature(text, detected, mode, chain, languages, steps, providers, settings)
    state_path = os.path.join(home_, STATE_FILENAME)
    raw = _io.load_json_with_backup(state_path)
    prev = models.ChainState.model_validate(raw) if raw else None
    if prev and not reset and not signatures_match(prev.signature, sig):
        log.error("检测到未完成的进度，但参数与本次不一致；如确认从头开始，请加 --reset。")
        return ExitCode.SIGNATURE

    tasks = build_tasks(detected, langs)
    done_steps: list[models.TranslationStep] = []
    next_index = 0
    cur_text = text
    started_at = datetime.now().isoformat(timespec="seconds")
    if prev and not reset:
        done_steps = [models.TranslationStep.model_validate(s) for s in prev.steps]
        next_index = prev.next_index
        cur_text = prev.text
        started_at = prev.started_at

    state = models.ChainState(
        signature=sig, source=text, detected=detected, languages=list(langs),
        text=cur_text, next_index=next_index,
        steps=[s.model_dump(mode="json") for s in done_steps], started_at=started_at)

    handler = _setup_file_log(home_, no_file_log)
    try:
        for idx in range(next_index, len(tasks)):
            task = tasks[idx]
            try:
                provider_name, out, meta = _translate_step(
                    cur_text, str(task["src"]), str(task["tgt"]),
                    translators, providers, balance, settings)
            except QuotaExceeded as exc:
                _save(state_path, state)
                log.error("今日配额已耗尽：%s", exc)
                return ExitCode.QUOTA
            except UnsupportedLanguage as exc:
                log.error("语言不受支持：%s", exc)
                return ExitCode.UNSUPPORTED_LANGUAGE
            except ModelNotLoaded as exc:
                _save(state_path, state)
                log.error("本地 RWKV 模型未加载：%s", exc)
                return ExitCode.PAUSED
            except ConnectionFailed as exc:
                _save(state_path, state)
                log.error("所有后端均连接失败：\n%s", exc)
                return ExitCode.CONNECTION
            except BadOutput as exc:
                _save(state_path, state)
                log.error("已暂停：%s", exc)
                return ExitCode.PAUSED

            cur_text = out
            done_steps.append(models.TranslationStep(
                index=idx, kind=str(task["kind"]), lang=str(task["lang"]),
                provider=provider_name, text=out, rwkv=meta))
            state.text = cur_text
            state.next_index = idx + 1
            state.steps = [s.model_dump(mode="json") for s in done_steps]
            _save(state_path, state)

        finished_at = datetime.now().isoformat(timespec="seconds")
        result = models.TranslationResult(
            source=text, source_lang=detected, target_lang=TARGET_ZH, mode=mode,
            providers=_providers_of(done_steps), languages=list(langs), steps=done_steps,
            final=cur_text, chain=chain_label, rwkv=_merge_rwkv(done_steps),
            started_at=started_at, finished_at=finished_at)
        os.makedirs(_io.out_dir(home_), exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = os.path.join(_io.out_dir(home_), stamp + ".json")
        _io.atomic_write_json(out_path, result)

        log.info("最终译文（%s）：%s", TARGET_ZH, cur_text)
        log.info("结果已保存：%s", _io.rel(home_, out_path))

        if not no_tts:
            try:
                audio = tts_bytes(cur_text, TARGET_ZH, settings.proxy or "")
                mp3_path = os.path.join(_io.out_dir(home_), stamp + ".mp3")
                with open(mp3_path, "wb") as f:
                    f.write(audio)
                log.info("语音已保存：%s（%d 字节）", _io.rel(home_, mp3_path), len(audio))
            except Exception as exc:  # noqa: BLE001 - 语音失败不影响翻译结果
                log.warning("语音生成失败（不影响翻译结果）：%s", exc)

        _io.remove_with_backup(state_path)
        return ExitCode.OK
    finally:
        if handler is not None:
            log.removeHandler(handler)
            handler.close()


def _save(state_path: str, state: models.ChainState) -> None:
    state.updated_at = datetime.now().isoformat(timespec="seconds")
    _io.atomic_write_json(state_path, state)
