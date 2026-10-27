"""传输层：统一后端抽象与 google / bing / baidu / rwkv 实现。"""
from __future__ import annotations

from story_tr.config import Settings

from .baidu import BaiduTranslator
from .base import BaseTranslator, Translator
from .bing import BingTranslator
from .google import GoogleTranslator
from .rwkv import RwkvTranslator

PROVIDERS = ["google", "bing", "baidu", "rwkv"]
DEFAULT_ORDER = ["google", "bing", "baidu"]

__all__ = [
    "PROVIDERS",
    "DEFAULT_ORDER",
    "Translator",
    "BaseTranslator",
    "build_translator",
    "GoogleTranslator",
    "BingTranslator",
    "BaiduTranslator",
    "RwkvTranslator",
]


def build_translator(provider: str, settings: Settings) -> Translator:
    """按后端名构造翻译器；未知后端抛 ValueError。"""
    if provider == "google":
        return GoogleTranslator(settings)
    if provider == "bing":
        return BingTranslator(settings)
    if provider == "baidu":
        return BaiduTranslator(settings)
    if provider == "rwkv":
        return RwkvTranslator(settings)
    raise ValueError(f"未知翻译后端：{provider}")
