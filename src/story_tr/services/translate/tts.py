"""语音生成（带连接类重试）。"""

from __future__ import annotations

import logging
import time

from story_tr.config import Settings
from story_tr.errors import ConnectionFailed
from story_tr.transport.tts import tts_bytes

log = logging.getLogger("story_tr.services.translate.tts")

TARGET_ZH = "zh-CN"
TTS_ATTEMPTS = 3
TTS_BACKOFF = 0.5


def tts_with_retry(text: str, settings: Settings, attempts: int = TTS_ATTEMPTS) -> bytes:
    """生成语音；连接类失败按指数退避重试，空文本等业务失败直接抛出。"""
    delay = TTS_BACKOFF
    last: Exception | None = None
    for attempt in range(1, max(1, attempts) + 1):
        try:
            return tts_bytes(text, TARGET_ZH, settings.proxy or "")
        except ConnectionFailed as exc:
            last = exc
            if attempt < attempts:
                log.warning("语音生成失败（第 %d/%d 次）：%s；%.1f 秒后重试。", attempt, attempts, exc, delay)
                time.sleep(delay)
                delay *= 2
    raise last or ConnectionFailed("语音生成失败")
