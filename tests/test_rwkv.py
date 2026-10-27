# -*- coding: utf-8 -*-
"""`story_tr.rwkv` 本地后端：用假 HTTP 覆盖配置、渲染、分块、清洗、重试与探测。"""
import hashlib
import json
import os
from types import SimpleNamespace

import pytest

from story_tr import rwkv

_ENV_NAMES = ("RWKV_API_URL", "RWKV_PROMPT_TEMPLATE", "RWKV_MAX_TOKENS", "RWKV_TEMPERATURE",
              "RWKV_TOP_P", "RWKV_TIMEOUT", "RWKV_CHUNK_CHARS", "RWKV_RETRIES")


class FakeResponse:
    def __init__(self, payload=None, status=200, text=None):
        self._payload = payload
        self.status_code = status
        self.text = text if text is not None else json.dumps(payload, ensure_ascii=False)

    def json(self):
        return self._payload


# --------------------------------------------------------------------------- #
# render / split_chunks
# --------------------------------------------------------------------------- #
def test_render_placeholders():
    assert rwkv.render("User: {text}\n\nBot:", "你好") == "User: 你好\n\nBot:"
    assert rwkv.render("{src}->{tgt}: {text}", "hi", "en", "zh-CN") == "en->zh-CN: hi"


def test_render_appends_text_when_placeholder_missing():
    out = rwkv.render("把下面的文本翻译成中文：", "你好")
    assert out == "把下面的文本翻译成中文：\n\n你好"


def test_split_chunks_short_and_empty():
    assert rwkv.split_chunks("短文本", 100) == ["短文本"]
    assert rwkv.split_chunks("", 100) == []


def test_split_chunks_on_sentence_end():
    text = "第一句话。" + "很长的第二句。" + "结尾。"
    chunks = rwkv.split_chunks(text, 10)
    assert all(len(c) <= 10 for c in chunks)
    assert "".join(chunks) == text
    assert chunks[0] == "第一句话。"


def test_split_chunks_hard_cut():
    text = "啊" * 25
    chunks = rwkv.split_chunks(text, 10)
    assert [len(c) for c in chunks] == [10, 10, 5]
    assert "".join(chunks) == text


# --------------------------------------------------------------------------- #
# clean_output
# --------------------------------------------------------------------------- #
def test_clean_output_strips_role_prefix():
    assert rwkv.clean_output("Bot: 这是结果", "", "原文") == "这是结果"
    assert rwkv.clean_output("Assistant：这是结果", "", "原文") == "这是结果"


def test_clean_output_strips_prompt_echo():
    prompt = "User: 原文\n\nBot:"
    assert rwkv.clean_output(prompt + "这是结果", prompt, "原文") == "这是结果"


def test_clean_output_strips_wrapping_quotes():
    assert rwkv.clean_output("“这是结果”", "", "原文") == "这是结果"
    assert rwkv.clean_output('"这是结果"', "", "原文") == "这是结果"
    assert rwkv.clean_output("「这是结果」", "", "原文") == "这是结果"


def test_clean_output_keeps_paragraph_blank_line():
    assert rwkv.clean_output("第一段  \n\n第二段\t", "", "原文") == "第一段\n\n第二段"


# --------------------------------------------------------------------------- #
# translate
# --------------------------------------------------------------------------- #
def test_translate_success(monkeypatch):
    calls = []

    def fake_post(url, payload, timeout):
        calls.append((url, payload, timeout))
        return {"choices": [{"text": "这是结果"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5}}

    monkeypatch.setattr(rwkv, "_post_json", fake_post)
    out = rwkv.translate("Hello", rwkv.RwkvConfig())

    assert out["text"] == "这是结果"
    assert out["chunks"] == 1
    assert out["usage"] == {"prompt_tokens": 10, "completion_tokens": 5}
    assert out["prompt_tokens"] == 10
    assert out["completion_tokens"] == 5
    assert isinstance(out["elapsed_ms"], int)

    url, payload, timeout = calls[0]
    assert url == "http://127.0.0.1:8000/v1/completions"
    assert payload["prompt"] == "User: Hello\n\nBot:"
    assert payload["model"] == "rwkv"
    assert payload["stream"] is False
    assert payload["stop"] == rwkv.DEFAULT_STOP
    assert timeout == 300.0


def test_translate_empty_text(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("空文本不应发请求")

    monkeypatch.setattr(rwkv, "_post_json", boom)
    assert rwkv.translate("   ") == {"text": "", "chunks": 0, "usage": {},
                                     "prompt_tokens": 0, "completion_tokens": 0,
                                     "elapsed_ms": 0}


def test_translate_split_chunks(monkeypatch):
    prompts = []

    def fake_post(url, payload, timeout):
        prompts.append(payload["prompt"])
        text = "甲" if "第一" in payload["prompt"] else "乙"
        return {"choices": [{"text": text}]}

    monkeypatch.setattr(rwkv, "_post_json", fake_post)
    out = rwkv.translate("第一句。第二句。", rwkv.RwkvConfig(chunk_chars=5))

    assert out["chunks"] == 2
    assert out["text"] == "甲乙"
    assert out["usage"] == {}
    assert len(prompts) == 2


def test_translate_empty_output_retries_then_fails(monkeypatch):
    calls = []

    def fake_post(url, payload, timeout):
        calls.append(payload)
        return {"choices": [{"text": "   "}]}

    monkeypatch.setattr(rwkv, "_post_json", fake_post)
    with pytest.raises(rwkv.RwkvBadOutput):
        rwkv.translate("原文", rwkv.RwkvConfig(retries=2))
    assert len(calls) == 3


def test_translate_same_as_source_is_bad_output(monkeypatch):
    monkeypatch.setattr(rwkv, "_post_json",
                        lambda *a, **k: {"choices": [{"text": "原文"}]})
    with pytest.raises(rwkv.RwkvBadOutput):
        rwkv.translate("原文", rwkv.RwkvConfig(retries=0))


def test_translate_connection_error_not_retried(monkeypatch):
    calls = []

    def fake_post(*a, **k):
        calls.append(1)
        raise rwkv.requests.exceptions.ConnectionError("refused")

    monkeypatch.setattr(rwkv.requests, "post", fake_post)
    with pytest.raises(rwkv.RwkvUnavailable):
        rwkv.translate("原文", rwkv.RwkvConfig(retries=2))
    assert len(calls) == 1


def test_read_json_400_model_not_loaded():
    resp = FakeResponse({"detail": "model not loaded"}, status=400)
    with pytest.raises(rwkv.RwkvModelOffline):
        rwkv._read_json(resp, "http://x/v1/completions")


def test_read_json_400_other_error():
    resp = FakeResponse({"detail": "bad prompt"}, status=400)
    with pytest.raises(rwkv.RwkvError) as excinfo:
        rwkv._read_json(resp, "http://x/v1/completions")
    assert not isinstance(excinfo.value, rwkv.RwkvModelOffline)


def test_read_json_500_failed_to_load():
    resp = FakeResponse(status=500, text="failed to load model")
    with pytest.raises(rwkv.RwkvModelOffline):
        rwkv._read_json(resp, "http://x/v1/completions")


def test_translate_429_is_unavailable(monkeypatch):
    monkeypatch.setattr(rwkv.requests, "post",
                        lambda *a, **k: FakeResponse(status=429, text="too many requests"))
    with pytest.raises(rwkv.RwkvUnavailable):
        rwkv.translate("原文")


def test_post_json_uses_connect_tuple_timeout(monkeypatch):
    seen = {}

    def fake_post(url, json=None, timeout=None):
        seen["timeout"] = timeout
        return FakeResponse(payload={"choices": [{"text": "这是结果"}],
                                     "usage": {"prompt_tokens": 1, "completion_tokens": 2}})

    monkeypatch.setattr(rwkv.requests, "post", fake_post)
    data = rwkv._post_json("http://x", {}, 300)

    assert data["choices"][0]["text"] == "这是结果"
    assert isinstance(seen["timeout"], tuple)
    assert seen["timeout"][0] <= 5
    assert seen["timeout"] == (5.0, 300.0)


# --------------------------------------------------------------------------- #
# 配置
# --------------------------------------------------------------------------- #
def test_from_env_defaults(monkeypatch):
    for name in _ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    assert rwkv.from_env() == rwkv.RwkvConfig()


def test_from_env_overrides_and_unescape(monkeypatch):
    monkeypatch.setenv("RWKV_API_URL", "http://127.0.0.1:9000/")
    monkeypatch.setenv("RWKV_PROMPT_TEMPLATE", "User: {text}\\n\\nBot:")
    monkeypatch.setenv("RWKV_MAX_TOKENS", "256")
    monkeypatch.setenv("RWKV_TEMPERATURE", "0.8")
    monkeypatch.setenv("RWKV_TOP_P", "0.5")
    monkeypatch.setenv("RWKV_TIMEOUT", "10")
    monkeypatch.setenv("RWKV_CHUNK_CHARS", "800")
    monkeypatch.setenv("RWKV_RETRIES", "0")

    cfg = rwkv.from_env()
    assert cfg.url == "http://127.0.0.1:9000"
    assert cfg.template == "User: {text}\n\nBot:"
    assert cfg.max_tokens == 256
    assert cfg.temperature == 0.8
    assert cfg.top_p == 0.5
    assert cfg.timeout == 10.0
    assert cfg.chunk_chars == 800
    assert cfg.retries == 0


def test_from_env_invalid_numbers_use_defaults(monkeypatch):
    for name in _ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("RWKV_MAX_TOKENS", "abc")
    monkeypatch.setenv("RWKV_TEMPERATURE", "")
    cfg = rwkv.from_env()
    assert cfg.max_tokens == 1024
    assert cfg.temperature == 1.0


def test_from_args_overrides_env(monkeypatch):
    for name in _ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("RWKV_API_URL", "http://127.0.0.1:9000")
    monkeypatch.setenv("RWKV_MAX_TOKENS", "512")

    args = SimpleNamespace(rwkv_url="http://127.0.0.1:9100", rwkv_template="User: {text}\\nBot:",
                           rwkv_max_tokens=None, rwkv_retries=1)
    cfg = rwkv.from_args(args)
    assert cfg.url == "http://127.0.0.1:9100"
    assert cfg.template == "User: {text}\nBot:"
    assert cfg.max_tokens == 512
    assert cfg.retries == 1


def test_from_args_missing_attributes_keep_baseline(monkeypatch):
    for name in _ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    assert rwkv.from_args(SimpleNamespace()) == rwkv.RwkvConfig()


def test_load_env_reads_home_dotenv(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text(
        "RWKV_API_URL=http://127.0.0.1:9999\nRWKV_TOP_P=0.5\n", encoding="utf-8")
    monkeypatch.delenv("RWKV_API_URL", raising=False)
    monkeypatch.delenv("RWKV_TOP_P", raising=False)

    rwkv.load_env(str(tmp_path))
    try:
        cfg = rwkv.from_env()
        assert cfg.url == "http://127.0.0.1:9999"
        assert cfg.top_p == 0.5
    finally:
        # monkeypatch.delenv 对「原本不存在」的键不记录恢复值，这里显式清理
        os.environ.pop("RWKV_API_URL", None)
        os.environ.pop("RWKV_TOP_P", None)


# --------------------------------------------------------------------------- #
# probe / signature
# --------------------------------------------------------------------------- #
def test_probe_success(monkeypatch):
    def fake_get(url, timeout):
        if url.endswith("/status"):
            return {"status": 3}
        return {"data": [{"id": "translate-20times-3B"}]}

    monkeypatch.setattr(rwkv, "_get_json", fake_get)
    out = rwkv.probe(rwkv.RwkvConfig())
    assert out == {"reachable": True, "status": "Working",
                   "model": "translate-20times-3B", "error": None}


def test_probe_status_string(monkeypatch):
    monkeypatch.setattr(rwkv, "_get_json", lambda url, timeout: {"status": "Loading"})
    out = rwkv.probe()
    assert out["reachable"] is True
    assert out["status"] == "Loading"
    assert out["model"] is None


def test_probe_unreachable(monkeypatch):
    def fake_get(url, timeout):
        raise rwkv.RwkvUnavailable("连接被拒绝")

    monkeypatch.setattr(rwkv, "_get_json", fake_get)
    out = rwkv.probe()
    assert out["reachable"] is False
    assert out["status"] is None
    assert out["model"] is None
    assert "连接被拒绝" in out["error"]


def test_status_name():
    assert rwkv.status_name(0) == "Offline"
    assert rwkv.status_name(2) == "Loading"
    assert rwkv.status_name(3) == "Working"
    assert rwkv.status_name("Working") == "Working"
    assert rwkv.status_name(7) == "7"


def test_signature_has_template_sha1_only():
    cfg = rwkv.RwkvConfig(template="User: {text}\n\nBot:")
    sig = rwkv.signature(cfg)
    assert sig["model"] == "rwkv"
    assert sig["url"] == cfg.url
    assert sig["template_sha1"] == hashlib.sha1(cfg.template.encode("utf-8")).hexdigest()
    assert "template" not in sig
    assert cfg.template not in sig.values()
