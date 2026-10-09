# -*- coding: utf-8 -*-
"""`story_tr.sync` 的纯函数：id 归一化、内容指纹、变更比较、新角色条目派生、索引生成。"""
import json

from story_tr import sync


def test_base_id_strips_element_suffix():
    assert sync.base_id("10000005-anemo") == "10000005"
    assert sync.base_id("10000007-hydro") == "10000007"
    assert sync.base_id("10000021") == "10000021"


def _rec(text="hello", name="Amber"):
    return {
        "id": "10000021",
        "en": {"avatar": {"name": name},
               "story": [{"i": 0, "title": "T", "title2": None, "text": text, "text2": ""}]},
        "chs": {"avatar": {"name": "安柏"},
                "story": [{"i": 0, "title": "t", "title2": None, "text": text, "text2": ""}]},
    }


def test_fingerprint_stable_and_sensitive():
    assert sync.fingerprint(_rec()) == sync.fingerprint(_rec())
    assert sync.fingerprint(_rec()) != sync.fingerprint(_rec(text="changed"))
    assert sync.fingerprint(_rec()) != sync.fingerprint(_rec(name="Amber2"))


def test_story_diff():
    old, new = _rec(), _rec(text="changed")
    assert any("文本变化" in d for d in sync.story_diff(old, new))
    assert any("角色资料变化" in d for d in sync.story_diff(_rec(), _rec(name="Amber2")))
    assert sync.story_diff(_rec(), _rec()) == []


def test_story_diff_detects_added_story():
    old = _rec()
    new = _rec()
    new["en"]["story"].append({"i": 1, "title": "New", "title2": None, "text": "x", "text2": ""})
    assert any("故事数" in d for d in sync.story_diff(old, new))


def test_make_char_entry():
    rec = {"en": {"avatar": {"name": "Amber"},
                  "story": [{"i": 0, "title": "A Legend of Sword", "title2": None, "text": "", "text2": ""},
                            {"i": 1, "title": "???", "title2": "Vision", "text": "", "text2": ""}]},
           "chs": {"avatar": {"name": "安柏"}, "story": []}}
    e = sync.make_char_entry("10000151", rec)
    assert e["id"] == "10000151"
    assert e["name_en"] == "Amber"
    assert e["name_zh"] == "安柏"
    assert e["path"] == "data/story/character/10000151"
    assert e["stories"] == ["a_legend_of_sword", "vision"]


def test_build_writes_one_index_entry_per_character(tmp_path, monkeypatch):
    """回归：build 每个角色只写一条 character.json 记录（曾因重复 append 翻倍），并追加新故事。"""
    monkeypatch.setattr(sync, "BASE", str(tmp_path))
    monkeypatch.setattr(sync, "STORY_ROOT", str(tmp_path / "story"))
    chars = [{"id": "10000021", "name_en": "Amber", "name_zh": "安柏",
              "path": "data/story/character/10000021", "stories": []}]
    rec = {"id": "10000021",
           "en": {"avatar": {"name": "Amber"},
                  "story": [{"i": 0, "title": "Details", "title2": None,
                             "text": "a\\nb", "text2": ""}]},
           "chs": {"avatar": {"name": "安柏"},
                   "story": [{"i": 0, "title": "详情", "title2": None,
                              "text": "a\\nb", "text2": ""}]}}
    sync.build(chars, {"10000021": rec}, only=None, report={})
    data = json.loads((tmp_path / "character.json").read_text(encoding="utf-8"))
    assert len(data) == 1
    assert data[0]["stories"] == ["details"]
    md = tmp_path / "story" / "10000021" / "profile" / "story" / "details" / "zh.md"
    assert md.exists()


def test_build_only_keeps_index_for_skipped_character(tmp_path, monkeypatch):
    """only 之外的角色：只保留索引，不重写文件。"""
    monkeypatch.setattr(sync, "BASE", str(tmp_path))
    monkeypatch.setattr(sync, "STORY_ROOT", str(tmp_path / "story"))
    chars = [{"id": "10000021", "name_en": "Amber", "name_zh": "安柏",
              "path": "data/story/character/10000021", "stories": ["details"]}]
    sync.build(chars, {"10000021": _rec()}, only=set(), report={})
    data = json.loads((tmp_path / "character.json").read_text(encoding="utf-8"))
    assert len(data) == 1
    assert not (tmp_path / "story").exists()
