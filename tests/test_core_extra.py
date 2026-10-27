"""`story_tr.core` 纯逻辑层补充边界用例（语言归一化、标签、frontmatter 异常等）。"""
import pytest

from story_tr.core import (
    describe_diff,
    detect_language,
    lang_label,
    normalize_detected,
    parse_frontmatter,
    resolve_languages,
)
from story_tr.errors import ExitCode
from story_tr.errors import label as exit_label


# --------------------------------------------------------------------------- #
# lang.normalize_detected / lang_label
# --------------------------------------------------------------------------- #
def test_normalize_detected_maps_langdetect_codes():
    assert normalize_detected("zh-cn") == "zh-CN"
    assert normalize_detected("zh-tw") == "zh-TW"
    assert normalize_detected("he") == "iw"
    assert normalize_detected("jv") == "jw"
    assert normalize_detected("fil") == "tl"
    assert normalize_detected("nb") == "no"
    assert normalize_detected("ja") == "ja"


def test_lang_label_known_and_unknown():
    assert lang_label("zh-CN") == "简体中文(zh-CN)"
    assert lang_label("nope") == "?(nope)"


def test_detect_language_uses_langdetect_for_latin():
    # 长句确保 langdetect 能稳定判出英文，覆盖其预热与检出路径
    assert detect_language("This is a clear English sentence used for language detection.") == "en"


# --------------------------------------------------------------------------- #
# routes.resolve_languages 未知链
# --------------------------------------------------------------------------- #
def test_resolve_fixed_unknown_chain():
    with pytest.raises(ValueError):
        resolve_languages("fixed", chain="nope")


# --------------------------------------------------------------------------- #
# segment.parse_frontmatter 异常形态
# --------------------------------------------------------------------------- #
def test_parse_frontmatter_single_delimiter():
    raw = "---\nid: 1\n"
    fm, body = parse_frontmatter(raw)
    assert fm is None
    assert body == raw


def test_parse_frontmatter_empty_key_and_value():
    raw = '---\n: novalue\nkey:\nid: "1"\n---\n正文\n'
    fm, body = parse_frontmatter(raw)
    assert fm == {"key": "", "id": "1"}
    assert body == "\n正文\n"


# --------------------------------------------------------------------------- #
# state.describe_diff 长列表折叠
# --------------------------------------------------------------------------- #
def test_describe_diff_folds_long_provider_list():
    text = describe_diff({}, {"providers": ["a", "b", "c", "d", "e", "f", "g"]})
    assert "共 7 项" in text


# --------------------------------------------------------------------------- #
# errors.label
# --------------------------------------------------------------------------- #
def test_exit_label_known_and_unknown():
    assert exit_label(int(ExitCode.OK)) == "完成"
    assert exit_label(999) == "未知"
