"""翻译链共享语义：签名、后端轮换、单步翻译与结果合并。

`services.chain`（单文本）与 `services.translate`（批量）共用同一套实现，
集中在本模块避免两处漂移。
"""

from __future__ import annotations

import hashlib
import itertools
import threading
from typing import Any

from story_tr import models, transport
from story_tr.config import Settings
from story_tr.core import build_signature
from story_tr.errors import (
    BadOutput,
    ConnectionFailed,
    ModelNotLoaded,
    QuotaExceeded,
    RateLimited,
    UnsupportedLanguage,
)

_balance_cursor = itertools.count()
_balance_lock = threading.Lock()


def rwkv_signature(settings: Settings) -> dict[str, Any]:
    """本地 RWKV 的关键参数签名（换模板 / 模型参数会导致续跑不兼容）。"""
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


def signature(
    mode: str, chain: str, languages: str, steps: int, providers: list[str], settings: Settings
) -> dict[str, Any]:
    """构造批量签名（仅在用到本地后端时附带 RWKV 子签名）。"""
    rwkv_sig = rwkv_signature(settings) if (mode == "rwkv20" or "rwkv" in providers) else None
    return build_signature(
        mode=mode, chain=chain, languages=languages, steps=steps, providers=providers, rwkv_signature=rwkv_sig
    )


def order(
    translators: list[transport.Translator], providers: list[str], balance: bool
) -> list[tuple[str, transport.Translator]]:
    """按 provider 顺序配对；`balance` 时轮换起始后端。"""
    pairs = list(zip(providers, translators, strict=False))
    if not balance or len(pairs) < 2:
        return pairs
    with _balance_lock:
        i = next(_balance_cursor) % len(pairs)
    return pairs[i:] + pairs[:i]


def rwkv_meta(translator: transport.Translator, settings: Settings) -> models.RwkvMeta | None:
    """本地后端的生成元数据（非 rwkv 后端返回 `None`）。"""
    if getattr(translator, "name", None) != "rwkv":
        return None
    sig = rwkv_signature(settings)
    return models.RwkvMeta(
        url=sig["url"],
        model=sig["model"],
        template_sha1=sig["template_sha1"],
        chunks=int(getattr(translator, "last_chunks", 0) or 0),
        prompt_tokens=int(getattr(translator, "last_prompt_tokens", 0) or 0),
        completion_tokens=int(getattr(translator, "last_completion_tokens", 0) or 0),
    )


def merge_rwkv(steps: list[models.TranslationStep]) -> models.RwkvMeta | None:
    """合并各步的 RWKV 元数据（无则返回 `None`）。"""
    metas = [s.rwkv for s in steps if s.rwkv is not None]
    if not metas:
        return None
    first = metas[0]
    return models.RwkvMeta(
        url=first.url,
        model=first.model,
        template_sha1=first.template_sha1,
        chunks=sum(m.chunks for m in metas),
        prompt_tokens=sum(m.prompt_tokens for m in metas),
        completion_tokens=sum(m.completion_tokens for m in metas),
    )


def providers_of(steps: list[models.TranslationStep]) -> list[str]:
    """按首次出现顺序去重的实际后端列表。"""
    seen: list[str] = []
    for s in steps:
        if s.provider and s.provider not in seen:
            seen.append(s.provider)
    return seen


def translate_step(
    text: str,
    src: str,
    tgt: str,
    translators: list[transport.Translator],
    providers: list[str],
    balance: bool,
    settings: Settings,
) -> tuple[str, str, models.RwkvMeta | None]:
    """按顺序尝试后端，返回 `(provider, 译文, rwkv 元数据)`。

    失败优先级：业务失败（`BadOutput`）> 全部被限流（`RateLimited`）> 连接失败。
    不可用 / 连接失败 / 限流 / 业务失败都会继续尝试下一个后端。
    """
    reasons: list[str] = []
    last_bad: BadOutput | None = None
    last_rate: RateLimited | None = None
    for pname, translator in order(translators, providers, balance):
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
            last_rate = exc
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
        return pname, out, rwkv_meta(translator, settings)
    detail = "\n    - " + "\n    - ".join(reasons)
    if last_bad is not None:
        raise BadOutput("所有后端业务失败：" + detail)
    if last_rate is not None:
        raise RateLimited("所有后端均被限流：" + detail)
    raise ConnectionFailed("所有后端连接失败：" + detail)
