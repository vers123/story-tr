# -*- coding: utf-8 -*-
"""工作目录解析优先级：--data-dir / $STORY_TR_HOME / 向上查找 / CWD。"""
import os

from story_tr import paths


def test_env_takes_priority(tmp_path, monkeypatch):
    monkeypatch.setenv("STORY_TR_HOME", str(tmp_path))
    assert paths.home() == os.path.abspath(str(tmp_path))


def test_find_upwards(tmp_path, monkeypatch):
    monkeypatch.delenv("STORY_TR_HOME", raising=False)
    data = tmp_path / "data"
    data.mkdir()
    (data / "character.json").write_text("[]", encoding="utf-8")
    deep = tmp_path / "a" / "b"
    deep.mkdir(parents=True)
    monkeypatch.chdir(deep)
    assert paths.home() == os.path.abspath(str(tmp_path))


def test_fallback_to_cwd(tmp_path, monkeypatch):
    monkeypatch.delenv("STORY_TR_HOME", raising=False)
    monkeypatch.chdir(tmp_path)
    assert paths.home() == os.path.abspath(str(tmp_path))


def test_resolve_prefers_cli_value(tmp_path, monkeypatch):
    monkeypatch.setenv("STORY_TR_HOME", os.path.join(str(tmp_path), "nonexistent"))
    assert paths.resolve(str(tmp_path)) == os.path.abspath(str(tmp_path))
