"""纯逻辑层：语言判定、路线与任务、分段清洗、状态签名与评测指标。

本包不依赖任何网络 / 文件 IO / 框架，全部为纯函数，可完全离线单测。
"""
from .lang import (
    BAIDU_NAMES,
    BING_OVERRIDES,
    DETECT_TO_GOOGLE,
    LANG_NAMES,
    TARGET_ZH,
    detect_language,
    has_features,
    has_han,
    lang_label,
    normalize_detected,
    to_provider_lang,
)
from .metrics import len_ratio, repeat4, score, similarity, zh_ratio
from .routes import CHAINS, RANDOM_POOL, build_tasks, resolve_languages
from .segment import clean_output, parse_frontmatter, split_chunks, split_paragraphs
from .state import build_signature, describe_diff, signatures_match

__all__ = [
    "BAIDU_NAMES",
    "BING_OVERRIDES",
    "CHAINS",
    "DETECT_TO_GOOGLE",
    "LANG_NAMES",
    "RANDOM_POOL",
    "TARGET_ZH",
    "build_signature",
    "build_tasks",
    "clean_output",
    "describe_diff",
    "detect_language",
    "has_features",
    "has_han",
    "lang_label",
    "len_ratio",
    "normalize_detected",
    "parse_frontmatter",
    "repeat4",
    "resolve_languages",
    "score",
    "signatures_match",
    "similarity",
    "split_chunks",
    "split_paragraphs",
    "to_provider_lang",
    "zh_ratio",
]
