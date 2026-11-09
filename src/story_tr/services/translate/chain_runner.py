"""单份文本的翻译链执行（批量翻译的任务单元）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from story_tr import models, transport
from story_tr.config import Settings
from story_tr.core import build_tasks, detect_language, has_features, has_han

from .. import chain_core

TARGET_ZH = "zh-CN"


def run_chain(
    text: str,
    languages: list[str],
    translators: list[transport.Translator],
    providers: list[str],
    balance: bool,
    settings: Settings,
    source_lang: str | None = None,
) -> dict[str, Any]:
    """执行一次完整来回翻译；无文字特征时直接 passthrough。"""
    started = datetime.now().isoformat(timespec="seconds")
    if not has_features(text):
        return {
            "source": text,
            "source_lang": "none",
            "target_lang": TARGET_ZH,
            "languages": list(languages),
            "steps": [],
            "final": text,
            "passthrough": True,
            "rwkv": None,
            "started_at": started,
            "finished_at": started,
        }
    det = _detect(text, languages, source_lang)
    steps: list[models.TranslationStep] = []
    cur = text
    for k, task in enumerate(build_tasks(det, languages)):
        provider, out, meta = chain_core.translate_step(
            cur, str(task["src"]), str(task["tgt"]), translators, providers, balance, settings
        )
        steps.append(
            models.TranslationStep(
                index=k,
                kind=str(task["kind"]),
                lang=str(task["lang"]),
                provider=provider,
                text=out,
                rwkv=meta,
            )
        )
        cur = out
    return {
        "source": text,
        "source_lang": det,
        "target_lang": TARGET_ZH,
        "languages": list(languages),
        "steps": steps,
        "final": cur,
        "passthrough": False,
        "rwkv": chain_core.merge_rwkv(steps),
        "started_at": started,
        "finished_at": datetime.now().isoformat(timespec="seconds"),
    }


def _detect(text: str, languages: list[str], source_lang: str | None) -> str:
    """确定源语言：`rwkv20` 固定 en；已知中文源文可跳过检测。"""
    if list(languages) == ["rwkv20"]:
        return "en"
    if source_lang and has_han(text):
        return source_lang
    return detect_language(text)
