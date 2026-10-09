# -*- coding: utf-8 -*-
"""MCP 只读数据层（`story_tr.mcp.tools` / `resources`）：不依赖 mcp，任何版本都可跑。"""
import json

import pytest

from story_tr import stories
from story_tr.mcp import resources, tools


@pytest.fixture
def ws(tmp_path, monkeypatch):
    """最小工作区：1 角色、1 篇故事（中英）、1 份翻译结果（segments + title）。"""
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "character.json").write_text(
        json.dumps([{"id": "1", "name_zh": "甲", "name_en": "A", "stories": ["vision"]}]),
        encoding="utf-8")
    prof = tmp_path / "data" / "story" / "character" / "1" / "profile"
    sdir = prof / "story" / "vision"
    sdir.mkdir(parents=True)
    (sdir / "zh.md").write_text(
        '---\nid: "1"\nstory: vision\nlang: zh\ntitle: "神之眼"\n---\n\n第一段\n\n第二段\n',
        encoding="utf-8")
    (sdir / "en.md").write_text(
        '---\nid: "1"\nstory: vision\nlang: en\ntitle: "Vision"\n---\n\nP1\n\nP2\n',
        encoding="utf-8")
    (prof / "meta.json").write_text('{"element": "Fire"}', encoding="utf-8")
    rdir = tmp_path / "data" / "story" / "character" / "1" / "result" / "vision"
    rdir.mkdir(parents=True)
    (rdir / "segments.json").write_text(
        json.dumps({"id": "1", "story": "vision", "segments": []}), encoding="utf-8")
    (rdir / "title.json").write_text(
        json.dumps({"source": "神之眼", "final": "X"}), encoding="utf-8")

    monkeypatch.setattr(stories, "STORY_ROOT", str(tmp_path / "data" / "story" / "character"))
    monkeypatch.setattr(stories, "CHAR_FILE", str(tmp_path / "data" / "character.json"))
    return tmp_path


# --------------------------------------------------------------------------- #
# Tools
# --------------------------------------------------------------------------- #
def test_list_characters(ws):
    assert tools.list_characters() == [
        {"id": "1", "name_zh": "甲", "name_en": "A", "story_count": 1}]


def test_list_stories_reads_frontmatter_titles(ws):
    assert tools.list_stories("1") == [
        {"story": "vision", "title_zh": "神之眼", "title_en": "Vision", "paragraph_count": 2}]


def test_unknown_character_raises(ws):
    with pytest.raises(LookupError):
        tools.list_stories("9999")


def test_get_result_summary(ws):
    assert tools.get_result_summary("1", "vision")["story"] == "vision"


def test_get_result_summary_missing(ws):
    with pytest.raises(FileNotFoundError):
        tools.get_result_summary("1", "nope")


def test_read_result_item(ws):
    assert tools.read_result_item("1", "vision", "title")["source"] == "神之眼"


def test_read_result_item_rejects_bad_key(ws):
    with pytest.raises(ValueError):
        tools.read_result_item("1", "vision", "../secret")


def test_read_result_item_missing(ws):
    with pytest.raises(FileNotFoundError):
        tools.read_result_item("1", "vision", "seg_01")


# --------------------------------------------------------------------------- #
# Resources
# --------------------------------------------------------------------------- #
def test_resource_characters_index(ws):
    assert json.loads(resources.characters_index())[0]["id"] == "1"


def test_resource_character_profile(ws):
    data = json.loads(resources.character_profile("1"))
    assert data["character"]["name_zh"] == "甲"
    assert data["meta"]["element"] == "Fire"
    assert data["stories"][0]["title_zh"] == "神之眼"


def test_resource_story_markdown(ws):
    body = resources.story_markdown("1", "vision", "zh")
    assert "title: \"神之眼\"" in body and "第一段" in body


def test_resource_story_markdown_rejects_bad_lang(ws):
    with pytest.raises(ValueError):
        resources.story_markdown("1", "vision", "fr")


def test_resource_story_markdown_missing(ws):
    with pytest.raises(FileNotFoundError):
        resources.story_markdown("1", "nope", "zh")


def test_resource_result_summary(ws):
    assert json.loads(resources.result_summary("1", "vision"))["id"] == "1"
