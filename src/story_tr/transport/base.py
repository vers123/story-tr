"""传输层协议与基类。

`Translator` 协议统一「如何调用某个翻译服务」；`BaseTranslator` 提供共享实现：
线程内复用的 `httpx.Client`、线程安全的最小请求间隔、HTTP 状态 → 错误层级映射，
以及后端语言代码映射与分块等纯工具函数。
"""
from __future__ import annotations

import threading
import time
from typing import Any, Protocol

import httpx

from story_tr.config import Settings
from story_tr.errors import BadOutput, ConnectionFailed, ModelNotLoaded, RateLimited

from ..core.lang import BAIDU_NAMES, BING_OVERRIDES, to_provider_lang
from ..core.segment import split_chunks

__all__ = [
    "BAIDU_NAMES",
    "BING_OVERRIDES",
    "BaseTranslator",
    "DEFAULT_TIMEOUT",
    "Translator",
    "split_chunks",
    "to_provider_lang",
]

DEFAULT_TIMEOUT = 30.0

_NOT_LOADED_KEYWORDS = ("model not loaded", "not loaded", "no model", "failed to load")


class Translator(Protocol):
    """统一翻译后端协议：只负责「如何调用某个翻译服务」，不做业务编排。"""

    name: str

    def translate(self, text: str, src: str, tgt: str) -> str: ...
    def available(self) -> tuple[bool, str]: ...


class BaseTranslator:
    """翻译后端基类：统一客户端、限速与错误映射。"""

    name: str = ""
    min_interval: float = 0.0

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client = httpx.Client(follow_redirects=True, timeout=DEFAULT_TIMEOUT)
        self._lock = threading.Lock()
        self._last_ts = 0.0

    def available(self) -> tuple[bool, str]:
        """后端是否可用（默认可用；缺 key 的后端自行覆盖）。"""
        return True, ""

    def close(self) -> None:
        """关闭底层连接池。"""
        self._client.close()

    def _throttle(self) -> None:
        """线程安全地保证相邻请求的最小间隔。"""
        if self.min_interval <= 0:
            return
        with self._lock:
            wait = self._last_ts + self.min_interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last_ts = time.monotonic()

    def _get(self, url: str, **kwargs: Any) -> httpx.Response:
        try:
            return self._client.get(url, **kwargs)
        except httpx.TimeoutException as exc:
            raise ConnectionFailed(f"请求超时：{exc}") from exc
        except httpx.HTTPError as exc:
            raise ConnectionFailed(f"请求失败（{type(exc).__name__}）：{exc}") from exc

    def _post(self, url: str, **kwargs: Any) -> httpx.Response:
        try:
            return self._client.post(url, **kwargs)
        except httpx.TimeoutException as exc:
            raise ConnectionFailed(f"请求超时：{exc}") from exc
        except httpx.HTTPError as exc:
            raise ConnectionFailed(f"请求失败（{type(exc).__name__}）：{exc}") from exc

    def _map_error(self, resp: httpx.Response) -> None:
        """把 HTTP 状态 / 文本映射为统一错误层级（先 429，再未加载关键词）。"""
        status = resp.status_code
        text = resp.text.strip()
        if status == 429:
            raise RateLimited(f"HTTP 429 被限流：{text[:120]}")
        low = text.lower()
        if any(k in low for k in _NOT_LOADED_KEYWORDS):
            raise ModelNotLoaded(f"模型未加载：{text[:120]}")
        if status >= 500:
            raise ConnectionFailed(f"HTTP {status}：{text[:120]}")
        if status >= 400:
            raise BadOutput(f"HTTP {status}：{text[:120]}")

    def _read_json(self, resp: httpx.Response) -> Any:
        """校验状态后解析 JSON；空或非 JSON → BadOutput。"""
        self._map_error(resp)
        if not resp.text.strip():
            raise BadOutput("响应为空")
        try:
            return resp.json()
        except ValueError as exc:
            raise BadOutput(f"响应不是 JSON：{resp.text[:120]}") from exc
