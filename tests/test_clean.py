# -*- coding: utf-8 -*-
"""`story_tr.clean`：目标枚举、大小统计、状态记录清理。"""
import json
import os

from story_tr import clean, stories


def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr(stories, "STORY_ROOT", str(tmp_path))
    monkeypatch.setattr(stories, "STATE_PATH", str(tmp_path / "state.json"))


def _make_result(tmp_path, cid, story, files=("full.json", "full.mp3", "segments.json")):
    d = tmp_path / cid / "result" / story
    d.mkdir(parents=True)
    for f in files:
        (d / f).write_bytes(b"x" * 10)
    return d


def test_targets_all_and_filtered(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    _make_result(tmp_path, "10000021", "amber_journal")
    _make_result(tmp_path, "10000021", "vision")
    assert [os.path.basename(p) for p in clean.targets("10000021")] == ["amber_journal", "vision"]
    assert [os.path.basename(p) for p in clean.targets("10000021", ["vision"])] == ["vision"]
    assert clean.targets("10000021", ["nope"]) == []
    assert clean.targets("99999999") == []


def test_remove_keeps_result_dir(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    d = _make_result(tmp_path, "10000021", "amber_journal")
    clean.remove(clean.targets("10000021"))
    assert not d.exists()
    assert (tmp_path / "10000021" / "result").is_dir()  # 保留 result/ 目录本身


def test_measure(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    _make_result(tmp_path, "10000021", "amber_journal")  # 3 个文件 × 10 B
    assert clean.measure(clean.targets("10000021")) == (3, 30)


def test_human():
    assert clean.human(512) == "512 B"
    assert clean.human(2048) == "2.0 KB"
    assert clean.human(3 * 1024 * 1024) == "3.0 MB"


def test_clean_state_filters(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    (tmp_path / "state.json").write_text(json.dumps({
        "signature": {"characters": ["10000021", "10000103"]}, "completed": [
            "10000021|amber_journal|full",
            "10000021|vision|seg_01",
            "10000103|amber_journal|full",
        ]}), encoding="utf-8")

    assert clean.clean_state(["10000021"], None, dry_run=True) == 2
    data = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert "signature" in data  # dry-run 不改动
    assert clean.clean_state(["10000021"], ["vision"]) == 1
    data = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert data["completed"] == ["10000021|amber_journal|full", "10000103|amber_journal|full"]
    assert "signature" not in data  # 清理后重置批次签名，避免下次 translate 要求 --reset


def test_clean_state_noop_without_state(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    assert clean.clean_state(["10000021"]) == 0
