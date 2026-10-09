# -*- coding: utf-8 -*-
"""MCP 工具层（`story_tr.mcp.tools` / `resources`）：不依赖 mcp，任何版本都可跑。"""
import json
import sys

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
    monkeypatch.setattr(stories, "STATE_PATH", str(tmp_path / ".state.json"))
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


# --------------------------------------------------------------------------- #
# P2：id 解析（不打印）
# --------------------------------------------------------------------------- #
def test_parse_char_ids_keeps_order_and_reports_missing(capsys):
    ids, missing = tools.parse_char_ids("1-3,9", ["1", "2", "3", "4"])
    assert ids == ["1", "2", "3"]          # 按 all_ids（character.json）顺序
    assert missing == ["9"]
    assert capsys.readouterr().out == ""   # 绝不打印（MCP stdio）


def test_parse_char_ids_rejects_bad_range():
    with pytest.raises(ValueError):
        tools.parse_char_ids("a-b", ["1"])


# --------------------------------------------------------------------------- #
# P2：长任务命令行
# --------------------------------------------------------------------------- #
def test_translate_argv_safe_subset(ws):
    argv = tools.translate_argv("1", "vision", "fixed", "asia", 4, "baidu", full_only=True)
    assert argv[:3] == [sys.executable, "-m", "story_tr"]
    assert argv[argv.index("translate") + 1] == "1"
    assert argv[argv.index("--stories") + 1] == "vision"
    assert argv[argv.index("--workers") + 1] == "4"
    assert argv[argv.index("--provider") + 1] == "baidu"
    assert "--chain" in argv and "--full-only" in argv
    assert "--overwrite" not in argv and "--reset" not in argv


def test_fetch_argv_defaults_to_update(ws):
    assert "--update" in tools.fetch_argv()
    assert "--update" not in tools.fetch_argv(update=False)


def test_count_translate_tasks(ws):
    # 1 篇故事（标题始终翻译）：both = title+full+2 段
    assert tools.count_translate_tasks(["1"], "", "both") == 4
    assert tools.count_translate_tasks(["1"], "", "full") == 2
    assert tools.count_translate_tasks(["1"], "", "segments") == 3


def test_make_translate_progress_reads_state(ws):
    (ws / ".state.json").write_text(
        json.dumps({"completed": ["1|vision|title", "1|vision|seg_01", "9|vision|full"]}),
        encoding="utf-8")
    read = tools.make_translate_progress(["1"], 4)
    assert read() == {"done": 2, "total": 4}


# --------------------------------------------------------------------------- #
# P2：chain（单条文本，同步）
# --------------------------------------------------------------------------- #
def test_chain_resolves_languages(monkeypatch):
    captured = {}

    def fake_run_chain(text, languages, providers, cfg, log, extra):
        captured.update(text=text, languages=list(languages), providers=list(providers))
        return {"source": text, "source_detected_lang": "zh-CN", "provider": "google",
                "steps": [], "final": "F"}

    monkeypatch.setattr(stories, "run_chain", fake_run_chain)
    out = tools.chain("你好", mode="custom", languages="ja,ko")
    assert captured["languages"] == ["ja", "ko"]
    assert captured["providers"] == ["google", "bing", "baidu"]
    assert out["final"] == "F"


# --------------------------------------------------------------------------- #
# P2：clean（默认只预览）
# --------------------------------------------------------------------------- #
def _seed_result(ws):
    rdir = ws / "data" / "story" / "character" / "1" / "result" / "vision"
    rdir.mkdir(parents=True, exist_ok=True)
    (rdir / "seg_01.json").write_text("{}", encoding="utf-8")
    return rdir


def test_clean_defaults_to_preview(ws):
    rdir = _seed_result(ws)
    out = tools.clean("1")
    assert out["dry_run"] is True and out["files"] >= 1
    assert (rdir / "seg_01.json").exists()          # 未删除


def test_clean_requires_confirm_even_with_dry_run_false(ws):
    rdir = _seed_result(ws)
    out = tools.clean("1", dry_run=False, confirm=False)
    assert out["dry_run"] is True
    assert rdir.exists()


def test_clean_deletes_only_after_confirm(ws):
    rdir = _seed_result(ws)
    out = tools.clean("1", dry_run=False, confirm=True)
    assert out["dry_run"] is False
    assert not rdir.exists()


def test_clean_unknown_character_raises(ws):
    with pytest.raises(ValueError):
        tools.clean("9999")
