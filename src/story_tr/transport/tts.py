"""Google TTS（httpx 直连，替代 gTTS）。"""
from __future__ import annotations

import httpx

from story_tr.errors import BadOutput, ConnectionFailed

from .base import split_chunks

TTS_URL = "https://translate.google.com/translate_tts"
TTS_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
TTS_CHUNK_SIZE = 200


def _fetch_chunk(client: httpx.Client, text: str, lang: str) -> bytes:
    params = {"ie": "UTF-8", "client": "tw-ob", "q": text, "tl": lang}
    resp = client.get(TTS_URL, params=params, timeout=30.0)
    resp.raise_for_status()
    return resp.content


def tts_bytes(text: str, lang: str, proxy: str = "") -> bytes:
    """用 Google TTS 端点生成 mp3 字节；长文本按 200 字分块拼接。"""
    text = (text or "").strip()
    if not text:
        raise BadOutput("TTS 无有效文本")
    if proxy:
        client = httpx.Client(follow_redirects=True, timeout=30.0,
                              headers={"User-Agent": TTS_UA}, proxy=proxy)
    else:
        client = httpx.Client(follow_redirects=True, timeout=30.0,
                              headers={"User-Agent": TTS_UA})
    try:
        parts = [_fetch_chunk(client, chunk, lang)
                 for chunk in split_chunks(text, TTS_CHUNK_SIZE)]
    except httpx.HTTPError as exc:
        raise ConnectionFailed(f"TTS 请求失败（{type(exc).__name__}）：{exc}") from exc
    finally:
        client.close()
    data = b"".join(parts)
    if not data:
        raise BadOutput("TTS 返回空音频")
    return data
