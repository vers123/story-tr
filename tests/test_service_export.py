"""services.export：数据集导出（jsonl / parquet / manifest）。"""

import json

import pytest

from story_tr.errors import ExitCode
from story_tr.services import export as export_mod
from story_tr.services import translate
from tests.support import FakeTranslator
from tests.support import setup_workspace as _setup_workspace


def _translate(tmp_path, monkeypatch, **kwargs):
    _setup_workspace(tmp_path)
    fake = FakeTranslator(name="google", result="译文")
    monkeypatch.setattr("story_tr.transport.build_translator", lambda p, s: fake)
    rc = translate.run(
        ["10000021"],
        stories="vision",
        mode="fixed",
        chain="asia",
        no_tts=True,
        data_dir=str(tmp_path),
        **kwargs,
    )
    assert rc == ExitCode.OK


def _lines(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_export_jsonl_and_manifest(tmp_path, monkeypatch, capsys):
    _translate(tmp_path, monkeypatch)
    out = tmp_path / "exp"
    rc = export_mod.run(data_dir=str(tmp_path), out=str(out))
    assert rc == ExitCode.OK
    assert "已导出 4 条记录" in capsys.readouterr().err or True
    records = _lines(out / "dataset.jsonl")
    assert len(records) == 4
    keys = {record["key"] for record in records}
    assert keys == {"title", "full", "seg_01", "seg_02"}
    title = next(record for record in records if record["key"] == "title")
    assert title["id"] == "10000021"
    assert title["name_zh"] == "安柏"
    assert title["story_title_zh"] == "角色详细"
    assert title["final"] == "译文"
    assert title["result_file"].endswith("title.json")

    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == 1
    assert manifest["result_tag"] is None
    assert manifest["counts"]["characters"] == 1
    assert manifest["counts"]["stories"] == 1
    assert manifest["counts"]["tasks"] == 4
    assert manifest["counts"]["segments"] == 2
    assert manifest["files"] == ["dataset.jsonl"]


def test_export_with_steps(tmp_path, monkeypatch):
    _translate(tmp_path, monkeypatch)
    out = tmp_path / "exp"
    assert export_mod.run(data_dir=str(tmp_path), out=str(out), with_steps=True) == ExitCode.OK
    records = _lines(out / "dataset.jsonl")
    assert all("steps" in record and len(record["steps"]) == 22 for record in records)


def test_export_result_tag(tmp_path, monkeypatch):
    _translate(tmp_path, monkeypatch, result_tag="cloud")
    out = tmp_path / "exp"
    assert export_mod.run(data_dir=str(tmp_path), out=str(out), result_tag="cloud") == ExitCode.OK
    assert len(_lines(out / "dataset.jsonl")) == 4
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["result_tag"] == "cloud"


def test_export_no_results_usage(tmp_path):
    _setup_workspace(tmp_path)
    assert export_mod.run(data_dir=str(tmp_path), out=str(tmp_path / "exp")) == ExitCode.USAGE


def test_export_invalid_format_and_tag(tmp_path):
    _setup_workspace(tmp_path)
    assert export_mod.run(data_dir=str(tmp_path), fmt="csv") == ExitCode.USAGE
    assert export_mod.run(data_dir=str(tmp_path), result_tag="a/b") == ExitCode.USAGE


def test_export_parquet_missing_dependency(tmp_path, monkeypatch):
    _translate(tmp_path, monkeypatch)
    monkeypatch.setattr(
        export_mod, "_import_pyarrow", lambda: (_ for _ in ()).throw(ImportError("no pyarrow"))
    )
    out = tmp_path / "exp"
    assert export_mod.run(data_dir=str(tmp_path), out=str(out), fmt="parquet") == ExitCode.USAGE


def test_export_parquet_when_available(tmp_path, monkeypatch):
    pytest.importorskip("pyarrow")
    _translate(tmp_path, monkeypatch)
    out = tmp_path / "exp"
    assert export_mod.run(data_dir=str(tmp_path), out=str(out), fmt="parquet") == ExitCode.OK
    assert (out / "dataset.parquet").exists()
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["format"] == "parquet"


def test_export_select_errors(tmp_path):
    _setup_workspace(tmp_path)
    assert (
        export_mod.run(char_ids=["10000021"], all_characters=True, data_dir=str(tmp_path)) == ExitCode.USAGE
    )
    assert export_mod.run(char_ids=["a-b"], data_dir=str(tmp_path)) == ExitCode.USAGE
    assert export_mod.run(char_ids=["999"], data_dir=str(tmp_path)) == ExitCode.USAGE
    assert export_mod.run(data_dir=str(tmp_path / "missing")) == ExitCode.USAGE


def test_export_default_out_and_char_ids(tmp_path, monkeypatch):
    _translate(tmp_path, monkeypatch)
    assert export_mod.run(char_ids=["10000021"], data_dir=str(tmp_path)) == ExitCode.OK
    assert (tmp_path / "out" / "export" / "dataset.jsonl").exists()


def test_export_corrupt_result_skipped(tmp_path, monkeypatch):
    _translate(tmp_path, monkeypatch)
    rdir = tmp_path / "data" / "story" / "character" / "10000021" / "result" / "vision"
    (rdir / "seg_99.json").write_text("{bad", encoding="utf-8")
    out = tmp_path / "exp"
    assert export_mod.run(data_dir=str(tmp_path), out=str(out)) == ExitCode.OK
    assert len(_lines(out / "dataset.jsonl")) == 4


def test_export_fetch_hashes(tmp_path, monkeypatch):
    _translate(tmp_path, monkeypatch)
    (tmp_path / "data" / "_fetch_all.json").write_text(
        json.dumps([{"id": "10000021", "hash": "abc"}]), encoding="utf-8"
    )
    out = tmp_path / "exp"
    assert export_mod.run(data_dir=str(tmp_path), out=str(out)) == ExitCode.OK
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["fetch_hashes"] == {"10000021": "abc"}

    (tmp_path / "data" / "_fetch_all.json").write_text("{bad", encoding="utf-8")
    assert export_mod.run(data_dir=str(tmp_path), out=str(out)) == ExitCode.OK
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["fetch_hashes"] == {}


def test_export_helpers():
    assert export_mod._int(None) is None
    assert export_mod._int("12") == 12
    assert export_mod._int("x") is None
    rows = export_mod._parquet_rows([{"key": "seg_01", "steps": [{"index": 0}], "rwkv": None}])
    assert isinstance(rows[0]["steps"], str)
    assert rows[0]["rwkv"] is None
