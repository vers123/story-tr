# -*- coding: utf-8 -*-
"""`story_tr.status`：只读统计与渲染。"""
import json
import types

from story_tr import status, stories


def _workspace(tmp_path, monkeypatch):
    data = tmp_path / "data"
    char_dir = data / "story" / "character" / "1"
    story_dir = char_dir / "profile" / "story" / "vision"
    story_dir.mkdir(parents=True)
    (story_dir / "zh.md").write_text("第一段\n\n第二段\n", encoding="utf-8")
    rdir = char_dir / "result" / "vision"
    rdir.mkdir(parents=True)
    (rdir / "seg_01.json").write_text(json.dumps({"source": "第一段"}), encoding="utf-8")
    (rdir / "seg_01.mp3").write_bytes(b"x")
    (rdir / "segments.json").write_text("{}", encoding="utf-8")
    (rdir / "title.json").write_text("{}", encoding="utf-8")
    vdir = char_dir / "result" / "video"
    vdir.mkdir()
    (vdir / "1.mp4").write_bytes(b"x" * 10)
    (data / "character.json").write_text(json.dumps([
        {"id": "1", "name_zh": "甲", "name_en": "A", "stories": ["vision"]}]),
        encoding="utf-8")
    (tmp_path / "state.json").write_text(json.dumps({
        "completed": ["1|vision|seg_01"], "updated_at": "2026-01-01T00:00:00",
        "signature": {"mode": "fixed", "chain": "asia", "steps": None,
                      "parts": "both", "provider": None}}), encoding="utf-8")

    monkeypatch.setenv("STORY_TR_HOME", str(tmp_path))
    monkeypatch.setattr(stories, "STORY_ROOT", str(data / "story" / "character"))
    monkeypatch.setattr(stories, "CHAR_FILE", str(data / "character.json"))
    monkeypatch.setattr(stories, "STATE_PATH", str(tmp_path / "state.json"))
    return tmp_path


def test_collect_counts(tmp_path, monkeypatch):
    _workspace(tmp_path, monkeypatch)
    info = status.collect()
    assert info["characters"] == 1
    assert info["stories"] == 1
    assert info["stories_with_result"] == 1
    assert info["segments"] == 1
    assert info["segment_mp3"] == 1
    assert info["summaries"] == 1
    assert info["titles"] == 1
    assert info["fulls"] == 0
    assert info["videos"] == 1
    assert info["state"]["completed"] == 1


def test_render_contains_key_lines(tmp_path, monkeypatch):
    _workspace(tmp_path, monkeypatch)
    text = status.render(status.collect())
    assert "角色：1" in text
    assert "段落结果：1 个 JSON" in text
    assert "批量状态：1 条完成" in text


def test_main_json_output(tmp_path, monkeypatch, capsys):
    _workspace(tmp_path, monkeypatch)
    rc = status.main(types.SimpleNamespace(json=True, deep=False))
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["characters"] == 1


def test_main_missing_character_file(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("STORY_TR_HOME", str(tmp_path))
    monkeypatch.setattr(stories, "CHAR_FILE", str(tmp_path / "nope.json"))
    assert status.main(types.SimpleNamespace(json=False, deep=False)) == 1
    assert "找不到" in capsys.readouterr().out


def test_write_manifest(tmp_path, monkeypatch):
    _workspace(tmp_path, monkeypatch)
    (tmp_path / "data" / "_fetch_all.json").write_text(
        json.dumps([{"id": "1", "hash": "abc"}]), encoding="utf-8")
    path = status.write_manifest()
    data = json.loads((tmp_path / "data" / "manifest.json").read_text(encoding="utf-8"))
    assert path.endswith("manifest.json")
    assert data["characters"] == 1 and data["stories"] == 1
    assert data["hashes"] == {"1": "abc"}
