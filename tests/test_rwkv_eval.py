# -*- coding: utf-8 -*-
"""`story_tr.rwkv_eval`：模板解析、指标计算、采样与报告（全部离线，mock 本地翻译）。"""
import json
import types

import pytest

from story_tr import rwkv, rwkv_eval, stories
from story_tr.exitcodes import ExitCode


# --------------------------------------------------------------------------- #
# 指标
# --------------------------------------------------------------------------- #
def test_score_metrics_basic():
    m = rwkv_eval.score_metrics("hello", "你好，世界！", "你好，世界！")
    assert m["zh_ratio"] == pytest.approx(4 / 6, abs=1e-4)
    assert m["len_ratio"] == pytest.approx(6 / 5, abs=1e-4)
    assert m["similarity"] == pytest.approx(1.0)
    assert m["repeat4"] == 0.0


def test_score_metrics_repeat_and_empty():
    m = rwkv_eval.score_metrics("source", "abababab")
    assert m["repeat4"] == pytest.approx(0.6)  # 5 个 4-gram，只有 2 个不同
    assert m["similarity"] is None  # 无基线

    empty = rwkv_eval.score_metrics("", "")
    assert empty == {"zh_ratio": 0.0, "len_ratio": 0.0, "similarity": None, "repeat4": 0.0}


# --------------------------------------------------------------------------- #
# 模板解析
# --------------------------------------------------------------------------- #
def test_parse_templates_builtin():
    templates = rwkv_eval.parse_templates("builtin")
    assert len(templates) == 4
    assert all("{text}" in t for t in templates)


def test_parse_templates_custom_and_empty():
    assert rwkv_eval.parse_templates("a||b") == ["a", "b"]
    assert rwkv_eval.parse_templates("") == []
    assert rwkv_eval.parse_templates("   ") == []


# --------------------------------------------------------------------------- #
# 采样
# --------------------------------------------------------------------------- #
def _make_ws(tmp_path):
    root = tmp_path / "data" / "story" / "character"
    d = root / "1" / "profile" / "story" / "vision"
    d.mkdir(parents=True)
    (d / "zh.md").write_text(
        '---\nid: "1"\nstory: vision\nlang: zh\ntitle: "标题"\n---\n\n第一段\n\n第二段\n',
        encoding="utf-8")
    char_file = tmp_path / "data" / "character.json"
    char_file.write_text(json.dumps([{"id": "1"}], ensure_ascii=False), encoding="utf-8")
    return root, d


def _patch_ws(monkeypatch, root, char_file):
    monkeypatch.setattr(stories, "STORY_ROOT", str(root))
    monkeypatch.setattr(stories, "CHAR_FILE", str(char_file))


def test_sample_items_only_segments_and_limit(tmp_path, monkeypatch):
    root, _ = _make_ws(tmp_path)
    _patch_ws(monkeypatch, root, tmp_path / "data" / "character.json")

    items = rwkv_eval.sample_items(["1"], 20)
    assert [i["key"] for i in items] == ["seg_01", "seg_02"]
    assert [i["text"] for i in items] == ["第一段", "第二段"]
    assert items[0]["story"] == "vision"
    assert all(i["baseline"] is None for i in items)

    assert len(rwkv_eval.sample_items(["1"], 1)) == 1
    assert rwkv_eval.sample_items(["1"], 0) == []


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def _args(tmp_path, **kw):
    base = dict(char_ids=["1"], all_characters=False, stories="", limit=20,
                templates="", out=str(tmp_path / "logs"), rwkv_url="", rwkv_template="")
    base.update(kw)
    return types.SimpleNamespace(**base)


def _write_baseline(root, key, final):
    d = root / "1" / "result" / "vision"
    d.mkdir(parents=True, exist_ok=True)
    (d / (key + ".json")).write_text(
        json.dumps({"source": "第一段", "final": final}, ensure_ascii=False), encoding="utf-8")


def test_run_eval_writes_reports(tmp_path, monkeypatch):
    root, _ = _make_ws(tmp_path)
    _patch_ws(monkeypatch, root, tmp_path / "data" / "character.json")
    _write_baseline(root, "seg_01", "云端译文")
    result_dir = root / "1" / "result" / "vision"
    before = sorted(p.name for p in result_dir.iterdir())

    def fake_translate(text, cfg=None, template=None):
        return {"text": "本地译文", "elapsed_ms": 5}

    monkeypatch.setattr(rwkv_eval.rwkv, "translate", fake_translate)

    rc = rwkv_eval.run_eval(_args(tmp_path))
    assert rc == ExitCode.OK

    reports = sorted((tmp_path / "logs").glob("rwkv_eval_*.json"))
    assert len(reports) == 1
    payload = json.loads(reports[0].read_text(encoding="utf-8"))
    assert payload["samples"] == 2
    assert payload["baseline_missing"] == 1
    assert payload["limit"] == 20
    assert payload["rwkv"]["url"] == rwkv.DEFAULT_URL
    assert len(payload["rwkv"]["template_sha1"]) == 40
    assert payload["results"][0]["score"] > 0
    assert payload["results"][0]["metrics"]["avg_similarity"] is not None
    assert payload["results"][0]["errors"] == []

    md = sorted((tmp_path / "logs").glob("rwkv_eval_*.md"))
    assert len(md) == 1
    text = md[0].read_text(encoding="utf-8")
    assert "推荐模板" in text and "不得再分发" in text

    # 不写 result/：只保留原有基线文件
    assert sorted(p.name for p in result_dir.iterdir()) == before == ["seg_01.json"]


def test_run_eval_ranks_chinese_template_first(tmp_path, monkeypatch):
    root, _ = _make_ws(tmp_path)
    _patch_ws(monkeypatch, root, tmp_path / "data" / "character.json")

    def fake_translate(text, cfg=None, template=None):
        if template == "plain":
            return {"text": "这是中文结果", "elapsed_ms": 3}
        return {"text": "abc", "elapsed_ms": 3}

    monkeypatch.setattr(rwkv_eval.rwkv, "translate", fake_translate)

    assert rwkv_eval.run_eval(_args(tmp_path, templates="plain||latin")) == ExitCode.OK
    payload = json.loads(next((tmp_path / "logs").glob("rwkv_eval_*.json"))
                         .read_text(encoding="utf-8"))
    assert payload["results"][0]["template"] == "plain"
    assert payload["results"][0]["score"] > payload["results"][1]["score"]


@pytest.mark.parametrize("exc,code", [
    (rwkv.RwkvUnavailable("down"), ExitCode.CONNECTION),
    (rwkv.RwkvModelOffline("no model"), ExitCode.PAUSED),
])
def test_run_eval_all_failed_maps_to_exit_code(tmp_path, monkeypatch, exc, code):
    root, _ = _make_ws(tmp_path)
    _patch_ws(monkeypatch, root, tmp_path / "data" / "character.json")

    def fake_translate(text, cfg=None, template=None):
        raise exc

    monkeypatch.setattr(rwkv_eval.rwkv, "translate", fake_translate)

    assert rwkv_eval.run_eval(_args(tmp_path)) == code
    payload = json.loads(next((tmp_path / "logs").glob("rwkv_eval_*.json"))
                         .read_text(encoding="utf-8"))
    assert payload["results"][0]["metrics"]["ok"] == 0
    assert payload["results"][0]["errors"][0]["error"] == type(exc).__name__


def test_run_eval_without_samples_returns_usage(tmp_path, monkeypatch, capsys):
    root = tmp_path / "data" / "story" / "character"
    (root / "1").mkdir(parents=True)
    char_file = tmp_path / "data" / "character.json"
    char_file.write_text(json.dumps([{"id": "1"}], ensure_ascii=False), encoding="utf-8")
    _patch_ws(monkeypatch, root, char_file)

    assert rwkv_eval.run_eval(_args(tmp_path)) == ExitCode.USAGE
    assert "没有采样到任何段落" in capsys.readouterr().out
