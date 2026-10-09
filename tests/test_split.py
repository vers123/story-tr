# -*- coding: utf-8 -*-
"""长文本分块：边界与拼接完整性。"""
from story_tr.chain import MAX_CHARS_PER_REQUEST, _split_text


def test_empty():
    assert _split_text("") == []


def test_short_text_single_chunk():
    assert _split_text("你好。") == ["你好。"]


def test_default_limit_is_1500():
    assert MAX_CHARS_PER_REQUEST == 1500


def test_split_by_sentence_and_rejoin():
    text = "句子。" * 600  # 1800 字
    chunks = _split_text(text, limit=100)
    assert len(chunks) > 1
    assert all(len(c) <= 100 for c in chunks)
    assert "".join(chunks) == text


def test_hard_split_when_single_sentence_too_long():
    text = "字" * 250
    chunks = _split_text(text, limit=100)
    assert [len(c) for c in chunks] == [100, 100, 50]
    assert "".join(chunks) == text
