#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""多后端「来回翻译 N 次」链路工具。

流程：
    原文 --(langdetect 判语言)--> 非英文先译成英文 --> 按路线翻译 N 次 --> 译回中文。

后端（优先官方 API，无 key 时回退免费通道）：
    google：配 GOOGLE_API_KEY 走 Cloud Translation v2；否则走 clients5 免 key 端点；
    bing：配 BING_API_KEY 走 Azure 官方接口；否则走 Bing 网页免 key 通道；
    baidu：需 BAIDU_APPID / BAIDU_APPKEY（官方翻译开放平台）。
    未显式指定 --provider 时：默认 google → bing → baidu 依次回退；都不行则出错误报告。

错误处理：
    被限流（429 / TooManyRequests）→ 退避等待后重试同一后端（不切后端）；
    连接类错误（超时/DNS/SSL/拒连）→ 立即切换后端；
    业务类错误（内容/参数/空结果等）→ 重试 MAX_ATTEMPTS 次后暂停并出报告。

稳定性优先：
    Google 通道以「稳定」为先 —— 请求保持最小间隔（--min-interval，默认 0.3s，
    远低于 5 请求/秒），被限流时长时间退避（5s 起、上限 120s、最多 10 次），
    宁可慢也不中断。

状态与续跑：
    根目录 .translate_state.json 记录进度；再次运行（参数一致）自动从断点继续，
    参数不一致会报错，需显式 --reset 从头开始。

日志：控制台 + logs/YYYYMMDD_HHMMSS.log
结果：out/<时间戳>.json
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import random
import re
import sys
import time
from datetime import datetime
from html import unescape

import requests
from dotenv import load_dotenv
from tenacity import (Retrying, retry_if_exception_type, stop_after_attempt,
                      wait_exponential, wait_incrementing)
from tqdm import tqdm

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_PATH = os.path.join(ROOT, ".translate_state.json")
LOG_DIR = os.path.join(ROOT, "logs")
OUT_DIR = os.path.join(ROOT, "out")

MAX_ATTEMPTS = 5
TARGET_ZH = "zh-CN"

# Google 免费端点限流（约 5 请求/秒、20 万请求/天）：
# 间隔取实测最优 0.3s（远低于 5 请求/秒），被限流后长时间退避重试
GOOGLE_MIN_INTERVAL = 0.3
RATE_LIMIT_BASE_WAIT = 5.0
RATE_LIMIT_MAX_WAIT = 120.0
RATE_LIMIT_MAX_RETRIES = 10

# 内置固定语言链（各 20 个中间语言；首 en、末 zh 不计入）
CHAINS = {
    "asia": ["ja", "ko", "th", "vi", "id", "ms", "tl", "hi", "bn", "ta",
             "te", "ur", "fa", "ar", "tr", "iw", "my", "km", "lo", "ne"],
    "europe": ["de", "fr", "es", "pt", "it", "nl", "sv", "no", "da", "fi",
               "pl", "cs", "hu", "ro", "bg", "el", "ru", "uk", "cy", "is"],
    "exotic": ["la", "eo", "cy", "ga", "is", "eu", "sq", "mt", "hy", "ka",
               "sw", "tl", "jw", "su", "mi", "haw", "zu", "yo", "ha", "am"],
}
# 随机模式的内置趣味语言池（并集去重）
RANDOM_POOL = sorted({c for chain in CHAINS.values() for c in chain})

LANG_NAMES = {
    "en": "英语", "zh-CN": "简体中文", "zh-TW": "繁体中文", "ja": "日语", "ko": "韩语",
    "th": "泰语", "vi": "越南语", "id": "印尼语", "ms": "马来语", "tl": "菲律宾语",
    "hi": "印地语", "bn": "孟加拉语", "ta": "泰米尔语", "te": "泰卢固语", "ur": "乌尔都语",
    "fa": "波斯语", "ar": "阿拉伯语", "tr": "土耳其语", "iw": "希伯来语", "my": "缅甸语",
    "km": "高棉语", "lo": "老挝语", "ne": "尼泊尔语", "de": "德语", "fr": "法语",
    "es": "西班牙语", "pt": "葡萄牙语", "it": "意大利语", "nl": "荷兰语", "sv": "瑞典语",
    "no": "挪威语", "da": "丹麦语", "fi": "芬兰语", "pl": "波兰语", "cs": "捷克语",
    "hu": "匈牙利语", "ro": "罗马尼亚语", "bg": "保加利亚语", "el": "希腊语", "ru": "俄语",
    "uk": "乌克兰语", "cy": "威尔士语", "is": "冰岛语", "la": "拉丁语", "eo": "世界语",
    "ga": "爱尔兰语", "eu": "巴斯克语", "sq": "阿尔巴尼亚语", "mt": "马耳他语",
    "hy": "亚美尼亚语", "ka": "格鲁吉亚语", "sw": "斯瓦希里语", "jw": "爪哇语",
    "su": "巽他语", "mi": "毛利语", "haw": "夏威夷语", "zu": "祖鲁语", "yo": "约鲁巴语",
    "ha": "豪萨语", "am": "阿姆哈拉语",
}

# 各后端语言代码差异
BING_OVERRIDES = {"iw": "he", "jw": "jv", "tl": "fil", "zh-CN": "zh-Hans",
                  "zh-TW": "zh-Hant", "no": "nb"}
BAIDU_NAMES = {
    "en": "english", "zh-CN": "chinese (simplified)", "zh-TW": "chinese (traditional)",
    "ja": "japanese", "ko": "korean", "ar": "arabic", "bg": "bulgarian", "cs": "czech",
    "da": "danish", "nl": "dutch", "et": "estonian", "fi": "finnish", "fr": "french",
    "de": "german", "el": "greek", "hu": "hungarian", "it": "italian", "pl": "polish",
    "pt": "portuguese", "ro": "romanian", "ru": "russian", "sl": "slovenian",
    "es": "spanish", "sv": "swedish", "th": "thai", "vi": "vietnamese",
}
# langdetect 的检出码 -> google 码
DETECT_TO_GOOGLE = {"zh-cn": "zh-CN", "zh-tw": "zh-TW", "he": "iw", "jv": "jw",
                    "fil": "tl", "nb": "no"}


class ChainError(Exception):
    pass


class ConnectionIssue(ChainError):
    """连接类错误：立即切换后端。"""


class RateLimited(ChainError):
    """被限流（429 / TooManyRequests）：退避等待后重试同一后端，不切换。"""


class BusinessError(ChainError):
    """业务类错误：重试后仍失败则暂停。"""


class UnsupportedLanguage(ChainError):
    """后端不支持该语言：直接终止（重试无意义）。"""


class Paused(ChainError):
    """重试耗尽，暂停并出报告。"""


# --------------------------------------------------------------------------- #
# 语言代码转换
# --------------------------------------------------------------------------- #
def normalize_detected(code: str) -> str:
    c = (code or "").lower()
    return DETECT_TO_GOOGLE.get(c, c)


def detect_language(text: str) -> str:
    """判定源语言（固定 langdetect seed，保证结果可复现）。"""
    from langdetect import DetectorFactory, detect

    DetectorFactory.seed = 0
    return normalize_detected(detect(text))


def to_provider_lang(provider: str, code: str) -> str:
    if provider == "google":
        return code
    if provider == "bing":
        return BING_OVERRIDES.get(code, code)
    if provider == "baidu":
        if code not in BAIDU_NAMES:
            raise UnsupportedLanguage("baidu 不支持语言：%s" % code)
        return BAIDU_NAMES[code]
    raise UnsupportedLanguage("未知后端：%s" % provider)


def lang_label(code: str) -> str:
    return "%s(%s)" % (LANG_NAMES.get(code, "?"), code)


# --------------------------------------------------------------------------- #
# 后端调用
# --------------------------------------------------------------------------- #
def make_translator(provider: str, src: str, tgt: str, cfg: dict):
    from deep_translator import BaiduTranslator, GoogleTranslator, MicrosoftTranslator

    proxies = cfg.get("proxies")
    if provider == "google":
        return GoogleTranslator(source=src, target=tgt, proxies=proxies)
    if provider == "bing":
        return MicrosoftTranslator(source=src, target=tgt, api_key=cfg["bing_api_key"],
                                    region=cfg.get("bing_region") or None, proxies=proxies)
    if provider == "baidu":
        return BaiduTranslator(source=src, target=tgt, appid=cfg["baidu_appid"],
                               appkey=cfg["baidu_appkey"])
    raise UnsupportedLanguage("未知后端：%s" % provider)


def provider_available(provider: str, cfg: dict):
    """返回 (是否可用, 原因)。"""
    if provider == "google":
        return True, ""
    if provider == "bing":
        # 配了 key 走 Azure 官方接口；否则走 cn.bing.com 网页免 key 通道
        return True, ""
    if provider == "baidu":
        if not (cfg.get("baidu_appid") and cfg.get("baidu_appkey")):
            return False, "缺少 BAIDU_APPID / BAIDU_APPKEY"
        return True, ""
    return False, "未知后端"


GOOGLE_DICT_URL = "https://clients5.google.com/translate_a/t"
GOOGLE_API_URL = "https://translation.googleapis.com/language/translate/v2"
GOOGLE_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# 各后端最小请求间隔（秒）：取 max(--min-interval, 该后端下限)
# 百度翻译开放平台标准版 QPS=1，故下限 1.1s
PROVIDER_MIN_INTERVAL = {"google": 0.0, "bing": 0.0, "baidu": 1.1}
_last_req_ts = {}


def _throttle(provider, cfg):
    """按后端保持最小请求间隔，避免触发限流。"""
    interval = max(float(cfg.get("min_interval") or 0),
                   PROVIDER_MIN_INTERVAL.get(provider, 0.0))
    if interval <= 0:
        return
    wait = _last_req_ts.get(provider, 0.0) + interval - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    _last_req_ts[provider] = time.monotonic()


# Bing 网页版翻译（免 key 通道）
# www.bing.com/translator 与 cn.bing.com/translator 端点相同，但会轮流限流（返回空页面），
# 因此多域名轮换尝试；可用环境变量 BING_WEB_BASE 覆盖（逗号分隔）
BING_WEB_BASES = ["https://cn.bing.com", "https://www.bing.com"]
# 注意：Bing 对 UA 校验严格，必须是标准 Chrome 版本号（如 124.0.0.0），否则 401
BING_WEB_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
BING_WEB_TIMEOUT = 40.0
BING_WEB_TOKEN_TTL = 3000.0  # 秒；页面 token 有效期约 1 小时，提前刷新
_bing_web = {"session": None, "base": None, "ig": None, "iid": None,
             "key": None, "token": None, "expire": 0.0}


def _bing_web_bases():
    env = os.getenv("BING_WEB_BASE")
    if env:
        return [b.strip().rstrip("/") for b in env.split(",") if b.strip()]
    return list(BING_WEB_BASES)


def _google_dict_translate(text: str, src: str, tgt: str, proxies) -> str:
    """走 clients5.google.com 的 dict-chrome-ex 端点（免 key，实测比 gtx 端点更少被 429 拦截）。"""
    params = {"client": "dict-chrome-ex", "sl": src, "tl": tgt, "q": text}
    resp = requests.get(GOOGLE_DICT_URL, params=params,
                        headers={"User-Agent": GOOGLE_UA}, timeout=20,
                        proxies=proxies or None)
    if resp.status_code == 429:
        raise RateLimited("google(dict) HTTP 429 被限流")
    resp.raise_for_status()
    data = resp.json()
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
        raise BusinessError("google(dict) 返回空结果")
    return out


def _google_api_translate(text: str, src: str, tgt: str, cfg: dict) -> str:
    """Google Cloud Translation v2（官方 API，用 GOOGLE_API_KEY；稳定、有配额、支持长文本）。"""
    payload = {"q": text, "target": tgt, "format": "text"}
    if src:
        payload["source"] = src
    resp = requests.post(GOOGLE_API_URL, params={"key": cfg["google_api_key"]}, json=payload,
                         timeout=60, proxies=cfg.get("proxies") or None)
    if resp.status_code == 429:
        raise RateLimited("google(api) HTTP 429 配额/限流")
    if resp.status_code in (401, 403):
        raise BusinessError("google(api) 认证失败（检查 GOOGLE_API_KEY）")
    resp.raise_for_status()
    try:
        out = resp.json()["data"]["translations"][0]["translatedText"]
    except (KeyError, IndexError, TypeError):
        raise BusinessError("google(api) 返回结构异常：%s" % resp.text[:120])
    out = unescape(out or "").strip()
    if not out:
        raise BusinessError("google(api) 返回空结果")
    return out


def _bing_web_session():
    if _bing_web["session"] is None:
        s = requests.Session()
        s.headers.update({
            "User-Agent": BING_WEB_UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        })
        _bing_web["session"] = s
    return _bing_web["session"]


def _bing_web_refresh(proxies):
    """在当前 base 上解析 IG / IID / token。"""
    s = _bing_web_session()
    base = _bing_web["base"]
    html = s.get(base + "/translator", timeout=BING_WEB_TIMEOUT, proxies=proxies or None).text
    ig = re.search(r'IG:"([^"]+)"', html)
    iid = re.search(r'data-iid="([^"]+)"', html)
    abuse = re.search(r"params_AbusePreventionHelper\s*=\s*(\[[^\]]*\])", html)
    if not (ig and iid and abuse):
        raise RateLimited("bing(web) 页面未解析到 IG/IID/token（可能被限流）")
    key, token = json.loads(abuse.group(1))[:2]
    _bing_web.update(ig=ig.group(1), iid=iid.group(1), key=key, token=token,
                     expire=time.monotonic() + BING_WEB_TOKEN_TTL)


def _bing_web_post(text, src, tgt, proxies):
    s = _bing_web_session()
    base = _bing_web["base"]
    url = "%s/ttranslatev3?isVertical=1&IG=%s&IID=%s" % (base, _bing_web["ig"], _bing_web["iid"])
    headers = {
        "Referer": base + "/translator",
        "Origin": base,
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Accept": "*/*",
        "X-Requested-With": "XMLHttpRequest",
    }
    data = {"fromLang": src, "text": text, "to": tgt,
            "token": _bing_web["token"], "key": _bing_web["key"]}
    return s.post(url, data=data, headers=headers, timeout=BING_WEB_TIMEOUT, proxies=proxies or None)


def _bing_web_call(text, src, tgt, proxies):
    """在当前域名上执行一次翻译并解析结果。"""
    for attempt in (1, 2):
        resp = _bing_web_post(text, src, tgt, proxies)
        if resp.status_code == 429:
            raise RateLimited("bing(web) HTTP 429 被限流")
        if resp.status_code in (401, 403) and attempt == 1:
            _bing_web_refresh(proxies)  # token 失效 → 刷新后重试一次
            continue
        if resp.status_code != 200:
            raise ConnectionIssue("bing(web) HTTP %d" % resp.status_code)
        if "json" not in (resp.headers.get("Content-Type") or "").lower():
            raise RateLimited("bing(web) 非 JSON 响应（可能被限流）：%r" % resp.text[:60])
        payload = resp.json()
        try:
            out = payload[0]["translations"][0]["text"]
        except (KeyError, IndexError, TypeError):
            raise BusinessError("bing(web) 返回结构异常：%s" % str(payload)[:120])
        out = (out or "").strip()
        if not out:
            raise BusinessError("bing(web) 返回空结果")
        return out
    raise RateLimited("bing(web) token 刷新后仍失败")


def _bing_web_translate(text: str, src_code: str, tgt_code: str, proxies) -> str:
    """免 key 走 Bing 网页端点。多域名轮换；token 失效自动刷新。

    被限流时该域名会返回空页面，此处换域名重试；全部域名失败则抛 RateLimited。
    """
    src = to_provider_lang("bing", src_code)
    tgt = to_provider_lang("bing", tgt_code)
    bases = _bing_web_bases()
    if _bing_web["base"] in bases:  # 优先上次成功的域名
        bases = [_bing_web["base"]] + [b for b in bases if b != _bing_web["base"]]

    last = None
    soft = False  # 出现过限流/业务类错误（换域名重试/退避后大概率可恢复）
    for base in bases:
        need_token = (_bing_web["base"] != base or not _bing_web["token"]
                      or time.monotonic() > _bing_web["expire"])
        if need_token:
            _bing_web["base"] = base
            _bing_web["token"] = None
            try:
                _bing_web_refresh(proxies)
            except ChainError as exc:
                last = exc
                soft = soft or not isinstance(exc, ConnectionIssue)
                continue
        try:
            return _bing_web_call(text, src, tgt, proxies)
        except ChainError as exc:
            last = exc
            soft = soft or not isinstance(exc, ConnectionIssue)
    if last is None:
        raise ConnectionIssue("bing(web) 无可用域名")
    if soft:
        raise RateLimited("bing(web) 所有域名均失败：%s" % last)
    raise last


def google_tts_bytes(text: str, lang: str, proxies) -> bytes:
    """用 gTTS（MIT，Google 翻译 TTS 的官方封装）生成 mp3 字节。

    - 长文本由 gTTS 自动分词、分请求后拼接，无需自行分段
    - proxies 通过环境变量临时注入（gTTS 不直接接收 proxies）
    """
    from io import BytesIO

    from gtts import gTTS

    text = (text or "").strip()
    if not text:
        raise BusinessError("TTS 无有效文本")
    saved = {k: os.environ.get(k) for k in ("HTTP_PROXY", "HTTPS_PROXY")}
    try:
        if proxies:
            url = proxies.get("https") or proxies.get("http")
            if url:
                os.environ["HTTP_PROXY"] = os.environ["HTTPS_PROXY"] = url
        buf = BytesIO()
        gTTS(text=text, lang=lang, slow=False).write_to_fp(buf)
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    data = buf.getvalue()
    if not data:
        raise BusinessError("TTS 返回空音频")
    return data


def _classify_and_raise(exc):
    if isinstance(exc, UnsupportedLanguage):
        raise exc
    if isinstance(exc, (RateLimited, ConnectionIssue, BusinessError)):
        raise exc
    if isinstance(exc, requests.exceptions.RequestException):
        raise ConnectionIssue("%s: %s" % (type(exc).__name__, exc))
    name = type(exc).__name__
    msg = str(exc)
    # 限流 / 拦截（429、Sorry 页、TooManyRequests）→ 退避重试同一后端
    if name == "TooManyRequests" or "429" in msg or "too many requests" in msg.lower():
        raise RateLimited("%s: %s" % (name, msg))
    # 百度：Invalid Access Limit / 54003 = QPS 超限 → 同样按限流退避重试
    if "access limit" in msg.lower() or "54003" in msg:
        raise RateLimited("%s: %s" % (name, msg))
    if name in ("TranslationNotFound", "NotValidPayload", "NotValidLength",
                "ServerException", "RequestError", "BaseError"):
        raise BusinessError("%s: %s" % (name, msg))
    # 无法归类时按业务错误处理
    raise BusinessError("%s: %s" % (name, msg))


def translate_once(provider: str, text: str, src_code: str, tgt_code: str, cfg: dict) -> str:
    def _run(fn):
        try:
            out = fn()
        except UnsupportedLanguage:
            raise
        except ChainError:
            raise
        except Exception as exc:  # noqa: BLE001
            _classify_and_raise(exc)
        if not out or not str(out).strip():
            raise BusinessError("返回空结果")
        return str(out)

    if provider == "google":
        if cfg.get("google_api_key"):
            return _run(lambda: _google_api_translate(text, src_code, tgt_code, cfg))
        try:
            _throttle("google", cfg)
            return _run(lambda: _google_dict_translate(text, src_code, tgt_code, cfg.get("proxies")))
        except (ConnectionIssue, BusinessError):
            from deep_translator import GoogleTranslator
            _throttle("google", cfg)
            return _run(lambda: GoogleTranslator(source=src_code, target=tgt_code,
                                                 proxies=cfg.get("proxies")).translate(text))
    if provider == "bing":
        if cfg.get("bing_api_key"):
            src = to_provider_lang("bing", src_code)
            tgt = to_provider_lang("bing", tgt_code)
            return _run(lambda: make_translator("bing", src, tgt, cfg).translate(text))
        _throttle("bing", cfg)
        return _run(lambda: _bing_web_translate(text, src_code, tgt_code, cfg.get("proxies")))
    if provider == "baidu":
        _throttle("baidu", cfg)  # 百度标准版 QPS=1，必须限速
    src = to_provider_lang(provider, src_code)
    tgt = to_provider_lang(provider, tgt_code)
    return _run(lambda: make_translator(provider, src, tgt, cfg).translate(text))


# --------------------------------------------------------------------------- #
# 带重试 / 后端切换的翻译
# --------------------------------------------------------------------------- #
def translate_step(text: str, src_code: str, tgt_code: str, providers, cfg, log):
    """返回 (provider, 结果文本)。

    - 被限流（429）：退避等待后重试**同一后端**（不切后端）
    - 连接类错误（超时/DNS/SSL/拒连）：立即切换后端
    - 业务类错误（内容/参数/空结果）：重试 MAX_ATTEMPTS 次后暂停
    """

    def _retryer(provider, label, exc_type, wait, stop, max_times):
        """构造 tenacity 重试器（退避/次数/日志交给 tenacity）。"""
        def _before_sleep(retry_state):
            sleep = retry_state.next_action.sleep if retry_state.next_action else 0
            log.warning("  [%s] %s（第 %d/%d 次），%.0fs 后重试：%s",
                        provider, label, retry_state.attempt_number, max_times,
                        sleep, retry_state.outcome.exception())
        return Retrying(retry=retry_if_exception_type(exc_type), wait=wait, stop=stop,
                        before_sleep=_before_sleep, reraise=True)

    conn_failed = []
    for provider in providers:
        ok, reason = provider_available(provider, cfg)
        if not ok:
            log.warning("  后端 %s 不可用（%s），跳过", provider, reason)
            conn_failed.append("%s 不可用：%s" % (provider, reason))
            continue
        # 同一后端：业务错误重试 MAX_ATTEMPTS 次；被限流则退避重试（都不切后端）
        business = _retryer(provider, "业务错误", BusinessError,
                            wait_incrementing(start=2, increment=2, max=8),
                            stop_after_attempt(MAX_ATTEMPTS), MAX_ATTEMPTS - 1)
        rate_limit = _retryer(provider, "被限流", RateLimited,
                              wait_exponential(multiplier=RATE_LIMIT_BASE_WAIT,
                                               max=RATE_LIMIT_MAX_WAIT),
                              stop_after_attempt(RATE_LIMIT_MAX_RETRIES + 1),
                              RATE_LIMIT_MAX_RETRIES)
        out = None
        try:
            for attempt in rate_limit:
                with attempt:
                    for inner in business:
                        with inner:
                            out = translate_once(provider, text, src_code, tgt_code, cfg)
        except BusinessError as exc:
            raise Paused("后端 %s 业务错误，重试 %d 次仍失败：%s"
                         % (provider, MAX_ATTEMPTS - 1, exc))
        except RateLimited as exc:
            conn_failed.append("%s 被限流，重试 %d 次仍失败：%s"
                               % (provider, RATE_LIMIT_MAX_RETRIES, exc))
            continue
        except ConnectionIssue as exc:
            log.warning("  [%s] 连接失败：%s → 切换后端", provider, exc)
            conn_failed.append("%s 连接失败：%s" % (provider, exc))
            continue
        log.info("  [%s] %s → %s：%s", provider, lang_label(src_code),
                 lang_label(tgt_code), out)
        return provider, out
    raise ConnectionIssue("所有后端均不可用：\n    - " + "\n    - ".join(conn_failed))


# --------------------------------------------------------------------------- #
# 路线与任务
# --------------------------------------------------------------------------- #
def resolve_languages(args) -> tuple[list[str], str]:
    if args.mode == "fixed":
        return list(CHAINS[args.chain]), args.chain
    if args.mode == "random":
        steps = args.steps
        if steps > len(RANDOM_POOL):
            raise SystemExit("随机语言池只有 %d 个，无法抽取 %d 个" % (len(RANDOM_POOL), steps))
        return random.sample(RANDOM_POOL, steps), "random"
    # custom
    langs = [x.strip() for x in args.languages.split(",") if x.strip()]
    if not langs:
        raise SystemExit("--mode custom 需要用 --languages 指定语言序列（逗号分隔）")
    return langs, "custom"


def build_tasks(detected: str, languages: list[str]):
    tasks = []
    if detected != "en":
        tasks.append({"kind": "to_en", "lang": "en", "src": detected, "tgt": "en"})
    cur = "en"
    for i, lang in enumerate(languages):
        tasks.append({"kind": "step", "index": i, "lang": lang, "src": cur, "tgt": lang})
        cur = lang
    tasks.append({"kind": "to_zh", "lang": TARGET_ZH, "src": cur, "tgt": TARGET_ZH})
    return tasks


def signature(args, text, detected, languages, providers):
    return {
        "text": text,
        "detected": detected,
        "mode": args.mode,
        "chain": args.chain if args.mode == "fixed" else None,
        "languages": languages,
        "providers": list(providers),
    }


# --------------------------------------------------------------------------- #
# 状态
# --------------------------------------------------------------------------- #
def load_state():
    if not os.path.exists(STATE_PATH):
        return None
    with open(STATE_PATH, encoding="utf-8") as f:
        return json.load(f)


def save_state(state: dict):
    state["updated_at"] = datetime.now().isoformat(timespec="seconds")
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def clear_state():
    if os.path.exists(STATE_PATH):
        os.remove(STATE_PATH)


# --------------------------------------------------------------------------- #
# 日志
# --------------------------------------------------------------------------- #
def setup_logging(no_file_log: bool):
    log = logging.getLogger("translate_chain")
    log.setLevel(logging.DEBUG)
    log.handlers.clear()
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(console)

    logfile = None
    if not no_file_log:
        os.makedirs(LOG_DIR, exist_ok=True)
        logfile = os.path.join(LOG_DIR, datetime.now().strftime("%Y%m%d_%H%M%S") + ".log")
        fh = logging.FileHandler(logfile, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(fh)
    return log, logfile


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def parse_args(argv=None):
    p = argparse.ArgumentParser(description="多后端来回翻译 N 次链路工具")
    p.add_argument("text", nargs="?", default="Hello, World", help="待翻译文本（默认 Hello, World）")
    p.add_argument("--mode", choices=["fixed", "random", "custom"], default="random",
                   help="路线模式（默认 random）")
    p.add_argument("--chain", choices=list(CHAINS), default="asia",
                   help="fixed 模式使用哪套链（默认 asia）")
    p.add_argument("--languages", default="", help="custom 模式的语言序列，逗号分隔")
    p.add_argument("--provider", choices=["google", "bing", "baidu"], default=None,
                   help="指定后端；不指定则 google → bing 自动回退")
    p.add_argument("--steps", type=int, default=20, help="random 模式抽取语言数（默认 20）")
    p.add_argument("--proxy", default="", help="代理地址（覆盖 .env 的 HTTPS_PROXY/HTTP_PROXY）")
    p.add_argument("--min-interval", type=float, default=GOOGLE_MIN_INTERVAL,
                   help="Google 请求最小间隔秒数，避免触发限流（默认 %.1f，0 关闭）" % GOOGLE_MIN_INTERVAL)
    p.add_argument("--reset", action="store_true", help="忽略旧状态，从头开始")
    p.add_argument("--dry-run", action="store_true", help="只显示路线，不翻译")
    p.add_argument("--no-file-log", action="store_true", help="不写文件日志")
    p.add_argument("--no-tts", action="store_true", help="不生成最终译文的语音")
    return p.parse_args(argv)


def build_cfg(args):
    load_dotenv(os.path.join(ROOT, ".env"))
    proxy = args.proxy or os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY") or ""
    proxies = {"http": proxy, "https": proxy} if proxy else None
    return {
        "proxies": proxies,
        "min_interval": getattr(args, "min_interval", GOOGLE_MIN_INTERVAL),
        "google_api_key": os.getenv("GOOGLE_API_KEY", ""),
        "bing_api_key": os.getenv("BING_API_KEY", ""),
        "bing_region": os.getenv("BING_REGION", ""),
        "baidu_appid": os.getenv("BAIDU_APPID", ""),
        "baidu_appkey": os.getenv("BAIDU_APPKEY", ""),
    }


def main(argv=None) -> int:
    args = parse_args(argv)
    log, logfile = setup_logging(args.no_file_log)
    # 控制台只保留告警，避免与 tqdm 进度条混排（明细仍进文件日志）
    for h in log.handlers:
        if type(h) is logging.StreamHandler:
            h.setLevel(logging.WARNING)
    cfg = build_cfg(args)

    text = args.text
    detected = detect_language(text)
    languages, chain_label = resolve_languages(args)

    providers = [args.provider] if args.provider else ["google", "bing", "baidu"]

    print("原文：%s" % text)
    print("检出语言：%s" % lang_label(detected))
    print("路线：%s（%d 次）" % (chain_label, len(languages)))
    print("路线语言：%s" % " → ".join(languages))
    print("后端：%s" % " → ".join(providers))
    print("目标语言：%s" % lang_label(TARGET_ZH))

    if args.dry_run:
        print("--dry-run：仅展示路线，未执行翻译。")
        return 0

    tasks = build_tasks(detected, languages)
    sig = signature(args, text, detected, languages, providers)

    # ---- 状态 / 续跑 ----
    steps = []
    next_index = 0
    cur_text = text
    started_at = datetime.now().isoformat(timespec="seconds")

    old = load_state()
    if old and not args.reset:
        if old.get("signature") != sig:
            log.error("检测到未完成的进度，但参数与本次不一致。")
            log.error("  上次：%s", json.dumps(old.get("signature"), ensure_ascii=False))
            log.error("  本次：%s", json.dumps(sig, ensure_ascii=False))
            log.error("如确认从头开始，请加 --reset。")
            return 3
        steps = old.get("steps", [])
        next_index = old.get("next_index", 0)
        cur_text = old.get("text", text)
        started_at = old.get("started_at", started_at)
        log.info("从断点续跑：已完成 %d/%d 步。", next_index, len(tasks))
        print("从断点续跑：已完成 %d/%d 步。" % (next_index, len(tasks)))

    state = {
        "signature": sig,
        "source": text,
        "detected": detected,
        "languages": languages,
        "tasks": tasks,
        "text": cur_text,
        "next_index": next_index,
        "steps": steps,
        "started_at": started_at,
    }

    try:
        with tqdm(total=len(tasks), initial=next_index, desc="翻译链", unit="步",
                  dynamic_ncols=True) as bar:
            for idx in range(next_index, len(tasks)):
                task = tasks[idx]
                bar.set_description("翻译链 %s→%s" % (task["src"], task["tgt"]))
                log.info("[%d/%d] %s → %s", idx + 1, len(tasks),
                         lang_label(task["src"]), lang_label(task["tgt"]))
                provider, out = translate_step(cur_text, task["src"], task["tgt"], providers, cfg, log)
                cur_text = out
                steps.append({"index": idx, "kind": task["kind"], "lang": task["lang"],
                              "provider": provider, "text": out})
                state.update({"text": cur_text, "next_index": idx + 1, "steps": steps})
                save_state(state)
                bar.update(1)
    except UnsupportedLanguage as exc:
        log.error("语言不受支持：%s", exc)
        log.error("请检查路线语言或 --provider（参数已保留，可修改后重跑）。")
        return 2
    except Paused as exc:
        save_state(state)
        log.error("已暂停：%s", exc)
        log.error("进度已保存（.translate_state.json）。排查/修复后重跑即可从断点继续。")
        return 4
    except ConnectionIssue as exc:
        save_state(state)
        log.error("所有后端均连接失败：\n%s", exc)
        log.error("请检查网络/代理（可在 .env 配置 HTTPS_PROXY 或加 --proxy）后重跑。")
        return 5

    final_text = cur_text
    finished_at = datetime.now().isoformat(timespec="seconds")

    result = {
        "source": text,
        "source_detected_lang": detected,
        "target_lang": TARGET_ZH,
        "provider": providers[0] if len(providers) == 1 else providers,
        "mode": args.mode,
        "chain": chain_label,
        "languages": languages,
        "steps": steps,
        "final": final_text,
        "started_at": started_at,
        "finished_at": finished_at,
    }
    os.makedirs(OUT_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(OUT_DIR, stamp + ".json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print("")
    print("最终译文（%s）：%s" % (lang_label(TARGET_ZH), final_text))
    print("结果已保存：%s" % os.path.relpath(out_path, ROOT).replace(os.sep, "/"))

    if not args.no_tts:
        try:
            audio = google_tts_bytes(final_text, TARGET_ZH, cfg.get("proxies"))
            mp3_path = os.path.join(OUT_DIR, stamp + ".mp3")
            with open(mp3_path, "wb") as f:
                f.write(audio)
            print("语音已保存：%s（%d 字节）"
                  % (os.path.relpath(mp3_path, ROOT).replace(os.sep, "/"), len(audio)))
        except Exception as exc:  # noqa: BLE001
            log.warning("语音生成失败（不影响翻译结果）：%s", exc)

    if logfile:
        print("日志文件：%s" % os.path.relpath(logfile, ROOT).replace(os.sep, "/"))

    clear_state()
    return 0


if __name__ == "__main__":
    sys.exit(main())
