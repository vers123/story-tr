"""`story_tr.transport` 补充单测：错误分支、工具函数与边界行为（全部 mock，不联网）。"""
import json

import httpx
import pytest

from story_tr import transport
from story_tr.config import Settings
from story_tr.errors import (
    BadOutput,
    ConnectionFailed,
    RateLimited,
)
from story_tr.transport import baidu, bing, google, rwkv, tts
from story_tr.transport.base import BaseTranslator


def _settings(**overrides) -> Settings:
    kwargs = {
        "google_api_key": "",
        "bing_api_key": "",
        "bing_region": "",
        "baidu_appid": "",
        "baidu_appkey": "",
        "rwkv_api_url": "http://127.0.0.1:8000",
        "rwkv_timeout": 300.0,
        "rwkv_chunk_chars": 1200,
        "rwkv_retries": 0,
        "rwkv_max_tokens": 1024,
        "rwkv_temperature": 1.0,
        "rwkv_top_p": 0.3,
    }
    kwargs.update(overrides)
    return Settings(**kwargs)


class FakeResponse:
    def __init__(self, payload=None, status=200, text=None, headers=None, content=b""):
        self.status_code = status
        self._payload = payload
        self.text = text if text is not None else (
            json.dumps(payload, ensure_ascii=False) if payload is not None else "")
        self.headers = httpx.Headers(headers or {})
        self.content = content

    def json(self):
        if self._payload is None:
            raise ValueError("不是 JSON")
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=httpx.Request("GET", "http://fake"),
                response=self,
            )


class FakeClient:
    def __init__(self, get=None, post=None):
        self.headers = {}
        self.get_calls = []
        self.post_calls = []
        self.closed = False
        self._get_handler = get
        self._post_handler = post

    def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        if self._get_handler is None:
            return FakeResponse(status=404, text="")
        return self._get_handler(url, **kwargs)

    def post(self, url, **kwargs):
        self.post_calls.append((url, kwargs))
        if self._post_handler is None:
            return FakeResponse(status=404, text="")
        return self._post_handler(url, **kwargs)

    def close(self):
        self.closed = True


def _install_client(monkeypatch, client):
    monkeypatch.setattr(httpx, "Client", lambda *args, **kwargs: client)
    return client


# --------------------------------------------------------------------------- #
# base
# --------------------------------------------------------------------------- #
def test_build_translator_all_backends():
    assert transport.build_translator("bing", _settings()).name == "bing"
    assert transport.build_translator("baidu", _settings()).name == "baidu"


def test_base_close(monkeypatch):
    client = FakeClient()
    _install_client(monkeypatch, client)
    t = BaseTranslator(_settings())
    t.close()
    assert client.closed is True


def test_base_throttle_waits(monkeypatch):
    values = iter([100.0, 100.0, 100.05, 100.05])
    monkeypatch.setattr("story_tr.transport.base.time.monotonic", lambda: next(values))
    sleeps = []
    monkeypatch.setattr("story_tr.transport.base.time.sleep", sleeps.append)
    t = BaseTranslator(_settings())
    t.min_interval = 0.1
    t._throttle()
    t._throttle()
    assert len(sleeps) == 1
    assert sleeps[0] == pytest.approx(0.05)


def test_base_get_timeout(monkeypatch):
    def boom(url, **kw):
        raise httpx.ConnectTimeout("timeout")

    client = FakeClient(get=boom)
    _install_client(monkeypatch, client)
    t = BaseTranslator(_settings())
    with pytest.raises(ConnectionFailed):
        t._get("http://x")


def test_base_get_http_error(monkeypatch):
    def boom(url, **kw):
        raise httpx.ConnectError("conn")

    client = FakeClient(get=boom)
    _install_client(monkeypatch, client)
    t = BaseTranslator(_settings())
    with pytest.raises(ConnectionFailed):
        t._get("http://x")


def test_base_post_http_error(monkeypatch):
    def boom(url, **kw):
        raise httpx.ConnectError("conn")

    client = FakeClient(post=boom)
    _install_client(monkeypatch, client)
    t = BaseTranslator(_settings())
    with pytest.raises(ConnectionFailed):
        t._post("http://x")


def test_base_map_error_500_and_400(monkeypatch):
    client = FakeClient()
    _install_client(monkeypatch, client)
    t = BaseTranslator(_settings())
    with pytest.raises(ConnectionFailed):
        t._map_error(FakeResponse(status=500, text="boom"))
    with pytest.raises(BadOutput):
        t._map_error(FakeResponse(status=404, text="nope"))


def test_base_read_json_empty_and_invalid(monkeypatch):
    client = FakeClient()
    _install_client(monkeypatch, client)
    t = BaseTranslator(_settings())
    with pytest.raises(BadOutput):
        t._read_json(FakeResponse(status=200, text="   "))
    with pytest.raises(BadOutput):
        t._read_json(FakeResponse(status=200, text="not-json"))


# --------------------------------------------------------------------------- #
# google
# --------------------------------------------------------------------------- #
def test_google_dict_shapes(monkeypatch):
    client = FakeClient(get=lambda url, **kw: FakeResponse(payload=[["你好"]]))
    _install_client(monkeypatch, client)
    t = google.GoogleTranslator(_settings())
    assert t.translate("hello", "en", "zh-CN") == "你好"


def test_google_dict_empty_result(monkeypatch):
    client = FakeClient(get=lambda url, **kw: FakeResponse(payload=[""]))
    _install_client(monkeypatch, client)
    t = google.GoogleTranslator(_settings())
    with pytest.raises(BadOutput):
        t.translate("hello", "en", "zh-CN")


def test_google_dict_batch_ok(monkeypatch):
    client = FakeClient(get=lambda url, **kw: FakeResponse(payload=["你好", "世界"]))
    _install_client(monkeypatch, client)
    t = google.GoogleTranslator(_settings())
    assert t._dict_translate_batch(["a", "b"], "en", "zh-CN") == ["你好", "世界"]


def test_google_dict_batch_shape_mismatch(monkeypatch):
    client = FakeClient(get=lambda url, **kw: FakeResponse(payload=["你好"]))
    _install_client(monkeypatch, client)
    t = google.GoogleTranslator(_settings())
    with pytest.raises(BadOutput):
        t._dict_translate_batch(["a", "b"], "en", "zh-CN")


def test_google_api_bad_structure(monkeypatch):
    client = FakeClient(post=lambda url, **kw: FakeResponse(payload={}))
    _install_client(monkeypatch, client)
    t = google.GoogleTranslator(_settings(google_api_key="k"))
    with pytest.raises(BadOutput):
        t.translate("hello", "en", "zh-CN")


def test_google_api_empty_result(monkeypatch):
    client = FakeClient(post=lambda url, **kw: FakeResponse(
        payload={"data": {"translations": [{"translatedText": ""}]}}))
    _install_client(monkeypatch, client)
    t = google.GoogleTranslator(_settings(google_api_key="k"))
    with pytest.raises(BadOutput):
        t.translate("hello", "en", "zh-CN")


# --------------------------------------------------------------------------- #
# bing
# --------------------------------------------------------------------------- #
_BING_HTML = (
    'IG:"IG1"; data-iid="iid.1"; '
    'params_AbusePreventionHelper = [123, "token1", 456];'
)


def _bing_web(monkeypatch, get, post):
    _install_client(monkeypatch, FakeClient(get=get, post=post))


def test_bing_web_all_domains_fail(monkeypatch):
    _bing_web(monkeypatch,
              get=lambda url, **kw: FakeResponse(status=200, text=""),
              post=lambda url, **kw: FakeResponse())
    t = bing.BingTranslator(_settings())
    with pytest.raises(RateLimited):
        t.translate("hello", "en", "zh-CN")


def test_bing_web_429(monkeypatch):
    _bing_web(monkeypatch,
              get=lambda url, **kw: FakeResponse(status=200, text=_BING_HTML),
              post=lambda url, **kw: FakeResponse(status=429, text="limited"))
    t = bing.BingTranslator(_settings())
    with pytest.raises(RateLimited):
        t.translate("hello", "en", "zh-CN")


def test_bing_web_non_200(monkeypatch):
    _bing_web(monkeypatch,
              get=lambda url, **kw: FakeResponse(status=200, text=_BING_HTML),
              post=lambda url, **kw: FakeResponse(status=500, text="boom"))
    t = bing.BingTranslator(_settings())
    with pytest.raises(ConnectionFailed):
        t.translate("hello", "en", "zh-CN")


def test_bing_web_non_json_content_type(monkeypatch):
    _bing_web(monkeypatch,
              get=lambda url, **kw: FakeResponse(status=200, text=_BING_HTML),
              post=lambda url, **kw: FakeResponse(status=200, text="<html>"))
    t = bing.BingTranslator(_settings())
    with pytest.raises(RateLimited):
        t.translate("hello", "en", "zh-CN")


def test_bing_web_bad_structure(monkeypatch):
    _bing_web(monkeypatch,
              get=lambda url, **kw: FakeResponse(status=200, text=_BING_HTML),
              post=lambda url, **kw: FakeResponse(
                  payload=[], headers={"Content-Type": "application/json"}))
    t = bing.BingTranslator(_settings())
    with pytest.raises(RateLimited):
        t.translate("hello", "en", "zh-CN")


def test_bing_web_401_twice(monkeypatch):
    _bing_web(monkeypatch,
              get=lambda url, **kw: FakeResponse(status=200, text=_BING_HTML),
              post=lambda url, **kw: FakeResponse(status=401, text="expired"))
    t = bing.BingTranslator(_settings())
    with pytest.raises(ConnectionFailed):
        t.translate("hello", "en", "zh-CN")


def test_bing_azure_bad_structure(monkeypatch):
    _bing_web(monkeypatch, get=None, post=lambda url, **kw: FakeResponse(payload=[]))
    t = bing.BingTranslator(_settings(bing_api_key="k"))
    with pytest.raises(BadOutput):
        t.translate("hello", "en", "zh-CN")


def test_bing_azure_empty_result(monkeypatch):
    _bing_web(monkeypatch, get=None,
              post=lambda url, **kw: FakeResponse(payload=[{"translations": [{"text": ""}]}]))
    t = bing.BingTranslator(_settings(bing_api_key="k"))
    with pytest.raises(BadOutput):
        t.translate("hello", "en", "zh-CN")


# --------------------------------------------------------------------------- #
# baidu
# --------------------------------------------------------------------------- #
def test_baidu_available_with_keys(monkeypatch):
    client = FakeClient()
    _install_client(monkeypatch, client)
    t = baidu.BaiduTranslator(_settings(baidu_appid="a", baidu_appkey="k"))
    assert t.available() == (True, "")


def test_baidu_translate_without_keys(monkeypatch):
    client = FakeClient()
    _install_client(monkeypatch, client)
    t = baidu.BaiduTranslator(_settings())
    with pytest.raises(BadOutput):
        t.translate("hello", "en", "zh-CN")


def test_baidu_non_dict(monkeypatch):
    client = FakeClient(post=lambda url, **kw: FakeResponse(payload=[]))
    _install_client(monkeypatch, client)
    t = baidu.BaiduTranslator(_settings(baidu_appid="a", baidu_appkey="k"))
    with pytest.raises(BadOutput):
        t.translate("hello", "en", "zh-CN")


def test_baidu_other_error_code(monkeypatch):
    client = FakeClient(post=lambda url, **kw: FakeResponse(
        payload={"error_code": "58001", "error_msg": "invalid"}))
    _install_client(monkeypatch, client)
    t = baidu.BaiduTranslator(_settings(baidu_appid="a", baidu_appkey="k"))
    with pytest.raises(BadOutput):
        t.translate("hello", "en", "zh-CN")


def test_baidu_bad_structure(monkeypatch):
    client = FakeClient(post=lambda url, **kw: FakeResponse(payload={}))
    _install_client(monkeypatch, client)
    t = baidu.BaiduTranslator(_settings(baidu_appid="a", baidu_appkey="k"))
    with pytest.raises(BadOutput):
        t.translate("hello", "en", "zh-CN")


def test_baidu_empty_result(monkeypatch):
    client = FakeClient(post=lambda url, **kw: FakeResponse(
        payload={"trans_result": [{"dst": ""}]}))
    _install_client(monkeypatch, client)
    t = baidu.BaiduTranslator(_settings(baidu_appid="a", baidu_appkey="k"))
    with pytest.raises(BadOutput):
        t.translate("hello", "en", "zh-CN")


# --------------------------------------------------------------------------- #
# rwkv
# --------------------------------------------------------------------------- #
def test_rwkv_render_without_text_placeholder():
    out = rwkv.render("Translate: {src}", "原文")
    assert out == "Translate: zh-CN\n\n原文"


def test_rwkv_status_name_variants():
    assert rwkv.status_name("Working") == "Working"
    assert rwkv.status_name(True) == "True"
    assert rwkv.status_name(0) == "Offline"
    assert rwkv.status_name(2) == "Loading"
    assert rwkv.status_name(99) == "99"


def test_rwkv_translate_empty_text(monkeypatch):
    client = FakeClient()
    _install_client(monkeypatch, client)
    t = rwkv.RwkvTranslator(_settings())
    assert t.translate("   ", "en", "zh-CN") == ""


def test_rwkv_extract_text_errors():
    with pytest.raises(BadOutput):
        rwkv.RwkvTranslator._extract_text({})
    with pytest.raises(BadOutput):
        rwkv.RwkvTranslator._extract_text({"choices": [{"text": 123}]})


def test_rwkv_as_int():
    assert rwkv.RwkvTranslator._as_int("12") == 12
    assert rwkv.RwkvTranslator._as_int("abc") == 0
    assert rwkv.RwkvTranslator._as_int(None) == 0


def test_rwkv_validate_failures():
    with pytest.raises(BadOutput):
        rwkv.RwkvTranslator._validate("", "原文")
    with pytest.raises(BadOutput):
        rwkv.RwkvTranslator._validate("原文", "原文")
    with pytest.raises(BadOutput):
        rwkv.RwkvTranslator._validate("abc", "原文")


def test_rwkv_probe_unreachable(monkeypatch):
    def boom(url, **kw):
        raise httpx.ConnectError("conn refused")

    client = FakeClient(get=boom)
    _install_client(monkeypatch, client)
    t = rwkv.RwkvTranslator(_settings())
    info = t.probe()
    assert info["reachable"] is False
    assert info["error"]


# --------------------------------------------------------------------------- #
# tts
# --------------------------------------------------------------------------- #
def test_tts_with_proxy(monkeypatch):
    client = FakeClient(get=lambda url, **kw: FakeResponse(status=200, content=b"mp3"))
    _install_client(monkeypatch, client)
    assert tts.tts_bytes("你好", "zh-CN", proxy="http://proxy") == b"mp3"


def test_tts_http_error(monkeypatch):
    def boom(url, **kw):
        raise httpx.ConnectError("conn")

    client = FakeClient(get=boom)
    _install_client(monkeypatch, client)
    with pytest.raises(ConnectionFailed):
        tts.tts_bytes("你好", "zh-CN")
