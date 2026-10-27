"""Bing 翻译后端：免 key 网页通道（多域名轮换）+ Azure 官方接口。"""
from __future__ import annotations

import json
import os
import re
import time

import httpx

from story_tr.config import Settings
from story_tr.errors import BadOutput, ConnectionFailed, RateLimited, TranslationError

from .base import BaseTranslator, to_provider_lang

BING_WEB_BASES = ["https://cn.bing.com", "https://www.bing.com"]
BING_WEB_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
BING_WEB_TIMEOUT = 40.0
BING_WEB_TOKEN_TTL = 3000.0  # 秒；页面 token 有效期约 1 小时，提前刷新
AZURE_URL = "https://api.cognitive.microsofttranslator.com/translate"


class BingTranslator(BaseTranslator):
    """Bing 翻译：有 key 走 Azure 官方接口，否则走网页免 key 通道。"""

    name = "bing"
    min_interval = 0.0

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self._client.headers.update({"User-Agent": BING_WEB_UA})
        self._base: str | None = None
        self._ig: str | None = None
        self._iid: str | None = None
        self._key: str | None = None
        self._token: str | None = None
        self._expire = 0.0

    def translate(self, text: str, src: str, tgt: str) -> str:
        if self.settings.bing_api_key:
            return self._azure_translate(text, src, tgt)
        self._throttle()
        return self._web_translate(text, src, tgt)

    # ---- 官方 Azure ----
    def _azure_translate(self, text: str, src: str, tgt: str) -> str:
        params = {
            "api-version": "3.0",
            "from": to_provider_lang("bing", src),
            "to": to_provider_lang("bing", tgt),
        }
        headers = {
            "Ocp-Apim-Subscription-Key": self.settings.bing_api_key,
            "Content-Type": "application/json",
        }
        if self.settings.bing_region:
            headers["Ocp-Apim-Subscription-Region"] = self.settings.bing_region
        resp = self._post(AZURE_URL, params=params, headers=headers,
                          json=[{"Text": text}], timeout=BING_WEB_TIMEOUT)
        data = self._read_json(resp)
        try:
            out = data[0]["translations"][0]["text"]
        except (KeyError, IndexError, TypeError):
            raise BadOutput(f"bing(azure) 返回结构异常：{str(data)[:120]}") from None
        out = (out or "").strip()
        if not out:
            raise BadOutput("bing(azure) 返回空结果")
        return out

    # ---- 免 key 网页通道 ----
    def _web_bases(self) -> list[str]:
        env = os.getenv("BING_WEB_BASE")
        if env:
            return [b.strip().rstrip("/") for b in env.split(",") if b.strip()]
        return list(BING_WEB_BASES)

    def _web_translate(self, text: str, src_code: str, tgt_code: str) -> str:
        """多域名轮换；token 失效自动刷新；全部域名失败则抛 RateLimited。"""
        src = to_provider_lang("bing", src_code)
        tgt = to_provider_lang("bing", tgt_code)
        bases = self._web_bases()
        if self._base in bases:  # 优先上次成功的域名
            bases = [self._base] + [b for b in bases if b != self._base]

        last: TranslationError | None = None
        soft = False
        for base in bases:
            if self._base != base or not self._token or time.monotonic() > self._expire:
                self._base = base
                self._token = None
                try:
                    self._refresh()
                except TranslationError as exc:
                    last = exc
                    soft = soft or not isinstance(exc, ConnectionFailed)
                    continue
            try:
                return self._web_call(text, src, tgt)
            except TranslationError as exc:
                last = exc
                soft = soft or not isinstance(exc, ConnectionFailed)
        if last is None:
            raise ConnectionFailed("bing(web) 无可用域名")
        if soft:
            raise RateLimited(f"bing(web) 所有域名均失败：{last}")
        raise last

    def _refresh(self) -> None:
        """在当前 base 上解析 IG / IID / token。"""
        base = self._base or BING_WEB_BASES[0]
        resp = self._get(base + "/translator", timeout=BING_WEB_TIMEOUT)
        if resp.status_code == 429:
            raise RateLimited("bing(web) 翻译页 HTTP 429 被限流")
        if resp.status_code >= 500:
            raise ConnectionFailed(f"bing(web) 翻译页 HTTP {resp.status_code}")
        if resp.status_code >= 400:
            raise RateLimited(f"bing(web) 翻译页 HTTP {resp.status_code}")
        html = resp.text
        ig = re.search(r'IG:"([^"]+)"', html)
        iid = re.search(r'data-iid="([^"]+)"', html)
        abuse = re.search(r"params_AbusePreventionHelper\s*=\s*(\[[^\]]*\])", html)
        if not (ig and iid and abuse):
            raise RateLimited("bing(web) 页面未解析到 IG/IID/token（可能被限流）")
        key, token = json.loads(abuse.group(1))[:2]
        self._ig = ig.group(1)
        self._iid = iid.group(1)
        self._key = key
        self._token = token
        self._expire = time.monotonic() + BING_WEB_TOKEN_TTL

    def _web_post(self, text: str, src: str, tgt: str) -> httpx.Response:
        base = self._base or BING_WEB_BASES[0]
        url = f"{base}/ttranslatev3?isVertical=1&IG={self._ig}&IID={self._iid}"
        headers = {
            "Referer": base + "/translator",
            "Origin": base,
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Accept": "*/*",
            "X-Requested-With": "XMLHttpRequest",
        }
        data = {"fromLang": src, "text": text, "to": tgt,
                "token": self._token, "key": self._key}
        return self._post(url, data=data, headers=headers, timeout=BING_WEB_TIMEOUT)

    def _web_call(self, text: str, src: str, tgt: str) -> str:
        """在当前域名上执行一次翻译并解析结果。"""
        for attempt in (1, 2):
            resp = self._web_post(text, src, tgt)
            if resp.status_code == 429:
                raise RateLimited("bing(web) HTTP 429 被限流")
            if resp.status_code in (401, 403) and attempt == 1:
                self._refresh()  # token 失效 → 刷新后重试一次
                continue
            if resp.status_code != 200:
                raise ConnectionFailed(f"bing(web) HTTP {resp.status_code}")
            if "json" not in (resp.headers.get("Content-Type") or "").lower():
                raise RateLimited(f"bing(web) 非 JSON 响应（可能被限流）：{resp.text[:60]!r}")
            try:
                payload = resp.json()
            except ValueError:
                raise BadOutput(f"bing(web) 响应不是合法 JSON：{resp.text[:120]}") from None
            try:
                out = payload[0]["translations"][0]["text"]
            except (KeyError, IndexError, TypeError):
                raise BadOutput(f"bing(web) 返回结构异常：{str(payload)[:120]}") from None
            out = (out or "").strip()
            if not out:
                raise BadOutput("bing(web) 返回空结果")
            return out
        raise RateLimited("bing(web) token 刷新后仍失败")
