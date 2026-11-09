"""`story_tr.transport` 传输层单测：全部 mock HTTP，不联网。"""

import hashlib

import httpx
import pytest

from story_tr import transport
from story_tr.errors import (
    BadOutput,
    ConnectionFailed,
    ModelNotLoaded,
    RateLimited,
    UnsupportedLanguage,
)
from story_tr.transport import baidu, bing, google, rwkv, tts
from story_tr.transport.base import BaseTranslator, to_provider_lang
from tests.support import (
    FakeClient,
    FakeResponse,
)
from tests.support import (
    install_client as _install_client,
)
from tests.support import (
    settings as _settings,
)

_BING_HTML = 'IG:"IG1"; data-iid="iid.1"; params_AbusePreventionHelper = [123, "token1", 456];'


def _bing_web(monkeypatch, get, post):
    _install_client(monkeypatch, FakeClient(get=get, post=post))


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
    client = FakeClient(
        post=lambda url, **kw: FakeResponse(payload={"data": {"translations": [{"translatedText": "你好"}]}})
    )
    _install_client(monkeypatch, client)
    t = google.GoogleTranslator(_settings(google_api_key="k"))
    assert t.translate("hello", "en", "zh-CN") == "你好"


def test_google_api_401_bad_output(monkeypatch):
    client = FakeClient(post=lambda url, **kw: FakeResponse(status=401, text="unauthorized"))
    _install_client(monkeypatch, client)
    t = google.GoogleTranslator(_settings(google_api_key="k"))
    with pytest.raises(BadOutput):
        t.translate("hello", "en", "zh-CN")


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
    posts = iter(
        [
            FakeResponse(status=401, text="expired"),
            FakeResponse(
                payload=[{"translations": [{"text": "你好"}]}], headers={"Content-Type": "application/json"}
            ),
        ]
    )
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
    client = FakeClient(post=lambda url, **kw: FakeResponse(payload=[{"translations": [{"text": "你好"}]}]))
    _install_client(monkeypatch, client)
    t = bing.BingTranslator(_settings(bing_api_key="k", bing_region="eastasia"))
    assert t.translate("hello", "en", "zh-CN") == "你好"
    url, kwargs = client.post_calls[0]
    assert url == "https://api.cognitive.microsofttranslator.com/translate"
    assert kwargs["params"]["to"] == "zh-Hans"
    assert kwargs["headers"]["Ocp-Apim-Subscription-Key"] == "k"


def test_baidu_missing_key_unavailable(monkeypatch):
    t = baidu.BaiduTranslator(_settings(baidu_appid="", baidu_appkey=""))
    assert t.available() == (False, "缺少 BAIDU_APPID / BAIDU_APPKEY")


def test_baidu_sign_and_throttle(monkeypatch):
    client = FakeClient(post=lambda url, **kw: FakeResponse(payload={"trans_result": [{"dst": "你好"}]}))
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
    client = FakeClient(
        post=lambda url, **kw: FakeResponse(payload={"error_code": "54003", "error_msg": "QPS limit"})
    )
    _install_client(monkeypatch, client)
    t = baidu.BaiduTranslator(_settings(baidu_appid="a", baidu_appkey="k"))
    with pytest.raises(RateLimited):
        t.translate("hello", "en", "zh-CN")


def test_rwkv_success(monkeypatch):
    client = FakeClient(
        post=lambda url, **kw: FakeResponse(
            payload={
                "choices": [{"text": "这是结果"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            }
        )
    )
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
    assert t.probe() == {
        "reachable": True,
        "status": "Working",
        "model": "translate-20times-3B",
        "error": None,
    }


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
    client = FakeClient(
        post=lambda url, **kw: FakeResponse(payload={"data": {"translations": [{"translatedText": ""}]}})
    )
    _install_client(monkeypatch, client)
    t = google.GoogleTranslator(_settings(google_api_key="k"))
    with pytest.raises(BadOutput):
        t.translate("hello", "en", "zh-CN")


def test_bing_web_all_domains_fail(monkeypatch):
    _bing_web(
        monkeypatch,
        get=lambda url, **kw: FakeResponse(status=200, text=""),
        post=lambda url, **kw: FakeResponse(),
    )
    t = bing.BingTranslator(_settings())
    with pytest.raises(RateLimited):
        t.translate("hello", "en", "zh-CN")


def test_bing_web_429(monkeypatch):
    _bing_web(
        monkeypatch,
        get=lambda url, **kw: FakeResponse(status=200, text=_BING_HTML),
        post=lambda url, **kw: FakeResponse(status=429, text="limited"),
    )
    t = bing.BingTranslator(_settings())
    with pytest.raises(RateLimited):
        t.translate("hello", "en", "zh-CN")


def test_bing_web_non_200(monkeypatch):
    _bing_web(
        monkeypatch,
        get=lambda url, **kw: FakeResponse(status=200, text=_BING_HTML),
        post=lambda url, **kw: FakeResponse(status=500, text="boom"),
    )
    t = bing.BingTranslator(_settings())
    with pytest.raises(ConnectionFailed):
        t.translate("hello", "en", "zh-CN")


def test_bing_web_non_json_content_type(monkeypatch):
    _bing_web(
        monkeypatch,
        get=lambda url, **kw: FakeResponse(status=200, text=_BING_HTML),
        post=lambda url, **kw: FakeResponse(status=200, text="<html>"),
    )
    t = bing.BingTranslator(_settings())
    with pytest.raises(RateLimited):
        t.translate("hello", "en", "zh-CN")


def test_bing_web_bad_structure(monkeypatch):
    _bing_web(
        monkeypatch,
        get=lambda url, **kw: FakeResponse(status=200, text=_BING_HTML),
        post=lambda url, **kw: FakeResponse(payload=[], headers={"Content-Type": "application/json"}),
    )
    t = bing.BingTranslator(_settings())
    with pytest.raises(RateLimited):
        t.translate("hello", "en", "zh-CN")


def test_bing_refresh_5xx_connection_failed(monkeypatch):
    _bing_web(
        monkeypatch,
        get=lambda url, **kw: FakeResponse(status=503, text="down"),
        post=lambda url, **kw: FakeResponse(status=200, text=""),
    )
    t = bing.BingTranslator(_settings())
    with pytest.raises(ConnectionFailed):
        t.translate("hello", "en", "zh-CN")


def test_bing_refresh_404_rate_limited(monkeypatch):
    _bing_web(
        monkeypatch,
        get=lambda url, **kw: FakeResponse(status=404, text="missing"),
        post=lambda url, **kw: FakeResponse(status=200, text=""),
    )
    t = bing.BingTranslator(_settings())
    with pytest.raises(RateLimited):
        t.translate("hello", "en", "zh-CN")


def test_bing_web_invalid_json_body(monkeypatch):
    _bing_web(
        monkeypatch,
        get=lambda url, **kw: FakeResponse(status=200, text=_BING_HTML),
        post=lambda url, **kw: FakeResponse(
            status=200, text="{not-json", headers={"Content-Type": "application/json"}
        ),
    )
    t = bing.BingTranslator(_settings())
    with pytest.raises(RateLimited):
        t.translate("hello", "en", "zh-CN")


def test_bing_web_401_twice(monkeypatch):
    _bing_web(
        monkeypatch,
        get=lambda url, **kw: FakeResponse(status=200, text=_BING_HTML),
        post=lambda url, **kw: FakeResponse(status=401, text="expired"),
    )
    t = bing.BingTranslator(_settings())
    with pytest.raises(ConnectionFailed):
        t.translate("hello", "en", "zh-CN")


def test_bing_azure_bad_structure(monkeypatch):
    _bing_web(monkeypatch, get=None, post=lambda url, **kw: FakeResponse(payload=[]))
    t = bing.BingTranslator(_settings(bing_api_key="k"))
    with pytest.raises(BadOutput):
        t.translate("hello", "en", "zh-CN")


def test_bing_azure_empty_result(monkeypatch):
    _bing_web(
        monkeypatch, get=None, post=lambda url, **kw: FakeResponse(payload=[{"translations": [{"text": ""}]}])
    )
    t = bing.BingTranslator(_settings(bing_api_key="k"))
    with pytest.raises(BadOutput):
        t.translate("hello", "en", "zh-CN")


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
    client = FakeClient(
        post=lambda url, **kw: FakeResponse(payload={"error_code": "58001", "error_msg": "invalid"})
    )
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
    client = FakeClient(post=lambda url, **kw: FakeResponse(payload={"trans_result": [{"dst": ""}]}))
    _install_client(monkeypatch, client)
    t = baidu.BaiduTranslator(_settings(baidu_appid="a", baidu_appkey="k"))
    with pytest.raises(BadOutput):
        t.translate("hello", "en", "zh-CN")


def test_baidu_multiline_result_joined(monkeypatch):
    client = FakeClient(
        post=lambda url, **kw: FakeResponse(
            payload={"trans_result": [{"src": "a", "dst": "甲"}, {"src": "b", "dst": "乙"}]}
        )
    )
    _install_client(monkeypatch, client)
    t = baidu.BaiduTranslator(_settings(baidu_appid="a", baidu_appkey="k"))
    assert t.translate("a\nb", "en", "zh-CN") == "甲\n乙"


def test_baidu_long_text_is_chunked(monkeypatch):
    client = FakeClient(post=lambda url, **kw: FakeResponse(payload={"trans_result": [{"dst": "译文"}]}))
    _install_client(monkeypatch, client)
    t = baidu.BaiduTranslator(_settings(baidu_appid="a", baidu_appkey="k"))
    t.min_interval = 0
    text = "a" * (baidu.BAIDU_MAX_CHARS + 500)
    assert t.translate(text, "en", "zh-CN") == "译文译文"
    assert len(client.post_calls) == 2


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


def test_rwkv_default_prompt_matches_finetuned_format(monkeypatch):
    client = FakeClient(post=lambda url, **kw: FakeResponse(payload={"choices": [{"text": " 你好"}]}))
    _install_client(monkeypatch, client)
    t = rwkv.RwkvTranslator(_settings())
    assert t.translate("Hello", "en", "zh-CN") == "你好"
    payload = client.post_calls[0][1]["json"]
    assert payload["prompt"] == "Translate this into Chinese.\n\nEnglish: Hello\nChinese:"
    assert "\n\nEnglish:" in payload["stop"]
    assert "\n\nChinese:" in payload["stop"]


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
