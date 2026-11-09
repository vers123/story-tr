"""config：`.env` 解析与工作目录查找。"""

import os
from types import SimpleNamespace

from story_tr import config as config_mod


def test_unescape_controls():
    assert config_mod._unescape("") == ""
    assert config_mod._unescape(r"a\nb\tc") == "a\nb\tc"


def test_resolve_data_dir_env(tmp_path, monkeypatch):
    monkeypatch.setattr(config_mod, "load_settings", lambda: SimpleNamespace(data_dir=str(tmp_path)))
    assert config_mod.resolve_data_dir(None) == os.path.abspath(str(tmp_path))


def test_resolve_data_dir_walk_finds_marker(tmp_path, monkeypatch):
    marker_dir = tmp_path / "marker"
    (marker_dir / "data").mkdir(parents=True)
    (marker_dir / "data" / "character.json").write_text("[]", encoding="utf-8")
    monkeypatch.setattr(config_mod, "load_settings", lambda: SimpleNamespace(data_dir=""))
    monkeypatch.setattr(os, "getcwd", lambda: str(marker_dir))
    assert config_mod.resolve_data_dir(None) == str(marker_dir)


def test_resolve_data_dir_walk_to_cwd(tmp_path, monkeypatch):
    monkeypatch.setattr(config_mod, "load_settings", lambda: SimpleNamespace(data_dir=""))
    monkeypatch.setattr(config_mod, "_MARKER", "data/_nonexistent_marker_xyz.json")
    monkeypatch.setattr(os, "getcwd", lambda: str(tmp_path))
    assert config_mod.resolve_data_dir(None) == str(tmp_path)
