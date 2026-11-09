"""chain_core：签名、后端轮换与单步翻译。"""

import pytest

from story_tr import models
from story_tr.config import Settings
from story_tr.errors import (
    BadOutput,
    ConnectionFailed,
    ModelNotLoaded,
    QuotaExceeded,
    RateLimited,
    UnsupportedLanguage,
)
from story_tr.services import chain_core
from tests.support import FakeTranslator


class TestTranslateStep:
    def _step(self, translators, providers, balance=False):
        return chain_core.translate_step("你好", "zh-CN", "en", translators, providers, balance, Settings())

    def test_unavailable(self):
        t = FakeTranslator(available=(False, "nokey"))
        with pytest.raises(ConnectionFailed):
            self._step([t], ["google"])

    def test_unsupported_language(self):
        t = FakeTranslator(error=UnsupportedLanguage("x"))
        with pytest.raises(UnsupportedLanguage):
            self._step([t], ["google"])

    def test_quota(self):
        t = FakeTranslator(error=QuotaExceeded("quota"))
        with pytest.raises(QuotaExceeded):
            self._step([t], ["google"])

    def test_model_not_loaded(self):
        t = FakeTranslator(error=ModelNotLoaded("m"))
        with pytest.raises(ModelNotLoaded):
            self._step([t], ["google"])

    def test_rate_limited_single(self):
        t = FakeTranslator(error=RateLimited("limited"))
        with pytest.raises(RateLimited):
            self._step([t], ["google"])

    def test_rate_limited_beats_connection(self):
        t1 = FakeTranslator(error=ConnectionFailed("c"))
        t2 = FakeTranslator(error=RateLimited("r"))
        with pytest.raises(RateLimited):
            self._step([t1, t2], ["google", "bing"])

    def test_bad_output_beats_rate_limit(self):
        t1 = FakeTranslator(error=RateLimited("r"))
        t2 = FakeTranslator(error=BadOutput("b"))
        with pytest.raises(BadOutput):
            self._step([t1, t2], ["google", "bing"])

    def test_bad_output_single(self):
        t = FakeTranslator(error=BadOutput("bad"))
        with pytest.raises(BadOutput):
            self._step([t], ["google"])

    def test_empty_result_single(self):
        t = FakeTranslator(result="")
        with pytest.raises(BadOutput):
            self._step([t], ["google"])

    def test_fallback_after_connection(self):
        t1 = FakeTranslator(error=ConnectionFailed("c"))
        t2 = FakeTranslator(result="译文")
        name, out, meta = self._step([t1, t2], ["google", "bing"])
        assert name == "bing"
        assert out == "译文"
        assert meta is None

    def test_fallback_after_bad_output(self):
        t1 = FakeTranslator(error=BadOutput("bad"))
        t2 = FakeTranslator(result="译文")
        name, out, _ = self._step([t1, t2], ["google", "bing"])
        assert name == "bing"
        assert out == "译文"

    def test_order_balance(self):
        t1 = FakeTranslator(name="google")
        t2 = FakeTranslator(name="bing")
        pairs = chain_core.order([t1, t2], ["google", "bing"], balance=True)
        assert len(pairs) == 2

    def test_rwkv_meta(self):
        t = FakeTranslator(name="rwkv")
        t.last_chunks = 2
        meta = chain_core.rwkv_meta(t, Settings())
        assert meta is not None and meta.chunks == 2
        assert chain_core.rwkv_meta(FakeTranslator(name="google"), Settings()) is None

    def test_merge_rwkv(self):
        meta = models.RwkvMeta(url="u", model="m", template_sha1="s", chunks=1)
        step = models.TranslationStep(
            index=0, kind="rwkv20", lang="zh-CN", provider="rwkv", text="x", rwkv=meta
        )
        merged = chain_core.merge_rwkv([step])
        assert merged is not None and merged.chunks == 1
        assert chain_core.merge_rwkv([]) is None
