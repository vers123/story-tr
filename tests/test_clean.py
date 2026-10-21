# -*- coding: utf-8 -*-
"""`story_tr.clean`：目标枚举、大小统计、状态记录清理。"""
import json
import os
import types

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


# --------------------------------------------------------------------------- #
# 视频产物默认保护
# --------------------------------------------------------------------------- #
def _make_video(tmp_path, cid):
    d = tmp_path / cid / "result" / "video"
    d.mkdir(parents=True)
    (d / (cid + ".mp4")).write_bytes(b"x" * 10)
    return d


def test_targets_protects_video_by_default(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    _make_result(tmp_path, "10000021", "amber_journal")
    video = _make_video(tmp_path, "10000021")

    assert [os.path.basename(p) for p in clean.targets("10000021")] == ["amber_journal"]
    # 显式指定 story 名时才允许清理视频
    assert [os.path.basename(p) for p in clean.targets("10000021", ["video"])] == ["video"]
    assert video.exists()


def test_remove_default_keeps_video(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    story = _make_result(tmp_path, "10000021", "amber_journal")
    video = _make_video(tmp_path, "10000021")

    clean.remove(clean.targets("10000021"))
    assert not story.exists()
    assert video.is_dir()
    assert (video / "10000021.mp4").is_file()


def test_main_dry_run_skips_video(tmp_path, monkeypatch, capsys):
    _setup(tmp_path, monkeypatch)
    _make_result(tmp_path, "10000021", "amber_journal")
    _make_video(tmp_path, "10000021")
    (tmp_path / "character.json").write_text(
        '[{"id": "10000021", "stories": ["amber_journal"]}]', encoding="utf-8")
    monkeypatch.setattr(stories, "CHAR_FILE", str(tmp_path / "character.json"))

    rc = clean.main(types.SimpleNamespace(
        char_ids=["10000021"], all_characters=False, stories="", dry_run=True,
        yes=False, keep_state=False, quiet=False, verbose=False))
    out = capsys.readouterr().out
    assert rc == 0
    assert "result/video/" in out
    assert "--stories video" in out
    assert (tmp_path / "10000021" / "result" / "amber_journal").is_dir()
    assert (tmp_path / "10000021" / "result" / "video").is_dir()
