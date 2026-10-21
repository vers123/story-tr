# -*- coding: utf-8 -*-
"""`story_tr.doctor`：环境检查不因可选依赖缺失而 FAIL，但缺数据要 FAIL。"""
import json
import types

from story_tr import doctor, stories


def _setup(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    (data / "character.json").write_text(json.dumps([]), encoding="utf-8")
    monkeypatch.setenv("STORY_TR_HOME", str(tmp_path))
    monkeypatch.setattr(stories, "CHAR_FILE", str(data / "character.json"))
    monkeypatch.setattr(stories, "STORY_ROOT", str(data / "story" / "character"))
    monkeypatch.setattr(stories, "STATE_PATH", str(tmp_path / "state.json"))
    monkeypatch.setattr(doctor.shutil, "which", lambda name: None)


def test_collect_has_no_fail_when_data_exists(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    rows = doctor.collect()
    assert not [r for r in rows if r[0] == doctor.FAIL]
    assert any("ffmpeg" in name for _, name, _ in rows)


def test_main_returns_0_with_data(tmp_path, monkeypatch, capsys):
    _setup(tmp_path, monkeypatch)
    assert doctor.main(types.SimpleNamespace()) == 0
    out = capsys.readouterr().out
    assert "[ OK ]" in out and "检查通过" in out


def test_main_returns_1_without_character_index(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("STORY_TR_HOME", str(tmp_path))
    monkeypatch.setattr(stories, "CHAR_FILE", str(tmp_path / "missing.json"))
    assert doctor.main(types.SimpleNamespace()) == 1
    assert "检查未通过" in capsys.readouterr().out
