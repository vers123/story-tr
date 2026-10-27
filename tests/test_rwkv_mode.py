# -*- coding: utf-8 -*-
"""`--mode rwkv20` 接入 chain / translate：参数解析、后端规则、签名与本地元数据。"""
import logging
from types import SimpleNamespace

import pytest

from story_tr import chain as tc
from story_tr import cli, rwkv, stories


def _cfg(template=None):
    cfg = rwkv.RwkvConfig()
    if template is not None:
        cfg.template = template
    return {"rwkv": cfg, "balance": False}


def _args(**kw):
    base = dict(mode="rwkv20", chain="asia", steps=20, languages="", provider=None,
                all=False, stories="", full_only=False, segments_only=False,
                tts=None, no_tts=False)
    base.update(kw)
    return SimpleNamespace(**base)


def test_resolve_languages_and_build_tasks_for_rwkv20():
    assert tc.resolve_languages(_args()) == (["rwkv20"], "rwkv20")
    tasks = tc.build_tasks("zh-CN", ["rwkv20"])
    assert len(tasks) == 1
    task = tasks[0]
    assert task["kind"] == "rwkv20"
    assert task["lang"] == "zh-CN"
    assert task["src"] == task["tgt"] == "zh-CN"


def test_resolve_providers_rules():
    assert tc.resolve_providers(_args()) == (["rwkv"], "")
    assert tc.resolve_providers(_args(provider="rwkv")) == (["rwkv"], "")

    providers, err = tc.resolve_providers(_args(provider="google"))
    assert providers == []
    assert "rwkv20" in err

    assert tc.resolve_providers(_args(mode="fixed", provider="google")) == (["google"], "")
    assert tc.resolve_providers(_args(mode="fixed")) == (["google", "bing", "baidu"], "")

    providers, err = tc.resolve_providers(_args(mode="fixed", provider="rwkv"))
    assert providers == []
    assert "rwkv20" in err


def test_tts_enabled():
    assert tc.tts_enabled(_args()) is False
    assert tc.tts_enabled(_args(tts=True)) is True
    assert tc.tts_enabled(_args(mode="fixed")) is True
    assert tc.tts_enabled(_args(mode="fixed", no_tts=True)) is False


def test_cli_parses_rwkv20_and_tts_conflict():
    args = cli.build_parser().parse_args(["chain", "hi", "--mode", "rwkv20"])
    assert args.mode == "rwkv20"
    assert args.rwkv_url == ""
    assert args.tts is None
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["chain", "hi", "--tts", "--no-tts"])


def test_chain_signature_includes_rwkv_and_template():
    args = _args()
    sig = tc.signature(args, "hi", "en", ["rwkv20"], ["rwkv"],
                       _cfg("User: {text}\n\nBot:"))
    assert sig["rwkv"]["model"] == "rwkv"
    assert sig["rwkv"]["template_sha1"]

    other = tc.signature(args, "hi", "en", ["rwkv20"], ["rwkv"], _cfg("另一套 {text}"))
    assert other["rwkv"]["template_sha1"] != sig["rwkv"]["template_sha1"]

    cloud = tc.signature(args, "hi", "en", ["ja"], ["google"], _cfg())
    assert "rwkv" not in cloud


def test_stories_signature_and_title_cache_key_follow_template():
    args = _args()
    sig = stories.signature(args, ["1"], _cfg("模板 A {text}"))
    assert sig["rwkv"]["template_sha1"]

    key_a = stories.title_cache_key("标题", args, ["rwkv"], _cfg("模板 A {text}"))
    key_b = stories.title_cache_key("标题", args, ["rwkv"], _cfg("模板 B {text}"))
    assert key_a != key_b

    google_a = stories.title_cache_key("标题", args, ["google"], _cfg("模板 A {text}"))
    google_b = stories.title_cache_key("标题", args, ["google"], _cfg("模板 B {text}"))
    assert google_a == google_b


@pytest.mark.parametrize("error, expected", [
    (lambda: rwkv.RwkvUnavailable("down"), tc.ConnectionIssue),
    (lambda: rwkv.RwkvModelOffline("offline"), tc.ModelNotLoaded),
    (lambda: rwkv.RwkvBadOutput("bad"), tc.BusinessError),
    (lambda: rwkv.RwkvError("other"), tc.BusinessError),
])
def test_translate_once_raw_maps_local_errors(monkeypatch, error, expected):
    def boom(*_a, **_k):
        raise error()

    monkeypatch.setattr(tc.local_rwkv, "translate", boom)
    with pytest.raises(expected):
        tc._translate_once_raw("rwkv", "原文", "zh-CN", "zh-CN", _cfg())


def test_translate_step_success_collects_local_meta(monkeypatch):
    monkeypatch.setattr(tc.local_rwkv, "translate", lambda text, cfg: {
        "text": "结果", "chunks": 1, "usage": {},
        "prompt_tokens": 3, "completion_tokens": 2})

    provider, out = tc.translate_step("原文", "zh-CN", "zh-CN", ["rwkv"], _cfg(),
                                      logging.getLogger("test"))
    assert (provider, out) == ("rwkv", "结果")

    meta = tc.local_meta()
    assert meta["chunks"] == 1
    assert meta["prompt_tokens"] == 3
    assert meta["completion_tokens"] == 2


def test_translate_many_rwkv_falls_back_to_per_text(monkeypatch):
    calls = []

    def fake_once(provider, text, src, tgt, cfg):
        calls.append(text)
        return "译" + text

    monkeypatch.setattr(tc, "translate_once", fake_once)
    out = tc.translate_many("rwkv", ["a", "b"], "zh-CN", "zh-CN", _cfg())
    assert out == ["译a", "译b"]
    assert calls == ["a", "b"]


def test_run_chain_many_rwkv20_has_single_step(monkeypatch):
    monkeypatch.setattr(tc.local_rwkv, "translate", lambda text, cfg: {
        "text": "译文", "chunks": 1, "usage": {},
        "prompt_tokens": 5, "completion_tokens": 4})

    results = stories.run_chain_many(["第一段"], ["rwkv20"], ["rwkv"], _cfg(),
                                     logging.getLogger("test"), [{"story": "s"}],
                                     source_lang="zh-CN")
    assert len(results) == 1
    res = results[0]
    assert len(res["steps"]) == 1
    assert res["steps"][0]["kind"] == "rwkv20"
    assert res["rwkv"]["chunks"] >= 1
    assert res["rwkv"]["prompt_tokens"] >= 5


def test_run_chain_many_attributes_meta_per_text(monkeypatch):
    results_iter = iter([
        {"text": "甲译", "chunks": 1, "usage": {},
         "prompt_tokens": 3, "completion_tokens": 1},
        {"text": "乙译", "chunks": 2, "usage": {},
         "prompt_tokens": 4, "completion_tokens": 2},
    ])
    monkeypatch.setattr(tc.local_rwkv, "translate", lambda text, cfg: next(results_iter))

    results = stories.run_chain_many(["甲", "乙"], ["rwkv20"], ["rwkv"], _cfg(),
                                     logging.getLogger("test"), [{}, {}],
                                     source_lang="zh-CN")

    assert results[0]["steps"][0]["rwkv"]["chunks"] == 1
    assert results[1]["steps"][0]["rwkv"]["chunks"] == 2
    assert results[0]["rwkv"]["chunks"] == 1
    assert results[1]["rwkv"]["chunks"] == 2
    assert results[0]["rwkv"]["prompt_tokens"] == 3
    assert results[1]["rwkv"]["prompt_tokens"] == 4


def test_translate_step_connection_issue_propagates(monkeypatch):
    def boom(*_a, **_k):
        raise tc.ConnectionIssue("refused")

    monkeypatch.setattr(tc, "translate_once", boom)
    monkeypatch.setattr(tc, "CONNECTION_RETRY_BASE", 0)  # 测试中不等待退避
    with pytest.raises(tc.ConnectionIssue):
        tc.translate_step("原文", "zh-CN", "zh-CN", ["rwkv"], _cfg(),
                          logging.getLogger("test"))


# --------------------------------------------------------------------------- #
# Task 9：本地模式致命错误快速失败
# --------------------------------------------------------------------------- #
def test_rwkv_model_offline_is_model_not_loaded(monkeypatch):
    def boom(*_a, **_k):
        raise rwkv.RwkvModelOffline("模型未加载")

    monkeypatch.setattr(tc.local_rwkv, "translate", boom)
    with pytest.raises(tc.ModelNotLoaded) as excinfo:
        tc._translate_once_raw("rwkv", "甲", "zh-CN", "zh-CN",
                               {"rwkv": tc.local_rwkv.from_env()})
    assert isinstance(excinfo.value, tc.Paused)


def test_step_loop_local_connection_no_retry(monkeypatch):
    calls = []

    def boom(*_a, **_k):
        calls.append(1)
        raise tc.ConnectionIssue("refused")

    monkeypatch.setattr(tc, "translate_once", boom)
    with pytest.raises(tc.ConnectionIssue):
        tc.translate_step("甲", "zh-CN", "zh-CN", ["rwkv"], _cfg(),
                          logging.getLogger("test"))
    assert len(calls) == 1  # 不做云端式连接退避


def test_step_loop_local_business_single_attempt(monkeypatch):
    calls = []

    def boom(*_a, **_k):
        calls.append(1)
        raise tc.BusinessError("空结果")

    monkeypatch.setattr(tc, "translate_once", boom)
    with pytest.raises(tc.Paused) as excinfo:
        tc.translate_step("甲", "zh-CN", "zh-CN", ["rwkv"], _cfg(),
                          logging.getLogger("test"))
    assert not isinstance(excinfo.value, tc.ModelNotLoaded)
    assert len(calls) == 1  # 不再叠加 4 次业务重试


def test_is_fatal_local_error():
    local_args = _args()  # mode=rwkv20
    assert stories.is_fatal_local_error(tc.ConnectionIssue("down"), local_args, ["rwkv"])
    assert stories.is_fatal_local_error(tc.ModelNotLoaded("offline"), local_args, ["rwkv"])
    assert not stories.is_fatal_local_error(tc.Paused("业务"), local_args, ["rwkv"])
    assert stories.is_fatal_local_error(tc.ConnectionIssue("down"), local_args, ["google"])

    cloud_args = _args(mode="fixed")
    assert not stories.is_fatal_local_error(tc.ConnectionIssue("down"), cloud_args, ["google"])
    assert not stories.is_fatal_local_error(tc.ModelNotLoaded("offline"), cloud_args, ["google"])


def test_translate_step_many_model_offline_propagates(monkeypatch):
    def boom(*_a, **_k):
        raise tc.ModelNotLoaded("offline")

    monkeypatch.setattr(tc, "translate_once", boom)
    with pytest.raises(tc.ModelNotLoaded):
        tc.translate_step_many(["甲", "乙"], "zh-CN", "zh-CN", ["rwkv"], _cfg(),
                               logging.getLogger("test"))
