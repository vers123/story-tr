"""`story_tr.core` 纯逻辑层离线单测。"""
import pytest

from story_tr.core import (
    CHAINS,
    RANDOM_POOL,
    build_signature,
    build_tasks,
    clean_output,
    describe_diff,
    detect_language,
    len_ratio,
    parse_frontmatter,
    repeat4,
    resolve_languages,
    score,
    signatures_match,
    similarity,
    split_chunks,
    split_paragraphs,
    to_provider_lang,
    zh_ratio,
)
from story_tr.errors import UnsupportedLanguage


# --------------------------------------------------------------------------- #
# detect_language
# --------------------------------------------------------------------------- #
def test_detect_language_by_script():
    assert detect_language("你好世界") == "zh-CN"
    assert detect_language("こんにちは") == "ja"
    assert detect_language("안녕하세요") == "ko"


def test_detect_language_no_features():
    assert detect_language("") == "en"
    assert detect_language("   ") == "en"
    assert detect_language("……") == "en"
    assert detect_language("12345") == "en"


# --------------------------------------------------------------------------- #
# to_provider_lang
# --------------------------------------------------------------------------- #
def test_to_provider_lang_google():
    assert to_provider_lang("google", "ja") == "ja"
    assert to_provider_lang("google", "zh-CN") == "zh-CN"


def test_to_provider_lang_bing():
    assert to_provider_lang("bing", "ja") == "ja"
    assert to_provider_lang("bing", "iw") == "he"
    assert to_provider_lang("bing", "zh-CN") == "zh-Hans"


def test_to_provider_lang_baidu():
    assert to_provider_lang("baidu", "ja") == "japanese"
    assert to_provider_lang("baidu", "zh-CN") == "chinese (simplified)"


def test_to_provider_lang_baidu_unsupported():
    with pytest.raises(UnsupportedLanguage):
        to_provider_lang("baidu", "tl")


def test_to_provider_lang_unknown_provider():
    with pytest.raises(UnsupportedLanguage):
        to_provider_lang("nope", "ja")


# --------------------------------------------------------------------------- #
# resolve_languages
# --------------------------------------------------------------------------- #
def test_resolve_fixed():
    langs, label = resolve_languages("fixed", chain="asia")
    assert label == "asia"
    assert langs == CHAINS["asia"]
    assert len(langs) == 20


def test_resolve_random():
    langs, label = resolve_languages("random", steps=5)
    assert label == "random"
    assert len(langs) == 5
    assert set(langs) <= set(RANDOM_POOL)


def test_resolve_random_too_many_steps():
    with pytest.raises(ValueError):
        resolve_languages("random", steps=len(RANDOM_POOL) + 1)


def test_resolve_custom():
    langs, label = resolve_languages("custom", languages=" ja, de , fr ")
    assert label == "custom"
    assert langs == ["ja", "de", "fr"]


def test_resolve_custom_empty():
    with pytest.raises(ValueError):
        resolve_languages("custom", languages=" , ")


def test_resolve_rwkv20():
    langs, label = resolve_languages("rwkv20")
    assert label == "rwkv20"
    assert langs == ["rwkv20"]


def test_resolve_invalid_mode():
    with pytest.raises(ValueError):
        resolve_languages("nope")


# --------------------------------------------------------------------------- #
# build_tasks
# --------------------------------------------------------------------------- #
def test_build_tasks_regular_chain():
    tasks = build_tasks("zh-CN", ["ja", "de"])
    assert tasks[0] == {"kind": "to_en", "lang": "en", "src": "zh-CN", "tgt": "en"}
    assert tasks[1] == {"kind": "step", "index": 0, "lang": "ja", "src": "en", "tgt": "ja"}
    assert tasks[2] == {"kind": "step", "index": 1, "lang": "de", "src": "ja", "tgt": "de"}
    assert tasks[-1] == {"kind": "to_zh", "lang": "zh-CN", "src": "de", "tgt": "zh-CN"}


def test_build_tasks_english_skips_to_en():
    tasks = build_tasks("en", ["ja"])
    assert tasks[0] == {"kind": "step", "index": 0, "lang": "ja", "src": "en", "tgt": "ja"}
    assert tasks[-1] == {"kind": "to_zh", "lang": "zh-CN", "src": "ja", "tgt": "zh-CN"}


def test_build_tasks_rwkv20_uses_english_source():
    assert build_tasks("zh-CN", ["rwkv20"]) == [
        {"kind": "rwkv20", "index": 0, "lang": "zh-CN", "src": "en", "tgt": "zh-CN"}
    ]
    assert build_tasks("en", ["rwkv20"]) == [
        {"kind": "rwkv20", "index": 0, "lang": "zh-CN", "src": "en", "tgt": "zh-CN"}
    ]


# --------------------------------------------------------------------------- #
# split_chunks
# --------------------------------------------------------------------------- #
def test_split_chunks_empty():
    assert split_chunks("", 100) == []


def test_split_chunks_short():
    assert split_chunks("你好。", 100) == ["你好。"]


def test_split_chunks_on_sentence_end():
    text = "第一句。第二句！第三句？"
    chunks = split_chunks(text, 4)
    assert chunks == ["第一句。", "第二句！", "第三句？"]
    assert "".join(chunks) == text


def test_split_chunks_hard_cut():
    text = "啊" * 25
    chunks = split_chunks(text, 10)
    assert [len(c) for c in chunks] == [10, 10, 5]
    assert "".join(chunks) == text


# --------------------------------------------------------------------------- #
# clean_output
# --------------------------------------------------------------------------- #
def test_clean_output_strips_role_prefix():
    assert clean_output("Bot: 这是结果", "", "原文") == "这是结果"
    assert clean_output("Assistant：这是结果", "", "原文") == "这是结果"


def test_clean_output_strips_prompt_echo():
    prompt = "User: 原文\n\nBot:"
    assert clean_output(prompt + "这是结果", prompt, "原文") == "这是结果"


def test_clean_output_strips_wrapping_quotes():
    assert clean_output("“这是结果”", "", "原文") == "这是结果"
    assert clean_output('"这是结果"', "", "原文") == "这是结果"
    assert clean_output("「这是结果」", "", "原文") == "这是结果"


def test_clean_output_keeps_paragraph_blank_line():
    assert clean_output("第一段  \n\n第二段\t", "", "原文") == "第一段\n\n第二段"


# --------------------------------------------------------------------------- #
# parse_frontmatter / split_paragraphs
# --------------------------------------------------------------------------- #
def test_parse_frontmatter_normal():
    raw = '---\nid: "1"\nstory: amber_journal\nlang: zh\ntitle: "标题"\n---\n\n第一段\n\n第二段\n'
    fm, body = parse_frontmatter(raw)
    assert fm == {"id": "1", "story": "amber_journal", "lang": "zh", "title": "标题"}
    assert split_paragraphs(body) == ["第一段", "第二段"]


def test_parse_frontmatter_without_frontmatter():
    raw = "第一段\n\n第二段\n"
    fm, body = parse_frontmatter(raw)
    assert fm is None
    assert body == raw


def test_parse_frontmatter_partial_keys():
    raw = '---\nid: "1"\n---\n\n正文\n'
    fm, body = parse_frontmatter(raw)
    assert fm == {"id": "1"}
    assert split_paragraphs(body) == ["正文"]


def test_parse_frontmatter_unquoted_value():
    raw = '---\ntitle: 角色详细\n---\n\n正文\n'
    fm, _ = parse_frontmatter(raw)
    assert fm == {"title": "角色详细"}


def test_split_paragraphs_empty():
    assert split_paragraphs("") == []
    assert split_paragraphs("\n\n\n") == []


# --------------------------------------------------------------------------- #
# build_signature / signatures_match / describe_diff
# --------------------------------------------------------------------------- #
def test_build_signature_stable():
    a = build_signature(mode="fixed", chain="asia", languages="", steps=20, providers=["google"])
    b = build_signature(mode="fixed", chain="asia", languages="", steps=20, providers=["google"])
    assert a == b


def test_build_signature_differs():
    a = build_signature(mode="fixed", chain="asia", languages="", steps=20, providers=["google"])
    b = build_signature(mode="fixed", chain="europe", languages="", steps=20, providers=["google"])
    assert a != b


def test_build_signature_mode_specific_fields():
    sig = build_signature(mode="random", chain="asia", languages="", steps=5, providers=["google"])
    assert sig["steps"] == 5
    assert sig["chain"] is None
    assert sig["languages"] is None


def test_build_signature_rwkv_sub_object():
    sig = build_signature(
        mode="rwkv20", chain="asia", languages="", steps=20,
        providers=["rwkv"], rwkv_signature={"url": "http://127.0.0.1:8000"},
    )
    assert sig["rwkv"] == {"url": "http://127.0.0.1:8000"}


def test_build_signature_empty_rwkv_omitted():
    sig = build_signature(
        mode="rwkv20", chain="asia", languages="", steps=20, providers=["rwkv"],
        rwkv_signature={},
    )
    assert "rwkv" not in sig


def test_signatures_match():
    assert signatures_match(None, {"mode": "fixed"}) is True
    assert signatures_match({}, {"mode": "fixed"}) is True
    assert signatures_match({"mode": "fixed"}, {"mode": "fixed"}) is True
    assert signatures_match({"mode": "fixed"}, {"mode": "random"}) is False


def test_describe_diff_lists_changes():
    prev = {"mode": "fixed", "chain": "asia", "steps": None, "languages": None,
            "providers": ["google"]}
    cur = {"mode": "random", "chain": None, "steps": 5, "languages": None,
           "providers": ["google"]}
    text = describe_diff(prev, cur)
    assert "模式：上次 fixed → 本次 random" in text
    assert "随机步数：上次 — → 本次 5" in text


def test_describe_diff_empty_when_same():
    sig = {"mode": "fixed"}
    assert describe_diff(sig, dict(sig)) == "（无可比较的差异）"


# --------------------------------------------------------------------------- #
# metrics
# --------------------------------------------------------------------------- #
def test_zh_ratio():
    assert zh_ratio("你好世界") == 1.0
    assert zh_ratio("你好 abc") == pytest.approx(0.4)
    assert zh_ratio("") == 0.0
    assert zh_ratio("   ") == 0.0


def test_len_ratio():
    assert len_ratio("abc", "abc") == 1.0
    assert len_ratio("abc", "abcdef") == 0.5
    assert len_ratio("abc", "") == 3.0


def test_similarity():
    assert similarity("abc", "abc") == 1.0
    assert similarity("abc", "xyz") == 0.0
    assert similarity("abc", "axc") == pytest.approx(2 / 3)


def test_repeat4():
    assert repeat4("") == 0.0
    assert repeat4("abc") == 0.0
    assert repeat4("abcdefgh") == 0.0
    assert repeat4("abababab") == pytest.approx(0.6)


def test_score_with_baseline():
    m = {"sim": 1.0, "zh": 0.5, "len": 1.0, "rep": 0.0}
    assert score(m, True) == pytest.approx(0.9)


def test_score_without_baseline():
    m = {"zh": 1.0, "len": 1.0, "rep": 0.0}
    assert score(m, False) == pytest.approx(1.0)
