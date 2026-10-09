# -*- coding: utf-8 -*-
"""`parse_char_ids`：id / 范围 / 组合的解析。"""
import pytest

from story_tr.stories import parse_char_ids

ALL = ["10000002", "10000003", "10000005", "10000021", "10000150"]


def test_single_id():
    assert parse_char_ids(["10000021"], ALL) == ["10000021"]


def test_range_keeps_character_json_order():
    assert parse_char_ids(["10000003-10000021"], ALL) == ["10000003", "10000005", "10000021"]


def test_comma_inside_token():
    assert parse_char_ids(["10000021,10000005-10000006"], ALL) == ["10000005", "10000021"]


def test_multiple_tokens_dedup():
    assert parse_char_ids(["10000021", "10000021-10000022"], ALL) == ["10000021"]


def test_reversed_range():
    assert parse_char_ids(["10000021-10000003"], ALL) == ["10000003", "10000005", "10000021"]


def test_unknown_ids_skipped_with_hint(capsys):
    assert parse_char_ids(["10000008-10000013"], ALL) == []
    assert "不在 character.json" in capsys.readouterr().out


def test_bad_range_exits():
    with pytest.raises(SystemExit):
        parse_char_ids(["abc-def"], ALL)
