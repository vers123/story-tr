# -*- coding: utf-8 -*-
"""批量状态文件：原子写、备份恢复、去抖写入。"""
import json
import threading

from story_tr import stories


def _state_file(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    monkeypatch.setattr(stories, "STATE_PATH", str(path))
    return path


def test_state_writer_debounces(tmp_path, monkeypatch):
    path = _state_file(tmp_path, monkeypatch)
    calls = {"n": 0}
    real_save = stories.save_state

    def counting_save(state):
        calls["n"] += 1
        real_save(state)

    monkeypatch.setattr(stories, "save_state", counting_save)
    state = {"signature": {}, "completed": []}
    completed = set()
    writer = stories._StateWriter(state, completed, threading.Lock(), every=50)

    for i in range(49):
        completed.add("k%d" % i)
        writer.mark()
    writer.flush()
    assert calls["n"] == 0

    completed.add("k49")
    writer.mark()
    writer.flush()
    assert calls["n"] == 1
    data = json.loads(path.read_text(encoding="utf-8"))
    assert len(data["completed"]) == 50
    assert data["completed"] == sorted(data["completed"])

    writer.flush(force=True)
    assert calls["n"] == 2


def test_save_state_is_atomic_and_keeps_backup(tmp_path, monkeypatch):
    path = _state_file(tmp_path, monkeypatch)
    stories.save_state({"completed": ["a"]})
    stories.save_state({"completed": ["a", "b"]})

    assert path.is_file()
    assert not (tmp_path / "state.json.tmp").exists()
    backup = tmp_path / "state.json.bak"
    assert backup.is_file()
    assert json.loads(backup.read_text(encoding="utf-8"))["completed"] == ["a"]
    assert json.loads(path.read_text(encoding="utf-8"))["completed"] == ["a", "b"]


def test_load_state_recovers_from_backup(tmp_path, monkeypatch):
    path = _state_file(tmp_path, monkeypatch)
    stories.save_state({"completed": ["a"]})
    stories.save_state({"completed": ["a", "b"]})  # .bak 保存上一版
    path.write_text("{ truncated", encoding="utf-8")

    recovered = stories.load_state()
    assert recovered is not None
    assert recovered["completed"] == ["a"]


def test_load_state_quarantines_corrupt_file(tmp_path, monkeypatch):
    path = _state_file(tmp_path, monkeypatch)
    path.write_text("{ truncated", encoding="utf-8")

    assert stories.load_state() is None
    corrupt = list(tmp_path.glob("state.json.corrupt-*"))
    assert len(corrupt) == 1
    assert not path.exists()
