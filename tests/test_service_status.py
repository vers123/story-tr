"""services.status / status_scan：报告采集与渲染。"""

import json
from pathlib import Path

from story_tr.errors import ExitCode
from story_tr.services import status as status_mod
from story_tr.services import status_scan
from tests.support import setup_result_files as _setup_result_files
from tests.support import setup_workspace as _setup_workspace
from tests.support import status_info


def test_status_collect_counts(tmp_path):
    rdir = _setup_result_files(tmp_path)
    (rdir / "seg_01.mp3").write_bytes(b"x")
    (rdir / "segments.json").write_text("{}", encoding="utf-8")
    (rdir / "title.json").write_text("{}", encoding="utf-8")
    info = status_mod.collect(str(tmp_path))
    assert info["characters"] == 1
    assert info["stories"] == 1
    assert info["stories_with_result"] == 1
    assert info["segments"] == 1
    assert info["segment_mp3"] == 1
    assert info["summaries"] == 1
    assert info["titles"] == 1


def test_status_manifest(tmp_path):
    _setup_workspace(tmp_path)
    rc = status_mod.run(manifest=True, data_dir=str(tmp_path))
    assert rc == ExitCode.OK
    assert (tmp_path / "data" / "manifest.json").exists()


def test_status_read_state_missing(tmp_path):
    assert status_mod._read_state(str(tmp_path / "none.json")) == (None, None)


def test_status_collect_deep_and_state_error(tmp_path):
    _setup_workspace(tmp_path)
    rdir = tmp_path / "data" / "story" / "character" / "10000021" / "result" / "vision"
    rdir.mkdir(parents=True)
    (rdir / "seg_01.json").write_text(json.dumps({"source": "x", "source_lang": "ja"}), encoding="utf-8")
    info = status_mod.collect(str(tmp_path), deep=True)
    assert info["languages"] == {"ja": 1}
    # 损坏状态文件 → state_error
    (tmp_path / status_mod.STATE_FILENAME).write_text("{bad", encoding="utf-8")
    info = status_mod.collect(str(tmp_path))
    assert info["state_error"] is not None


def test_status_collect_counts_single_scan(tmp_path):
    _setup_workspace(tmp_path)
    rdir = tmp_path / "data" / "story" / "character" / "10000021" / "result" / "vision"
    rdir.mkdir(parents=True)
    (rdir / "seg_01.json").write_text(json.dumps({"source": "x"}), encoding="utf-8")
    (rdir / "seg_02.json").write_text(json.dumps({"source": "y"}), encoding="utf-8")
    (rdir / "seg_01.mp3").write_bytes(b"mp3")
    (rdir / "title.json").write_text("{}", encoding="utf-8")
    (rdir / "full.json").write_text("{}", encoding="utf-8")
    (rdir / "segments.json").write_text("{}", encoding="utf-8")

    info = status_mod.collect(str(tmp_path))
    assert info["characters_with_result"] == 1
    assert info["characters_without_result"] == 0
    assert info["stories_with_result"] == 1
    assert info["stories_without_result"] == 0
    assert info["segments"] == 2
    assert info["segment_mp3"] == 1
    assert info["summaries"] == info["titles"] == info["fulls"] == 1


def test_status_render_rwkv_and_state(tmp_path):
    _setup_workspace(tmp_path)
    info = {
        "workdir": str(tmp_path),
        "characters": 1,
        "characters_with_result": 0,
        "characters_without_result": 1,
        "stories": 1,
        "stories_with_result": 0,
        "stories_without_result": 1,
        "summaries": 0,
        "titles": 0,
        "fulls": 0,
        "segments": 0,
        "segment_mp3": 0,
        "data_files": 0,
        "data_bytes": 0,
        "log_files": 0,
        "log_bytes": 0,
        "state": {"completed": 1, "updated_at": "2026-01-01T00:00:00", "signature": {"mode": "fixed"}},
        "state_error": None,
        "languages": None,
        "rwkv": {
            "url": "http://127.0.0.1:8000",
            "template_present": True,
            "model_path": "/nope/model.pth",
            "model_path_exists": False,
            "probe": None,
        },
    }
    text = status_mod.render(info)
    assert "模型文件不存在" in text
    assert "批量状态" in text


def test_status_run_json(tmp_path):
    _setup_workspace(tmp_path)
    assert status_mod.run(json_out=True, data_dir=str(tmp_path)) == ExitCode.OK


def test_status_write_manifest_with_cache(tmp_path):
    _setup_workspace(tmp_path)
    cache = tmp_path / "data" / "_fetch_all.json"
    cache.write_text(json.dumps([{"id": "10000021", "hash": "abc"}]), encoding="utf-8")
    path = status_mod.write_manifest(str(tmp_path), status_mod.collect(str(tmp_path)))
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    assert data["hashes"]["10000021"] == "abc"


def test_status_segment_lang_and_dir_size_missing(tmp_path):
    bad = tmp_path / "a.json"
    bad.write_text("{bad", encoding="utf-8")
    assert status_scan.segment_lang(str(bad)) == "unknown"
    bad.write_text("[]", encoding="utf-8")
    assert status_scan.segment_lang(str(bad)) == "unknown"
    assert status_scan.segment_lang(str(tmp_path / "none.json")) == "unknown"
    assert status_scan.dir_size(str(tmp_path / "missing")) == (0, 0)


def test_status_collect_skips_non_dirs_and_corrupt_segments(tmp_path):
    home = str(tmp_path)
    root = tmp_path / "data" / "story" / "character"
    root.mkdir(parents=True)
    (root / "afile").write_text("x", encoding="utf-8")
    (root / "1").mkdir()
    (root / "1" / "result").write_text("not a dir", encoding="utf-8")
    story = root / "2" / "result" / "vision"
    story.mkdir(parents=True)
    (root / "2" / "result" / "afile").write_text("x", encoding="utf-8")
    (story / "seg_01.json").write_text("{bad", encoding="utf-8")
    (story / "seg_02.json").write_text("[]", encoding="utf-8")
    (tmp_path / "data" / "character.json").write_text(
        json.dumps([{"id": "2", "stories": ["vision"]}]), encoding="utf-8"
    )
    info = status_mod.collect(home, deep=True)
    assert info["languages"] == {"unknown": 2}
    assert info["segments"] == 2
    assert info["characters_with_result"] == 1


def test_status_collect_state_and_probe(tmp_path, monkeypatch):
    _setup_workspace(tmp_path)
    (tmp_path / status_mod.STATE_FILENAME).write_text(
        json.dumps({"completed": ["a"], "updated_at": "t", "signature": {"mode": "fixed"}}),
        encoding="utf-8",
    )

    class Fake:
        def __init__(self, settings):
            pass

        def probe(self):
            return {"reachable": True, "status": "Working", "model": "m"}

    monkeypatch.setattr(status_mod, "RwkvTranslator", Fake)
    info = status_mod.collect(str(tmp_path), probe=True)
    assert info["state"]["completed"] == 1
    assert info["rwkv"]["probe"]["reachable"] is True


def test_status_render_rwkv_branches():
    text = status_mod.render(status_info())
    assert "未配置 RWKV_MODEL_PATH" in text

    rwkv = {
        "url": "u",
        "template_present": True,
        "model_path": "/m",
        "model_path_exists": True,
        "probe": {"reachable": True, "status": "Working", "model": "m"},
    }
    text = status_mod.render(status_info(rwkv=rwkv))
    assert "模型文件存在" in text
    assert "服务 Working" in text

    rwkv = {
        "url": "u",
        "template_present": True,
        "model_path": "/m",
        "model_path_exists": False,
        "probe": {"reachable": False, "error": "down"},
    }
    text = status_mod.render(status_info(rwkv=rwkv))
    assert "模型文件不存在" in text
    assert "服务不可达" in text

    text = status_mod.render(status_info(state=None, state_error="bad", languages={"ja": 1}))
    assert "读取失败" in text
    assert "源语言分布" in text
    assert "批量状态：无" in status_mod.render(status_info())


def test_status_write_manifest_bad_cache(tmp_path):
    _setup_workspace(tmp_path)
    (tmp_path / "data" / "_fetch_all.json").write_text("{bad", encoding="utf-8")
    path = status_mod.write_manifest(str(tmp_path), status_mod.collect(str(tmp_path)))
    assert json.loads(Path(path).read_text(encoding="utf-8"))["hashes"] == {}


def test_status_run_manifest_plain(tmp_path, capsys):
    _setup_workspace(tmp_path)
    assert status_mod.run(manifest=True, data_dir=str(tmp_path)) == ExitCode.OK
    assert "清单已写入" in capsys.readouterr().out
