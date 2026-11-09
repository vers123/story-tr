"""services.clean：结果清理与状态重置。"""

import os

import pytest

from story_tr import models
from story_tr.errors import ExitCode
from story_tr.services import clean as clean_mod
from story_tr.services import storage, story_inputs
from tests.support import setup_result_files as _setup_result_files
from tests.support import setup_workspace as _setup_workspace


def test_clean_dry_run_keeps_files(tmp_path):
    rdir = _setup_result_files(tmp_path)
    rc = clean_mod.run(["10000021"], dry_run=True, data_dir=str(tmp_path))
    assert rc == ExitCode.OK
    assert (rdir / "seg_01.json").exists()


def test_clean_executes_and_keeps_result_dir(tmp_path):
    rdir = _setup_result_files(tmp_path)
    rc = clean_mod.run(["10000021"], yes=True, data_dir=str(tmp_path))
    assert rc == ExitCode.OK
    result_root = tmp_path / "data" / "story" / "character" / "10000021" / "result"
    assert result_root.exists()
    assert not (rdir / "seg_01.json").exists()


def test_clean_parse_char_ids():
    assert story_inputs.parse_char_ids(["1-3"], ["1", "2", "3"]) == ["1", "2", "3"]
    with pytest.raises(ValueError):
        story_inputs.parse_char_ids(["a-b"], ["1"])


def test_clean_state_key_match():
    assert clean_mod._state_key_match("1|vision|seg_01", {"1"}, None) is True
    assert clean_mod._state_key_match("1|vision|seg_01", {"1"}, ["vision"]) is True
    assert clean_mod._state_key_match("1|vision|seg_01", {"1"}, ["other"]) is False
    assert clean_mod._state_key_match("1|vision|seg_01", {"2"}, None) is False
    assert clean_mod._state_key_match("bad", {"1"}, None) is False


def test_clean_state_removes_hits(tmp_path):
    home = str(tmp_path)
    state = models.BatchState(
        signature={"mode": "fixed"}, completed=["10000021|vision|seg_01"], started_at="2026-01-01T00:00:00"
    )
    storage.atomic_write_json(os.path.join(home, clean_mod.STATE_FILENAME), state)
    hits = clean_mod._clean_state(home, ["10000021"], None, dry_run=False)
    assert hits == 1
    raw = storage.load_json_with_backup(os.path.join(home, clean_mod.STATE_FILENAME))
    assert raw["completed"] == []


def test_clean_usage_errors(tmp_path):
    assert clean_mod.run([], data_dir=str(tmp_path)) == ExitCode.USAGE
    assert clean_mod.run(["1"], all_characters=True, data_dir=str(tmp_path)) == ExitCode.USAGE


def test_clean_no_match(tmp_path):
    _setup_workspace(tmp_path)
    assert clean_mod.run(["999"], data_dir=str(tmp_path)) == ExitCode.USAGE


def test_clean_confirm_cancel(tmp_path, monkeypatch):
    _setup_workspace(tmp_path)
    rdir = tmp_path / "data" / "story" / "character" / "10000021" / "result" / "vision"
    rdir.mkdir(parents=True)
    (rdir / "seg_01.json").write_text('{"source": "第一段"}', encoding="utf-8")
    monkeypatch.setattr("builtins.input", lambda *a: "n")
    assert clean_mod.run(["10000021"], data_dir=str(tmp_path)) == ExitCode.OK
    assert (rdir / "seg_01.json").exists()


def test_clean_targets_protect_video(tmp_path):
    _setup_workspace(tmp_path)
    rdir = tmp_path / "data" / "story" / "character" / "10000021" / "result"
    (rdir / "video").mkdir(parents=True)
    (rdir / "vision").mkdir()
    targets = clean_mod._targets(str(tmp_path), "10000021", None)
    assert [os.path.basename(t) for t in targets] == ["vision"]


def test_clean_targets_named_and_missing(tmp_path):
    _setup_workspace(tmp_path)
    rdir = tmp_path / "data" / "story" / "character" / "10000021" / "result"
    (rdir / "vision").mkdir(parents=True)
    (rdir / "video").mkdir()
    assert clean_mod._targets(str(tmp_path), "404", None) == []
    named = clean_mod._targets(str(tmp_path), "10000021", ["vision", "nope"])
    assert [os.path.basename(t) for t in named] == ["vision"]


def test_clean_measure_and_remove_file(tmp_path):
    target = tmp_path / "a.json"
    target.write_text("x", encoding="utf-8")
    assert clean_mod._measure([str(target)]) == (1, 1)
    clean_mod._remove([str(target)])
    assert not target.exists()
    assert clean_mod._measure([str(target)]) == (0, 0)


def test_clean_dry_run_and_keep_state(tmp_path):
    _setup_workspace(tmp_path)
    rdir = tmp_path / "data" / "story" / "character" / "10000021" / "result" / "vision"
    rdir.mkdir(parents=True)
    (rdir / "seg_01.json").write_text("{}", encoding="utf-8")
    state = models.BatchState(
        signature={}, completed=["10000021|vision|seg_01"], started_at="2026-01-01T00:00:00"
    )
    storage.atomic_write_json(str(tmp_path / clean_mod.STATE_FILENAME), state)

    assert clean_mod.run(["10000021"], dry_run=True, data_dir=str(tmp_path)) == ExitCode.OK
    assert (rdir / "seg_01.json").exists()
    assert clean_mod.run(["10000021"], yes=True, keep_state=True, data_dir=str(tmp_path)) == ExitCode.OK
    assert not (rdir / "seg_01.json").exists()
    raw = storage.load_json_with_backup(str(tmp_path / clean_mod.STATE_FILENAME))
    assert raw["completed"] == ["10000021|vision|seg_01"]


def test_clean_cancel_on_eof_and_nothing_to_do(tmp_path, monkeypatch):
    _setup_workspace(tmp_path)
    assert clean_mod.run(["10000021"], data_dir=str(tmp_path)) == ExitCode.OK
    rdir = tmp_path / "data" / "story" / "character" / "10000021" / "result" / "vision"
    rdir.mkdir(parents=True)
    (rdir / "seg_01.json").write_text("{}", encoding="utf-8")

    def boom(*args):
        raise EOFError

    monkeypatch.setattr("builtins.input", boom)
    assert clean_mod.run(["10000021"], data_dir=str(tmp_path)) == ExitCode.OK
    assert (rdir / "seg_01.json").exists()
