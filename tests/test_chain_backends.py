# -*- coding: utf-8 -*-
"""`story_tr.chain` 后端适配层：用假响应覆盖 Google 免费/官方端点与回退。"""
import pytest

from story_tr import chain as tc


class FakeResponse:
    def __init__(self, payload, status=200, headers=None):
        self._payload = payload
        self.status_code = status
        self.headers = headers or {"Content-Type": "application/json"}
        self.text = str(payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise AssertionError("status %d" % self.status_code)


def test_google_dict_single(monkeypatch):
    monkeypatch.setattr(tc.requests, "get",
                        lambda *a, **k: FakeResponse([["你好"]]))
    assert tc._google_dict_translate("hello", "en", "zh-CN", None) == "你好"


def test_google_dict_batch_shape(monkeypatch):
    monkeypatch.setattr(tc.requests, "get",
                        lambda *a, **k: FakeResponse(["A", "B"]))
    assert tc._google_dict_translate_batch(["a", "b"], "en", "zh-CN", None) == ["A", "B"]


def test_google_dict_batch_bad_shape(monkeypatch):
    monkeypatch.setattr(tc.requests, "get",
                        lambda *a, **k: FakeResponse([["A"]]))
    with pytest.raises(tc.BusinessError):
        tc._google_dict_translate_batch(["a", "b"], "en", "zh-CN", None)


def test_google_api_translate(monkeypatch):
    payload = {"data": {"translations": [{"translatedText": "你好 &amp; 世界"}]}}
    monkeypatch.setattr(tc.requests, "post",
                        lambda *a, **k: FakeResponse(payload))
    out = tc._google_api_translate("hello", "en", "zh-CN", {"google_api_key": "k"})
    assert out == "你好 & 世界"


def test_google_free_falls_back_to_deep_translator(monkeypatch):
    monkeypatch.setattr(tc, "_throttle", lambda p, cfg: None)

    def boom(*a, **k):
        raise tc.ConnectionIssue("dict down")

    monkeypatch.setattr(tc, "_google_dict_translate", boom)

    class FakeGoogle:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def translate(self, text):
            return "fallback:" + text

    import deep_translator
    monkeypatch.setattr(deep_translator, "GoogleTranslator", FakeGoogle)
    assert tc._translate_once_raw("google", "hello", "en", "zh-CN", {}) == "fallback:hello"
