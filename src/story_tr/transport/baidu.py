"""百度翻译开放平台后端：APPID + APPKEY 签名，QPS=1 限速。"""
from __future__ import annotations

import hashlib
import random

import httpx

from story_tr.errors import BadOutput, RateLimited

from .base import BaseTranslator, split_chunks, to_provider_lang

BAIDU_URL = "https://fanyi-api.baidu.com/api/trans/vip/translate"
BAIDU_MIN_INTERVAL = 1.1
# 标准版单次 q 上限 6000 字节；按字符留余量（中文 3 字节/字）
BAIDU_MAX_CHARS = 1800
# 54003 = QPS 超限、54001 = 请求过快，均按限流处理
BAIDU_RATE_LIMIT_CODES = {"54001", "54003"}


class BaiduTranslator(BaseTranslator):
    """百度翻译开放平台（APPID + APPKEY 签名，标准版 QPS=1）。"""

    name = "baidu"
    min_interval = BAIDU_MIN_INTERVAL

    def available(self) -> tuple[bool, str]:
        if not (self.settings.baidu_appid and self.settings.baidu_appkey):
            return False, "缺少 BAIDU_APPID / BAIDU_APPKEY"
        return True, ""

    def translate(self, text: str, src: str, tgt: str) -> str:
        if not (self.settings.baidu_appid and self.settings.baidu_appkey):
            raise BadOutput("缺少 BAIDU_APPID / BAIDU_APPKEY")
        src_name = to_provider_lang("baidu", src)
        tgt_name = to_provider_lang("baidu", tgt)
        chunks = split_chunks(text, BAIDU_MAX_CHARS)
        return "".join(self._translate_chunk(c, src_name, tgt_name) for c in chunks)

    def _translate_chunk(self, text: str, src_name: str, tgt_name: str) -> str:
        self._throttle()
        salt = str(random.randint(1000000000, 9999999999))
        sign = hashlib.md5(
            (self.settings.baidu_appid + text + salt + self.settings.baidu_appkey).encode("utf-8")
        ).hexdigest()
        data = {
            "q": text,
            "from": src_name,
            "to": tgt_name,
            "appid": self.settings.baidu_appid,
            "salt": salt,
            "sign": sign,
        }
        resp = self._post(BAIDU_URL, data=data, timeout=30.0)
        return self._parse(resp)

    def _parse(self, resp: httpx.Response) -> str:
        data = self._read_json(resp)
        if not isinstance(data, dict):
            raise BadOutput(f"baidu 返回结构异常：{str(data)[:120]}")
        error_code = data.get("error_code")
        if error_code:
            code = str(error_code)
            if code in BAIDU_RATE_LIMIT_CODES:
                raise RateLimited(f"baidu 错误码 {code}：{data.get('error_msg', '')}")
            raise BadOutput(f"baidu 错误码 {code}：{data.get('error_msg', '')}")
        rows = data.get("trans_result")
        if not isinstance(rows, list) or not rows:
            raise BadOutput(f"baidu 返回结构异常：{str(data)[:120]}")
        # 百度按换行拆分 q，返回多条记录；按原顺序用换行拼接，避免只取首条导致内容丢失
        parts: list[str] = []
        for row in rows:
            if not isinstance(row, dict):
                raise BadOutput(f"baidu 返回结构异常：{str(data)[:120]}")
            dst = (row.get("dst") or "").strip()
            if dst:
                parts.append(dst)
        out = "\n".join(parts).strip()
        if not out:
            raise BadOutput("baidu 返回空结果")
        return out
