"""Google 翻译后端：免 key clients5 端点与官方 Cloud Translation v2。"""
from __future__ import annotations

from html import unescape

from story_tr.config import Settings
from story_tr.errors import BadOutput

from .base import BaseTranslator, split_chunks

GOOGLE_DICT_URL = "https://clients5.google.com/translate_a/t"
GOOGLE_API_URL = "https://translation.googleapis.com/language/translate/v2"
GOOGLE_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# 单次请求的文本上限：Google 免费 GET 端点约 1800 字（中文 URL 编码后约 9 字节/字）即 400，故留余量
MAX_CHARS_PER_REQUEST = 1500


class GoogleTranslator(BaseTranslator):
    """Google 翻译：有 key 走官方 v2，否则走 clients5 免 key 端点。"""

    name = "google"
    min_interval = 0.0

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self._client.headers.update({"User-Agent": GOOGLE_UA})

    def translate(self, text: str, src: str, tgt: str) -> str:
        chunks = split_chunks(text, MAX_CHARS_PER_REQUEST)
        return "".join(self._translate_chunk(c, src, tgt) for c in chunks)

    def _translate_chunk(self, text: str, src: str, tgt: str) -> str:
        if self.settings.google_api_key:
            return self._api_translate(text, src, tgt)
        self._throttle()
        return self._dict_translate(text, src, tgt)

    def _dict_translate(self, text: str, src: str, tgt: str) -> str:
        """clients5 dict-chrome-ex 免 key 端点（单条）。"""
        params = {"client": "dict-chrome-ex", "sl": src, "tl": tgt, "q": text}
        resp = self._get(GOOGLE_DICT_URL, params=params, timeout=20.0)
        data = self._read_json(resp)
        if isinstance(data, list) and data:
            first = data[0]
            if isinstance(first, str):
                out = "".join(x for x in data if isinstance(x, str))
            elif isinstance(first, list):
                out = first[0] if first and isinstance(first[0], str) else ""
            else:
                out = str(first)
        else:
            out = str(data)
        out = out.strip()
        if not out:
            raise BadOutput("google(dict) 返回空结果")
        return out

    def _dict_translate_batch(self, texts: list[str], src: str, tgt: str) -> list[str]:
        """clients5 支持重复 `q`，响应与 `q` 一一对应。"""
        qs = [("client", "dict-chrome-ex"), ("sl", src), ("tl", tgt)] + [("q", t) for t in texts]
        resp = self._get(GOOGLE_DICT_URL, params=qs, timeout=30.0)
        data = self._read_json(resp)
        if (isinstance(data, list) and len(data) == len(texts)
                and all(isinstance(x, str) and x.strip() for x in data)):
            return [x.strip() for x in data]
        raise BadOutput(f"google(dict) 批量返回形状不符（期望 {len(texts)} 条）")

    def _api_translate(self, text: str, src: str, tgt: str) -> str:
        """Google Cloud Translation v2（官方 API）。"""
        payload: dict[str, object] = {"q": text, "target": tgt, "format": "text"}
        if src:
            payload["source"] = src
        resp = self._post(GOOGLE_API_URL, params={"key": self.settings.google_api_key},
                          json=payload, timeout=60.0)
        if resp.status_code in (401, 403):
            raise BadOutput("google(api) 认证失败（检查 GOOGLE_API_KEY）")
        data = self._read_json(resp)
        try:
            out = data["data"]["translations"][0]["translatedText"]
        except (KeyError, IndexError, TypeError):
            raise BadOutput(f"google(api) 返回结构异常：{resp.text[:120]}") from None
        out = unescape(out or "").strip()
        if not out:
            raise BadOutput("google(api) 返回空结果")
        return out
