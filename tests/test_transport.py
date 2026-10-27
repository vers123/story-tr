"""`story_tr.transport` 传输层单测：全部 mock HTTP，不联网。"""
import hashlib
import json

import httpx
import pytest

from story_tr import transport
from story_tr.config import Settings
from story_tr.errors import (
    BadOutput,
    ConnectionFailed,
    ModelNotLoaded,
    RateLimited,
    UnsupportedLanguage,
)
from story_tr.transport import baidu, bing, google, rwkv, tts
from story_tr.transport.base import to_provider_lang


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
        "rwkv_prompt_template": "User: {text}\\n\\nBot:",
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
        pass


def _install_client(monkeypatch, client):
    monkeypatch.setattr(httpx, "Client", lambda *args, **kwargs: client)
    return client


# --------------------------------------------------------------------------- #
# google
# --------------------------------------------------------------------------- #
def test_google_free_success(monkeypatch):
    client = FakeClient(get=lambda url, **kw: FakeResponse(payload=["你好"]))
    _install_client(monkeypatch, client)
    t = google.GoogleTranslator(_settings())
    assert t.translate("hello", "en", "zh-CN") == "你好"
    assert client.get_calls[0][1]["params"]["client"] == "dict-chrome-ex"


def test_google_free_429_rate_limited(monkeypatch):
    client = FakeClient(get=lambda url, **kw: FakeResponse(status=429, text="too many requests"))
    _install_client(monkeypatch, client)
    t = google.GoogleTranslator(_settings())
    with pytest.raises(RateLimited):
        t.translate("hello", "en", "zh-CN")


def test_google_api_success(monkeypatch):
    client = FakeClient(post=lambda url, **kw: FakeResponse(
        payload={"data": {"translations": [{"translatedText": "你好"}]}}))
    _install_client(monkeypatch, client)
    t = google.GoogleTranslator(_settings(google_api_key="k"))
    assert t.translate("hello", "en", "zh-CN") == "你好"


def test_google_api_401_bad_output(monkeypatch):
    client = FakeClient(post=lambda url, **kw: FakeResponse(status=401, text="unauthorized"))
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


def test_bing_web_token_parse_and_domain_rotation(monkeypatch):
    def get(url, **kwargs):
        if "cn.bing.com" in url:
            return FakeResponse(status=200, text="")  # 空页 → 被限流 → 换域名
        if "www.bing.com" in url:
            return FakeResponse(status=200, text=_BING_HTML)
        return FakeResponse(status=404, text="")

    client = FakeClient(
        get=get,
        post=lambda url, **kw: FakeResponse(
            payload=[{"translations": [{"text": "你好"}]}],
            headers={"Content-Type": "application/json"},
        ),
    )
    _install_client(monkeypatch, client)
    t = bing.BingTranslator(_settings())
    assert t.translate("hello", "en", "zh-CN") == "你好"
    urls = [u for u, _ in client.get_calls]
    assert urls == ["https://cn.bing.com/translator", "https://www.bing.com/translator"]
    assert client.post_calls[0][0].startswith("https://www.bing.com/ttranslatev3")


def test_bing_web_token_refresh_retry(monkeypatch):
    posts = iter([
        FakeResponse(status=401, text="expired"),
        FakeResponse(payload=[{"translations": [{"text": "你好"}]}],
                     headers={"Content-Type": "application/json"}),
    ])
    client = FakeClient(
        get=lambda url, **kw: FakeResponse(status=200, text=_BING_HTML),
        post=lambda url, **kw: next(posts),
    )
    _install_client(monkeypatch, client)
    t = bing.BingTranslator(_settings())
    assert t.translate("hello", "en", "zh-CN") == "你好"
    assert len(client.get_calls) == 2  # 初次 token + 401 后刷新
    assert len(client.post_calls) == 2


def test_bing_azure_success(monkeypatch):
    client = FakeClient(post=lambda url, **kw: FakeResponse(
        payload=[{"translations": [{"text": "你好"}]}]))
    _install_client(monkeypatch, client)
    t = bing.BingTranslator(_settings(bing_api_key="k", bing_region="eastasia"))
    assert t.translate("hello", "en", "zh-CN") == "你好"
    url, kwargs = client.post_calls[0]
    assert url == "https://api.cognitive.microsofttranslator.com/translate"
    assert kwargs["params"]["to"] == "zh-Hans"
    assert kwargs["headers"]["Ocp-Apim-Subscription-Key"] == "k"


# --------------------------------------------------------------------------- #
# baidu
# --------------------------------------------------------------------------- #
def test_baidu_missing_key_unavailable(monkeypatch):
    t = baidu.BaiduTranslator(_settings(baidu_appid="", baidu_appkey=""))
    assert t.available() == (False, "缺少 BAIDU_APPID / BAIDU_APPKEY")


def test_baidu_sign_and_throttle(monkeypatch):
    client = FakeClient(post=lambda url, **kw: FakeResponse(
        payload={"trans_result": [{"dst": "你好"}]}))
    _install_client(monkeypatch, client)
    monkeypatch.setattr(baidu.random, "randint", lambda *a: 1234567890)
    t = baidu.BaiduTranslator(_settings(baidu_appid="appid", baidu_appkey="appkey"))
    assert t.min_interval == 1.1
    assert t.translate("hello", "en", "zh-CN") == "你好"
    url, kwargs = client.post_calls[0]
    assert url == "https://fanyi-api.baidu.com/api/trans/vip/translate"
    data = kwargs["data"]
    assert data["appid"] == "appid"
    assert data["from"] == "english"
    assert data["to"] == "chinese (simplified)"
    assert data["salt"] == "1234567890"
    assert data["sign"] == hashlib.md5(b"appidhello1234567890appkey").hexdigest()


def test_baidu_error_code_54003_rate_limited(monkeypatch):
    client = FakeClient(post=lambda url, **kw: FakeResponse(
        payload={"error_code": "54003", "error_msg": "QPS limit"}))
    _install_client(monkeypatch, client)
    t = baidu.BaiduTranslator(_settings(baidu_appid="a", baidu_appkey="k"))
    with pytest.raises(RateLimited):
        t.translate("hello", "en", "zh-CN")


# --------------------------------------------------------------------------- #
# rwkv
# --------------------------------------------------------------------------- #
def test_rwkv_success(monkeypatch):
    client = FakeClient(post=lambda url, **kw: FakeResponse(
        payload={"choices": [{"text": "这是结果"}],
                 "usage": {"prompt_tokens": 10, "completion_tokens": 5}}))
    _install_client(monkeypatch, client)
    t = rwkv.RwkvTranslator(_settings())
    assert t.translate("Hello", "en", "zh-CN") == "这是结果"
    assert t.last_usage == {"prompt_tokens": 10, "completion_tokens": 5}
    assert t.last_prompt_tokens == 10
    assert t.last_completion_tokens == 5
    url, kwargs = client.post_calls[0]
    assert url == "http://127.0.0.1:8000/v1/completions"
    assert kwargs["json"]["model"] == "rwkv"
    assert kwargs["json"]["stream"] is False
    assert kwargs["timeout"] == (5.0, 300.0)


def test_rwkv_400_model_not_loaded(monkeypatch):
    client = FakeClient(post=lambda url, **kw: FakeResponse(status=400, text="model not loaded"))
    _install_client(monkeypatch, client)
    t = rwkv.RwkvTranslator(_settings())
    with pytest.raises(ModelNotLoaded):
        t.translate("Hello", "en", "zh-CN")


def test_rwkv_connection_failed(monkeypatch):
    def boom(url, **kw):
        raise httpx.ConnectError("connection refused")

    client = FakeClient(post=boom)
    _install_client(monkeypatch, client)
    t = rwkv.RwkvTranslator(_settings())
    with pytest.raises(ConnectionFailed):
        t.translate("Hello", "en", "zh-CN")


def test_rwkv_empty_output_retries(monkeypatch):
    client = FakeClient(post=lambda url, **kw: FakeResponse(payload={"choices": [{"text": "  "}]}))
    _install_client(monkeypatch, client)
    t = rwkv.RwkvTranslator(_settings(rwkv_retries=2))
    with pytest.raises(BadOutput):
        t.translate("原文", "en", "zh-CN")
    assert len(client.post_calls) == 3


def test_rwkv_probe(monkeypatch):
    def get(url, **kwargs):
        if url.endswith("/status"):
            return FakeResponse(payload={"status": 3})
        return FakeResponse(payload={"data": [{"id": "translate-20times-3B"}]})

    client = FakeClient(get=get)
    _install_client(monkeypatch, client)
    t = rwkv.RwkvTranslator(_settings())
    assert t.probe() == {"reachable": True, "status": "Working",
                         "model": "translate-20times-3B", "error": None}


# --------------------------------------------------------------------------- #
# build_translator / 语言映射
# --------------------------------------------------------------------------- #
def test_build_translator_known_and_unknown():
    assert transport.build_translator("google", _settings()).name == "google"
    assert transport.build_translator("rwkv", _settings()).name == "rwkv"
    with pytest.raises(ValueError):
        transport.build_translator("yandex", _settings())


def test_to_provider_lang():
    assert to_provider_lang("google", "en") == "en"
    assert to_provider_lang("bing", "zh-CN") == "zh-Hans"
    assert to_provider_lang("bing", "iw") == "he"
    assert to_provider_lang("baidu", "en") == "english"
    with pytest.raises(UnsupportedLanguage):
        to_provider_lang("baidu", "la")


# --------------------------------------------------------------------------- #
# tts
# --------------------------------------------------------------------------- #
def test_tts_bytes_success(monkeypatch):
    client = FakeClient(get=lambda url, **kw: FakeResponse(status=200, content=b"mp3data"))
    _install_client(monkeypatch, client)
    assert tts.tts_bytes("你好", "zh-CN") == b"mp3data"


def test_tts_bytes_empty(monkeypatch):
    client = FakeClient(get=lambda url, **kw: FakeResponse(status=200, content=b""))
    _install_client(monkeypatch, client)
    with pytest.raises(BadOutput):
        tts.tts_bytes("你好", "zh-CN")


def test_tts_bytes_empty_text():
    with pytest.raises(BadOutput):
        tts.tts_bytes("   ", "zh-CN")
