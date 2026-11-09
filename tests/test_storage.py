"""storage：原子读写、备份恢复与后端解析。"""

import json
from pathlib import Path

from story_tr import models
from story_tr.services import storage


def test_atomic_write_json_with_backup(tmp_path):
    p = str(tmp_path / "x.json")
    storage.atomic_write_json(p, {"a": 1})
    storage.atomic_write_json(p, {"a": 2})
    assert json.loads(Path(p).read_text(encoding="utf-8")) == {"a": 2}
    assert json.loads(Path(p + ".bak").read_text(encoding="utf-8")) == {"a": 1}


def test_atomic_write_json_pydantic(tmp_path):
    p = str(tmp_path / "m.json")
    storage.atomic_write_json(p, models.BatchState(signature={}, started_at="2026-01-01T00:00:00"))
    data = json.loads(Path(p).read_text(encoding="utf-8"))
    assert data["started_at"] == "2026-01-01T00:00:00"


def test_atomic_write_text_skips_unchanged(tmp_path):
    p = str(tmp_path / "a.txt")
    assert storage.atomic_write_text(p, "a", backup=True) is True
    assert storage.atomic_write_text(p, "a", backup=True) is False
    assert storage.atomic_write_text(p, "b", backup=True) is True
    assert Path(p).read_text(encoding="utf-8") == "b"
    assert Path(p + ".bak").read_text(encoding="utf-8") == "a"


def test_load_json_with_backup_restores(tmp_path):
    p = str(tmp_path / "s.json")
    Path(p).write_text("{bad json", encoding="utf-8")
    Path(p + ".bak").write_text('{"ok": true}', encoding="utf-8")
    assert storage.load_json_with_backup(p) == {"ok": True}


def test_load_json_with_backup_corrupt_no_backup(tmp_path):
    p = str(tmp_path / "s.json")
    Path(p).write_text("{bad json", encoding="utf-8")
    assert storage.load_json_with_backup(p) is None
    assert list(Path(tmp_path).glob("s.json.corrupt-*"))


def test_load_json_with_backup_both_corrupt(tmp_path):
    p = str(tmp_path / "s.json")
    Path(p).write_text("{bad", encoding="utf-8")
    Path(p + ".bak").write_text("{also bad", encoding="utf-8")
    assert storage.load_json_with_backup(p) is None


def test_remove_with_backup(tmp_path):
    p = tmp_path / "s.json"
    p.write_text("x", encoding="utf-8")
    storage.remove_with_backup(str(p))
    assert not p.exists()
    assert Path(str(p) + ".bak").exists()


def test_resolve_providers():
    assert storage.resolve_providers("auto", "fixed") == (["google", "bing", "baidu"], None)
    assert storage.resolve_providers(None, "fixed") == (["google", "bing", "baidu"], None)
    assert storage.resolve_providers("google", "fixed") == (["google"], None)
    assert storage.resolve_providers("rwkv", "rwkv20") == (["rwkv"], None)
    assert storage.resolve_providers("google", "rwkv20")[1] is not None
    assert storage.resolve_providers("rwkv", "fixed")[1] is not None
    assert storage.resolve_providers("yandex", "fixed")[1] is not None
