# -*- coding: utf-8 -*-
"""批量翻译：分块打包、批量/逐条派发、多条同步走链、输出内容开关与签名。"""
import json
import logging
import types

import pytest

from story_tr import chain as tc
from story_tr import stories

LOG = logging.getLogger("test")


def test_pack_chunks_by_total_length():
    items = [(0, "a" * 10), (0, "b" * 10), (1, "c" * 10)]
    groups = tc._pack_chunks(items, limit=25)
    assert [[c for _, c in g] for g in groups] == [["a" * 10, "b" * 10], ["c" * 10]]


def test_pack_chunks_single_oversized():
    items = [(0, "a" * 30), (1, "b")]
    assert [[c for _, c in g] for g in tc._pack_chunks(items, limit=10)] == [["a" * 30], ["b"]]


def test_translate_many_falls_back_to_single(monkeypatch):
    calls = []
    monkeypatch.setattr(tc, "translate_once",
                        lambda p, t, s, g, cfg: calls.append(t) or t.upper())
    assert tc.translate_many("bing", ["a", "b"], "en", "zh", {}) == ["A", "B"]
    assert calls == ["a", "b"]


def test_translate_many_uses_batch_for_google(monkeypatch):
    seen = {}
    monkeypatch.setattr(tc, "_throttle", lambda p, cfg: None)

    def fake_batch(texts, src, tgt, proxies):
        seen["texts"] = list(texts)
        return [t.upper() for t in texts]

    monkeypatch.setattr(tc, "_google_dict_translate_batch", fake_batch)
    assert tc.translate_many("google", ["aa", "bb", "cc"], "en", "zh", {}) == ["AA", "BB", "CC"]
    assert seen["texts"] == ["aa", "bb", "cc"]


def test_translate_many_splits_long_text_and_reassembles(monkeypatch):
    monkeypatch.setattr(tc, "_throttle", lambda p, cfg: None)
    monkeypatch.setattr(tc, "_google_dict_translate_batch",
                        lambda texts, s, g, p: ["<" + t + ">" for t in texts])
    monkeypatch.setattr(tc, "translate_once", lambda p, t, s, g, cfg: "[" + t + "]")
    long_text = "字" * (tc.MAX_CHARS_PER_REQUEST + 10)
    out = tc.translate_many("google", ["short", long_text], "zh", "en", {})
    # 短文本单独成组（与超长块合计会超限）；长文本切块后各自成组、再拼接回一条
    assert out[0] == "[short]"
    assert out[1].startswith("[") and out[1].endswith("]")
    assert len(out[1]) > tc.MAX_CHARS_PER_REQUEST


def test_translate_many_batch_shape_error_falls_back(monkeypatch):
    monkeypatch.setattr(tc, "_throttle", lambda p, cfg: None)
    monkeypatch.setattr(tc, "translate_once", lambda p, t, s, g, cfg: t.upper())

    def bad_batch(*a, **k):
        raise tc.BusinessError("形状不符")

    monkeypatch.setattr(tc, "_google_dict_translate_batch", bad_batch)
    assert tc.translate_many("google", ["a", "b"], "en", "zh", {}) == ["A", "B"]


def test_translate_many_batch_connection_error_is_classified(monkeypatch):
    """批量通道的连接类异常应归一化为 ConnectionIssue（交上层切后端），而非裸抛崩溃。"""
    monkeypatch.setattr(tc, "_throttle", lambda p, cfg: None)

    def timeout(*a, **k):
        raise tc.requests.exceptions.ConnectTimeout("连接客户端超时")

    monkeypatch.setattr(tc, "_google_dict_translate_batch", timeout)
    with pytest.raises(tc.ConnectionIssue):
        tc.translate_many("google", ["a", "b"], "en", "zh", {})


def test_translate_many_batch_rate_limited_propagates(monkeypatch):
    """限流（429）应由 _step_loop 退避重试同一后端，故 RateLimited 必须原样抛出。"""
    monkeypatch.setattr(tc, "_throttle", lambda p, cfg: None)

    def limited(*a, **k):
        raise tc.RateLimited("google(dict) HTTP 429 被限流")

    monkeypatch.setattr(tc, "_google_dict_translate_batch", limited)
    with pytest.raises(tc.RateLimited):
        tc.translate_many("google", ["a", "b"], "en", "zh", {})


def test_run_chain_many_all_texts_advance_together(monkeypatch):
    seen, pairs = [], []

    def fake_step_many(texts, src, tgt, providers, cfg, log):
        seen.append(list(texts))
        pairs.append((src, tgt))
        return "google", ["%s|%s" % (t, tgt) for t in texts]

    monkeypatch.setattr(tc, "translate_step_many", fake_step_many)
    monkeypatch.setattr(tc, "detect_language", lambda t: "zh-CN")
    results = stories.run_chain_many(["a", "b"], ["ja", "ko"], ["google"], {}, LOG,
                                     [{"story": "s1"}, {"story": "s2"}])
    assert len(seen) == 4  # 转英 + 2 步 + 回中文
    assert all(len(s) == 2 for s in seen)  # 每步两条一起推进
    assert pairs[0][1] == "en" and pairs[-1][1] == tc.TARGET_ZH  # 先转英、最后译回中文
    assert [r["source"] for r in results] == ["a", "b"]
    assert [r["story"] for r in results] == ["s1", "s2"]
    assert [len(r["steps"]) for r in results] == [4, 4]


def test_run_chain_many_splits_by_detected_language(monkeypatch):
    groups = []

    def fake_step_many(texts, src, tgt, providers, cfg, log):
        groups.append(list(texts))
        return "google", list(texts)

    monkeypatch.setattr(tc, "translate_step_many", fake_step_many)
    monkeypatch.setattr(tc, "detect_language", lambda t: "en" if t.startswith("e") else "zh-CN")
    res = stories.run_chain_many(["en1", "zh1", "en2"], ["ja"], ["google"], {}, LOG)
    assert len(res) == 3
    assert all(s == ["en1", "en2"] or s == ["zh1"] for s in groups)


def test_build_tasks_parts_filter(tmp_path):
    d = tmp_path / "vision"
    d.mkdir()
    (d / "zh.md").write_text(
        '---\nid: "1"\nstory: vision\nlang: zh\ntitle: "神之眼"\n---\n\n第一段\n\n第二段\n',
        encoding="utf-8")

    def keys(parts):
        return [t["key"] for t in
                stories.build_tasks(str(tmp_path), "1", ["vision"], LOG, parts)]

    assert keys("both") == ["title", "full", "seg_01", "seg_02"]
    assert keys("full") == ["title", "full"]  # 标题始终翻译，不受开关影响
    assert keys("segments") == ["title", "seg_01", "seg_02"]


def test_read_title_from_frontmatter(tmp_path):
    md = tmp_path / "zh.md"
    md.write_text('---\nid: "1"\nstory: vision\nlang: zh\ntitle: "角色详细"\n---\n\n正文\n',
                  encoding="utf-8")
    assert stories.read_title(str(md)) == "角色详细"
    plain = tmp_path / "plain.md"
    plain.write_text("没有 frontmatter 的正文\n", encoding="utf-8")
    assert stories.read_title(str(plain)) is None


def test_write_summary_includes_title(tmp_path, monkeypatch):
    monkeypatch.setattr(stories, "story_dir", lambda cid: str(tmp_path))
    d = tmp_path / "vision"
    d.mkdir()
    (d / "title.json").write_text(
        json.dumps({"source": "角色详细", "languages": ["ja"], "final": "X"},
                   ensure_ascii=False), encoding="utf-8")
    stories.write_summary("1", "vision", "google", "fixed/asia")
    out = json.loads((d / "segments.json").read_text(encoding="utf-8"))
    assert out["title"]["source"] == "角色详细"
    assert out["title"]["languages"] == ["ja"]
    assert out["title"]["json"] == "title.json"


def _fake_args(**kw):
    base = {"stories": "", "all": True, "mode": "fixed", "chain": "asia", "steps": 20,
            "languages": "", "provider": None, "full_only": False, "segments_only": False}
    base.update(kw)
    return types.SimpleNamespace(**base)


def test_signature_includes_parts_only_when_non_default():
    assert "parts" not in stories.signature(_fake_args(), ["1"])
    assert stories.signature(_fake_args(full_only=True), ["1"])["parts"] == "full"
    assert stories.signature(_fake_args(segments_only=True), ["1"])["parts"] == "segments"


def test_parts_label():
    assert stories.parts_label(_fake_args()) == "both"
    assert stories.parts_label(_fake_args(full_only=True)) == "full"
    assert stories.parts_label(_fake_args(segments_only=True)) == "segments"
