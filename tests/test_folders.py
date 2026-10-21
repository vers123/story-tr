# -*- coding: utf-8 -*-
"""`story_tr.folders`：按 character.json 的 stories 重命名故事文件夹。"""
import json
import types

from story_tr import folders


def _setup(tmp_path, monkeypatch, stories, current):
    root = tmp_path
    monkeypatch.setattr(folders, "ROOT", str(root))
    monkeypatch.setattr(folders, "CHAR_JSON", str(root / "data" / "character.json"))
    (root / "data").mkdir(parents=True)
    (root / "data" / "character.json").write_text(
        json.dumps([{"id": "1", "stories": stories}], ensure_ascii=False), encoding="utf-8")
    sdir = root / "data" / "story" / "character" / "1" / "profile" / "story"
    sdir.mkdir(parents=True)
    for name in current:
        (sdir / name).mkdir()
    return sdir


def _args():
    return types.SimpleNamespace(quiet=True, verbose=False)


def test_renames_leftover_folders(tmp_path, monkeypatch):
    sdir = _setup(tmp_path, monkeypatch,
                  ["character_details", "character_story_1", "an_eye_mask"],
                  ["character_details", "character_story_1", "old_slug"])
    assert folders.main(_args()) == 0
    assert (sdir / "an_eye_mask").is_dir()
    assert not (sdir / "old_slug").exists()


def test_second_run_is_idempotent(tmp_path, monkeypatch):
    sdir = _setup(tmp_path, monkeypatch,
                  ["character_details", "an_eye_mask"],
                  ["character_details", "old_slug"])
    assert folders.main(_args()) == 0
    assert folders.main(_args()) == 0
    assert (sdir / "an_eye_mask").is_dir()


def test_mismatched_counts_warns_and_returns_1(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, ["vision", "an_eye_mask"], ["old_slug"])
    assert folders.main(_args()) == 1
