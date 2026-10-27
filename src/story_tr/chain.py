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

import itertools
import json
import logging
import os
import random
import re
import threading
import time
from datetime import datetime
from html import unescape

import requests
from dotenv import load_dotenv
from tenacity import (
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
    wait_incrementing,
)
from tqdm import tqdm

from . import paths, report, statefile
from . import rwkv as local_rwkv
from .exitcodes import ExitCode

ROOT = paths.home()
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
# 连接类错误（超时/DNS/SSL/拒连）：先短退避重试同一后端，仍失败再切换
CONNECTION_RETRIES = 3
CONNECTION_RETRY_BASE = 10.0

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


class QuotaExceeded(ChainError):
    """当日配额耗尽：重试无意义，保存进度后由上层以退出码 8 结束。"""


class BusinessError(ChainError):
    """业务类错误：重试后仍失败则暂停。"""


class UnsupportedLanguage(ChainError):
    """后端不支持该语言：直接终止（重试无意义）。"""


class Paused(ChainError):
    """重试耗尽，暂停并出报告。"""


class ModelNotLoaded(Paused):
    """本地模型未加载（服务可达）；区别于普通业务失败，批量应立即中止。"""


class Cancelled(ChainError):
    """被调用方主动取消（GUI 停止按钮 / 分支任务中止）：保存进度后退出。"""


# --------------------------------------------------------------------------- #
# 语言代码转换
# --------------------------------------------------------------------------- #
def normalize_detected(code: str) -> str:
    c = (code or "").lower()
    return DETECT_TO_GOOGLE.get(c, c)


_detect_lock = threading.Lock()
_detect_ready = False

# 「文字」= Unicode 字母（含汉字）。langdetect 的特征只来自字母，
# 纯标点（如 `……`）/ 纯数字 / 空白的文本没有任何特征可判。
_LETTER_RE = re.compile(r"[^\W\d_]", re.UNICODE)
# 文字脚本：来源文本以中文为主，先按脚本判定，避免 langdetect 把短中文误判成韩/日。
_KANA_RE = re.compile(r"[\u3040-\u30ff\u31f0-\u31ff]")
_HANGUL_RE = re.compile(r"[\u1100-\u11ff\u3130-\u318f\uac00-\ud7af]")
_HAN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


def has_features(text: str) -> bool:
    """文本是否含「文字」（字母 / 汉字等）—— langdetect 判语言所需的最小特征。"""
    return bool(_LETTER_RE.search(text or ""))


def has_han(text: str) -> bool:
    """文本是否含汉字（用于 `translate` 的源语言提示）。"""
    return bool(_HAN_RE.search(text or ""))


def detect_language(text: str) -> str:
    """判定源语言（固定 langdetect seed，保证结果可复现）。

    langdetect 的 `init_factory()` 非线程安全（profile 未加载完就被并发调用会抛
    `Need to load profiles`），故首次调用在锁内预热一次，之后即可并发使用。

    无语言特征的文本（纯标点 / 数字 / 空白，如角色故事里的 `……`）直接按 `en` 返回：
    既然无从判定，也就不需要「先译成英文」这一步，更不该让整批任务因此中断。

    先按 Unicode 脚本判定：假名 → 日语、谚文 → 韩语、汉字 → 简体中文；
    其余（拉丁字母等）再交给 langdetect。这样短中文不会被误判为韩/日。
    """
    global _detect_ready
    text = text or ""
    if not has_features(text):
        return "en"
    if _KANA_RE.search(text):
        return "ja"
    if _HANGUL_RE.search(text):
        return "ko"
    if _HAN_RE.search(text):
        return "zh-CN"

    from langdetect import DetectorFactory, LangDetectException, detect

    if not _detect_ready:
        with _detect_lock:
            if not _detect_ready:
                DetectorFactory.seed = 0
                detect("warmup")  # 触发 profile 加载完成
                _detect_ready = True
    try:
        return normalize_detected(detect(text))
    except LangDetectException:  # 极短文本等偶发判定失败，同样退回 en
        return "en"


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
    if provider == "rwkv":
        # 本地服务是否可达由实际请求判定（不在选后端阶段额外探测）
        return True, ""
    return False, "未知后端"


GOOGLE_DICT_URL = "https://clients5.google.com/translate_a/t"
GOOGLE_API_URL = "https://translation.googleapis.com/language/translate/v2"
GOOGLE_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# 免 key 端点每次新建 TLS 连接，经代理时握手成本很高；线程内复用 Session
_google_local = threading.local()


def _google_session():
    """每个线程复用一个 requests.Session（复用连接 / TLS 会话）。"""
    session = getattr(_google_local, "session", None)
    if session is None:
        session = requests.Session()
        session.headers.update({"User-Agent": GOOGLE_UA})
        _google_local.session = session
    return session


def _google_get(params, timeout, proxies):
    """Google 免 key 端点的 GET（线程内连接复用，便于测试替换）。"""
    return _google_session().get(GOOGLE_DICT_URL, params=params, timeout=timeout,
                                 proxies=proxies or None)

# 单次请求的文本上限：Google 免费 GET 端点约 1800 字（中文 URL 编码后约 9 字节/字）即 400，故留余量
MAX_CHARS_PER_REQUEST = 1500

# 各后端最小请求间隔（秒）：取 max(--min-interval, 该后端下限)
# 百度翻译开放平台标准版 QPS=1，故下限 1.1s
PROVIDER_MIN_INTERVAL = {"google": 0.0, "bing": 0.0, "baidu": 1.1}
_last_req_ts = {}
_throttle_lock = threading.Lock()
# gTTS 读取进程级代理环境变量；多线程并发时必须串行，避免互相覆盖/恢复错值
_tts_lock = threading.Lock()


def _throttle(provider, cfg):
    """按后端保持最小请求间隔（线程安全：持锁等待即形成全局速率限制）。"""
    interval = max(float(cfg.get("min_interval") or 0),
                   PROVIDER_MIN_INTERVAL.get(provider, 0.0))
    if interval <= 0:
        return
    with _throttle_lock:
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
    resp = _google_get(params, timeout=20, proxies=proxies)
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


def _google_dict_translate_batch(texts, src: str, tgt: str, proxies):
    """一次请求翻译多条文本（clients5 支持重复 `q`，响应与 `q` 一一对应）。

    实测：2 条 `q` → 2 个元素（同一 `q` 内的多句会被合并成一个字符串）。
    形状不符时抛 BusinessError，由调用方退化为逐条。
    """
    qs = [("client", "dict-chrome-ex"), ("sl", src), ("tl", tgt)] + [("q", t) for t in texts]
    resp = _google_get(qs, timeout=30, proxies=proxies)
    if resp.status_code == 429:
        raise RateLimited("google(dict) HTTP 429 被限流")
    resp.raise_for_status()
    data = resp.json()
    if (isinstance(data, list) and len(data) == len(texts)
            and all(isinstance(x, str) and x.strip() for x in data)):
        return [x.strip() for x in data]
    raise BusinessError("google(dict) 批量返回形状不符（期望 %d 条）" % len(texts))


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
    - gTTS 内部读取进程级环境变量，因此这里用锁串行化，避免多线程互相覆盖
    """
    from io import BytesIO

    from gtts import gTTS

    text = (text or "").strip()
    if not text:
        raise BusinessError("TTS 无有效文本")
    with _tts_lock:
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
    if isinstance(exc, (UnsupportedLanguage, QuotaExceeded)):
        raise exc
    if isinstance(exc, (RateLimited, ConnectionIssue, BusinessError)):
        raise exc
    if isinstance(exc, requests.exceptions.RequestException):
        raise ConnectionIssue("%s: %s" % (type(exc).__name__, exc))
    name = type(exc).__name__
    msg = str(exc)
    low = msg.lower()
    # 每日配额耗尽：重试没有意义，交给上层保存进度并以退出码 8 结束
    if "200k requests per day" in low or "daily limit" in low or "daily quota" in low:
        raise QuotaExceeded("%s: %s" % (name, msg))
    # 限流 / 拦截（429、Sorry 页、TooManyRequests）→ 退避重试同一后端
    if name == "TooManyRequests" or "429" in msg or "too many requests" in low:
        raise RateLimited("%s: %s" % (name, msg))
    # 百度：Invalid Access Limit / 54003 = QPS 超限 → 同样按限流退避重试
    if "access limit" in msg.lower() or "54003" in msg:
        raise RateLimited("%s: %s" % (name, msg))
    if name in ("TranslationNotFound", "NotValidPayload", "NotValidLength",
                "ServerException", "RequestError", "BaseError"):
        raise BusinessError("%s: %s" % (name, msg))
    # 无法归类时按业务错误处理
    raise BusinessError("%s: %s" % (name, msg))


def _split_text(text: str, limit: int = MAX_CHARS_PER_REQUEST):
    """把超长文本切成 ≤ limit 的片段（优先按句末标点，必要时硬切）。"""
    if not text or len(text) <= limit:
        return [text] if text else []
    chunks, cur = [], ""
    for piece in re.split(r"(?<=[。！？；!?;.\n])", text):
        if not piece:
            continue
        if cur and len(cur) + len(piece) > limit:
            chunks.append(cur)
            cur = ""
        while len(piece) > limit:
            chunks.append(piece[:limit])
            piece = piece[limit:]
        cur += piece
    if cur:
        chunks.append(cur)
    return chunks


def translate_once(provider: str, text: str, src_code: str, tgt_code: str, cfg: dict) -> str:
    """翻译一段文本；超长时按块翻译后拼接（免费端点对超长文本会报 400）。"""
    if provider == "rwkv":
        # 本地后端自带分块（chunk_chars）；此处再切块会把「一次成型」拆成多次生成，
        # 也会让每份文本对应的本地元数据对不上。
        return _translate_once_raw(provider, text, src_code, tgt_code, cfg)
    chunks = _split_text(text)
    if len(chunks) <= 1:
        return _translate_once_raw(provider, text, src_code, tgt_code, cfg)
    return "".join(_translate_once_raw(provider, c, src_code, tgt_code, cfg) for c in chunks)


def _pack_chunks(items, limit: int = MAX_CHARS_PER_REQUEST):
    """把 [(文本下标, 块)] 按累计长度打包成若干组（每组一次请求）。"""
    groups, cur, size = [], [], 0
    for it in items:
        n = len(it[1])
        if cur and size + n > limit:
            groups.append(cur)
            cur, size = [], 0
        cur.append(it)
        size += n
    if cur:
        groups.append(cur)
    return groups


def translate_many(provider: str, texts, src_code: str, tgt_code: str, cfg: dict):
    """批量翻译多条文本，返回与 texts 同序的结果。

    仅 google 的免 key 通道（clients5）支持合并请求；其它情况退化为逐条调用。
    每条文本仍按 MAX_CHARS_PER_REQUEST 切块，再按累计长度分组，每组一次请求。
    """
    texts = list(texts)
    if len(texts) <= 1 or provider != "google" or cfg.get("google_api_key"):
        return [translate_once(provider, t, src_code, tgt_code, cfg) for t in texts]

    flat = [(i, chunk) for i, t in enumerate(texts) for chunk in _split_text(t)]
    got = {}
    for group in _pack_chunks(flat):
        qs = [c for _, c in group]
        if len(qs) == 1:
            res = [translate_once("google", qs[0], src_code, tgt_code, cfg)]
        else:
            try:
                _throttle("google", cfg)
                res = _google_dict_translate_batch(qs, src_code, tgt_code, cfg.get("proxies"))
            except BusinessError:
                # 形状异常等业务错误：批量通道不可用，退化为逐条
                res = [translate_once("google", q, src_code, tgt_code, cfg) for q in qs]
            except ChainError:
                raise
            except Exception as exc:  # noqa: BLE001
                # 原始 requests 异常（超时/DNS/SSL 等）归一化为 ConnectionIssue，交上层切后端
                _classify_and_raise(exc)
        for (i, _), out in zip(group, res):
            got.setdefault(i, []).append(out)
    return ["".join(got.get(i, [])) for i in range(len(texts))]


# 本地后端元数据（线程内累计）：供 run_chain_many / chain.main 写进 step 与结果
_local_meta = threading.local()


def _reset_local_meta():
    """清空当前线程的本地生成元数据（每次步骤开始前调用）。"""
    _local_meta.metas = []


def local_metas():
    """当前线程自上次重置以来逐条文本的本地生成元数据（按调用顺序）。"""
    return list(getattr(_local_meta, "metas", []))


def local_meta():
    """当前线程自上次重置以来累计的本地生成元数据（非本地后端时为 None）。"""
    return merge_local_meta(local_metas())


def _merge_local_meta(cfg_local, res):
    """把一次本地生成的结果追加到当前线程的元数据（每条文本一条）。"""
    sig = local_rwkv.signature(cfg_local)
    _local_meta.metas = local_metas() + [{
        "url": sig["url"], "model": sig["model"],
        "template_sha1": sig["template_sha1"],
        "chunks": int(res.get("chunks") or 0),
        "prompt_tokens": int(res.get("prompt_tokens") or 0),
        "completion_tokens": int(res.get("completion_tokens") or 0)}]


def merge_local_meta(metas):
    """把多条 step 的 `rwkv` 元数据合成一份（静态字段取第一条，三个计数求和）。"""
    items = [m for m in (metas or ()) if isinstance(m, dict)]
    if not items:
        return None
    merged = {"url": items[0].get("url"), "model": items[0].get("model"),
              "template_sha1": items[0].get("template_sha1"),
              "chunks": 0, "prompt_tokens": 0, "completion_tokens": 0}
    for item in items:
        merged["chunks"] += int(item.get("chunks") or 0)
        merged["prompt_tokens"] += int(item.get("prompt_tokens") or 0)
        merged["completion_tokens"] += int(item.get("completion_tokens") or 0)
    return merged


def _translate_once_raw(provider: str, text: str, src_code: str, tgt_code: str, cfg: dict) -> str:
    if provider == "rwkv":
        cfg_local = (cfg or {}).get("rwkv") or local_rwkv.from_env()
        try:
            res = local_rwkv.translate(text, cfg_local)
        except local_rwkv.RwkvUnavailable as exc:
            raise ConnectionIssue(str(exc))
        except local_rwkv.RwkvModelOffline as exc:
            raise ModelNotLoaded(str(exc))
        except local_rwkv.RwkvBadOutput as exc:
            raise BusinessError(str(exc))
        except local_rwkv.RwkvError as exc:
            raise BusinessError(str(exc))
        _merge_local_meta(cfg_local, res)
        return res["text"]

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
_balance_cursor = itertools.count()
_balance_lock = threading.Lock()


def order_providers(providers, balance: bool):
    """balance=True 时轮转后端顺序，让多个后端分摊请求（默认保持原顺序 = 主后端优先）。"""
    providers = list(providers)
    if not balance or len(providers) < 2:
        return providers
    with _balance_lock:
        i = next(_balance_cursor) % len(providers)
    return providers[i:] + providers[:i]


def _step_loop(provider, call, cfg, log):
    """在「限流退避 + 业务重试」包裹下执行 call()。

    - 被限流（429）：退避等待后重试**同一后端**（不切后端），耗尽则抛 RateLimited
    - 业务类错误（内容/参数/空结果）：重试 MAX_ATTEMPTS 次后抛 Paused
    - 连接类错误（超时/DNS/SSL/拒连）：抛 ConnectionIssue，由调用方切后端
    - 本地后端（rwkv）：不做云端式长退避 —— 连接失败立即抛，业务错误单次转 Paused
      （`rwkv.translate` 已按 `RWKV_RETRIES` 重试过空输出）
    """
    if provider == "rwkv":
        try:
            return call()
        except BusinessError as exc:
            raise Paused("本地 RWKV 业务失败（已按 RWKV_RETRIES 重试）：%s" % exc)
        except ChainError:
            raise
        except Exception as exc:  # noqa: BLE001
            _classify_and_raise(exc)

    def _retryer(label, exc_type, wait, stop, max_times):
        """构造 tenacity 重试器（退避/次数/日志交给 tenacity）。"""
        def _before_sleep(retry_state):
            sleep = retry_state.next_action.sleep if retry_state.next_action else 0
            log.warning("  [%s] %s（第 %d/%d 次），%.0fs 后重试：%s",
                        provider, label, retry_state.attempt_number, max_times,
                        sleep, retry_state.outcome.exception())
        return Retrying(retry=retry_if_exception_type(exc_type), wait=wait, stop=stop,
                        before_sleep=_before_sleep, reraise=True)

    business = _retryer("业务错误", BusinessError,
                        wait_incrementing(start=2, increment=2, max=8),
                        stop_after_attempt(MAX_ATTEMPTS), MAX_ATTEMPTS - 1)
    rate_limit = _retryer("被限流", RateLimited,
                          wait_exponential(multiplier=RATE_LIMIT_BASE_WAIT,
                                           max=RATE_LIMIT_MAX_WAIT),
                          stop_after_attempt(RATE_LIMIT_MAX_RETRIES + 1),
                          RATE_LIMIT_MAX_RETRIES)
    connection = _retryer("连接错误", ConnectionIssue,
                          wait_incrementing(start=CONNECTION_RETRY_BASE,
                                            increment=CONNECTION_RETRY_BASE, max=40),
                          stop_after_attempt(CONNECTION_RETRIES), CONNECTION_RETRIES - 1)
    try:
        for attempt in rate_limit:
            with attempt:
                for inner in business:
                    with inner:
                        for conn in connection:
                            with conn:
                                return call()
    except BusinessError as exc:
        raise Paused("后端 %s 业务错误，重试 %d 次仍失败：%s"
                     % (provider, MAX_ATTEMPTS - 1, exc))
    return None


def _translate_with_providers(providers, cfg, log, call):
    """按后端顺序执行 call(provider)，返回 (provider, 结果)；错误处理规则见 `_step_loop`。"""
    conn_failed = []
    for provider in order_providers(providers, cfg.get("balance")):
        ok, reason = provider_available(provider, cfg)
        if not ok:
            log.warning("  后端 %s 不可用（%s），跳过", provider, reason)
            conn_failed.append("%s 不可用：%s" % (provider, reason))
            continue
        try:
            out = _step_loop(provider, lambda p=provider: call(p), cfg, log)
        except RateLimited as exc:
            conn_failed.append("%s 被限流，重试 %d 次仍失败：%s"
                               % (provider, RATE_LIMIT_MAX_RETRIES, exc))
            continue
        except ConnectionIssue as exc:
            log.warning("  [%s] 连接失败：%s → 切换后端", provider, exc)
            conn_failed.append("%s 连接失败：%s" % (provider, exc))
            continue
        return provider, out
    raise ConnectionIssue("所有后端均不可用：\n    - " + "\n    - ".join(conn_failed))


def translate_step(text: str, src_code: str, tgt_code: str, providers, cfg, log):
    """返回 (provider, 结果文本)。规则见 `_step_loop`。"""
    _reset_local_meta()
    provider, out = _translate_with_providers(
        providers, cfg, log, lambda p: translate_once(p, text, src_code, tgt_code, cfg))
    log.log(report.STEP, "  [%s] %s → %s：%s", provider, lang_label(src_code),
            lang_label(tgt_code), out)
    return provider, out


def translate_step_many(texts, src_code: str, tgt_code: str, providers, cfg, log):
    """一次把多条文本推进同一语言步骤，返回 (provider, 结果列表)。

    仅一条时等价于 `translate_step`；多条时由 `translate_many` 决定是否合并为一次请求。
    """
    texts = list(texts)
    if len(texts) == 1:
        provider, out = translate_step(texts[0], src_code, tgt_code, providers, cfg, log)
        return provider, [out]
    _reset_local_meta()
    provider, outs = _translate_with_providers(
        providers, cfg, log, lambda p: translate_many(p, texts, src_code, tgt_code, cfg))
    log.log(report.STEP, "  [%s] %s → %s：%d 条（批量）", provider, lang_label(src_code),
            lang_label(tgt_code), len(texts))
    return provider, outs


# --------------------------------------------------------------------------- #
# 路线与任务
# --------------------------------------------------------------------------- #
def tts_enabled(args) -> bool:
    """rwkv20 默认不生成语音；其它模式默认生成（--no-tts 关闭）。"""
    if getattr(args, "mode", None) == "rwkv20":
        return bool(getattr(args, "tts", False))
    return not bool(getattr(args, "no_tts", False))


def resolve_providers(args):
    """返回 (providers, 错误文案)。rwkv20 只允许本地后端；rwkv 只能配 rwkv20。"""
    mode = getattr(args, "mode", None)
    provider = getattr(args, "provider", None)
    if mode == "rwkv20":
        if provider and provider != "rwkv":
            return [], "--mode rwkv20 只支持本地后端，请去掉 --provider 或使用 --provider rwkv"
        return ["rwkv"], ""
    if provider == "rwkv":
        return [], "--provider rwkv 仅能与 --mode rwkv20 搭配（本地模型只做一次成型）"
    return ([provider] if provider else ["google", "bing", "baidu"]), ""


def resolve_languages(args) -> tuple[list[str], str]:
    if getattr(args, "mode", None) == "rwkv20":
        return ["rwkv20"], "rwkv20"
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
    if list(languages) == ["rwkv20"]:
        return [{"kind": "rwkv20", "index": 0, "lang": TARGET_ZH,
                 "src": TARGET_ZH, "tgt": TARGET_ZH}]
    tasks = []
    if detected != "en":
        tasks.append({"kind": "to_en", "lang": "en", "src": detected, "tgt": "en"})
    cur = "en"
    for i, lang in enumerate(languages):
        tasks.append({"kind": "step", "index": i, "lang": lang, "src": cur, "tgt": lang})
        cur = lang
    tasks.append({"kind": "to_zh", "lang": TARGET_ZH, "src": cur, "tgt": TARGET_ZH})
    return tasks


def signature(args, text, detected, languages, providers, cfg=None):
    sig = {
        "text": text,
        "detected": detected,
        "mode": args.mode,
        "chain": args.chain if args.mode == "fixed" else None,
        "languages": languages,
        "providers": list(providers),
    }
    if "rwkv" in providers:
        cfg_local = (cfg or {}).get("rwkv") or local_rwkv.from_args(args)
        sig["rwkv"] = local_rwkv.signature(cfg_local)
    return sig


# --------------------------------------------------------------------------- #
# 状态
# --------------------------------------------------------------------------- #
def load_state():
    return statefile.load_json_with_backup(STATE_PATH)


def save_state(state: dict):
    state["updated_at"] = datetime.now().isoformat(timespec="seconds")
    statefile.atomic_write_json(STATE_PATH, state)


def clear_state():
    statefile.remove_with_backup(STATE_PATH)


# --------------------------------------------------------------------------- #
# 日志
# --------------------------------------------------------------------------- #
def prune_logs(keep=None):
    """按 `STORY_TR_LOG_KEEP` 保留最近 N 个 `.log`（0/未设置 = 不清理）。"""
    if keep is None:
        try:
            keep = int(os.getenv("STORY_TR_LOG_KEEP", "0") or 0)
        except ValueError:
            keep = 0
    if keep <= 0 or not os.path.isdir(LOG_DIR):
        return []
    files = sorted((os.path.join(LOG_DIR, n) for n in os.listdir(LOG_DIR)
                    if n.endswith(".log") and os.path.isfile(os.path.join(LOG_DIR, n))),
                   key=os.path.getmtime)
    removed = files[:-keep] if len(files) > keep else []
    for path in removed:
        try:
            os.remove(path)
        except OSError:
            pass
    return removed


def setup_logging(no_file_log: bool, console_level: int = logging.INFO):
    """控制台 handler 走 `report.TqdmHandler`（日志上滚、进度条固定底部）；文件日志记全量。"""
    log = report.setup_console(console_level)

    logfile = None
    if not no_file_log:
        os.makedirs(LOG_DIR, exist_ok=True)
        logfile = os.path.join(LOG_DIR, datetime.now().strftime("%Y%m%d_%H%M%S") + ".log")
        fh = logging.FileHandler(logfile, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(fh)
        prune_logs()
    return log, logfile


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def add_common_args(p, default_mode="random"):
    """注册两个脚本共用的「翻译链 / 后端与速率 / 输出」参数（统一 CLI 设计）。"""
    g = p.add_argument_group("翻译链")
    g.add_argument("--mode", choices=["fixed", "random", "custom", "rwkv20"], default=default_mode,
                   help="路线模式（默认 %s；rwkv20 = 本地一次成型）" % default_mode)
    g.add_argument("--chain", choices=list(CHAINS), default="asia",
                   help="fixed 模式使用哪套链（默认 asia）")
    g.add_argument("--languages", default="", help="custom 模式的语言序列，逗号分隔")
    g.add_argument("--steps", type=int, default=20, help="random 模式抽取语言数（默认 20）")

    g = p.add_argument_group("后端与速率")
    g.add_argument("--provider", choices=["google", "bing", "baidu", "rwkv"], default=None,
                   help="固定单个后端；不指定则 google → bing → baidu 自动回退；"
                        "rwkv（本地）仅配合 --mode rwkv20")
    g.add_argument("--proxy", default="", help="代理地址（覆盖 .env 的 HTTPS_PROXY/HTTP_PROXY）")
    g.add_argument("--min-interval", type=float, default=GOOGLE_MIN_INTERVAL,
                   help="请求最小间隔秒数，避免触发限流（默认 %.1f，0 关闭）" % GOOGLE_MIN_INTERVAL)
    g.add_argument("--balance", action="store_true",
                   help="多后端轮转分摊请求（默认关闭；开启可分摊配额，但备用通道更慢/更易限流）")

    g = p.add_argument_group("本地 RWKV（rwkv20）")
    g.add_argument("--rwkv-url", default="",
                   help="本地服务地址（默认读 .env 的 RWKV_API_URL，再默认 %s）"
                        % local_rwkv.DEFAULT_URL)
    g.add_argument("--rwkv-template", default="",
                   help="提示词模板，占位符 {text}/{src}/{tgt}（默认读 .env 的 RWKV_PROMPT_TEMPLATE）")
    g.add_argument("--rwkv-max-tokens", type=int, default=None,
                   help="单块生成上限（默认读 .env 的 RWKV_MAX_TOKENS）")
    g.add_argument("--rwkv-temperature", type=float, default=None,
                   help="采样温度（默认读 .env 的 RWKV_TEMPERATURE）")
    g.add_argument("--rwkv-top-p", type=float, default=None,
                   help="采样 top_p（默认读 .env 的 RWKV_TOP_P）")
    g.add_argument("--rwkv-timeout", type=float, default=None,
                   help="单块请求超时秒数（默认读 .env 的 RWKV_TIMEOUT）")
    g.add_argument("--rwkv-chunk-chars", type=int, default=None,
                   help="长文本分块阈值（默认读 .env 的 RWKV_CHUNK_CHARS）")
    g.add_argument("--rwkv-retries", type=int, default=None,
                   help="输出异常重试次数（默认读 .env 的 RWKV_RETRIES）")

    g = p.add_argument_group("输出")
    gsel = g.add_mutually_exclusive_group()
    gsel.add_argument("--tts", dest="tts", action="store_true", default=None,
                      help="生成语音（rwkv20 模式默认不生成，需显式开启）")
    gsel.add_argument("--no-tts", dest="no_tts", action="store_true", help="不生成最终译文的语音")
    g.add_argument("--no-file-log", action="store_true", help="不写文件日志")
    report.add_verbosity_args(g)


def add_parser(sub):
    """注册 `story-tr chain` 子命令。"""
    p = sub.add_parser("chain", help="单文本「来回翻译 N 次」链路（最后译回中文）",
                       parents=[paths.data_dir_parent()])
    p.add_argument("text", nargs="?", default="Hello, World", help="待翻译文本（默认 Hello, World）")
    add_common_args(p, default_mode="random")
    p.add_argument("--reset", action="store_true", help="忽略旧状态，从头开始")
    p.add_argument("--dry-run", action="store_true", help="只显示路线，不翻译")
    p.set_defaults(func=main)
    return p


def build_cfg(args):
    load_dotenv(os.path.join(ROOT, ".env"))
    proxy = args.proxy or os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY") or ""
    proxies = {"http": proxy, "https": proxy} if proxy else None
    return {
        "proxies": proxies,
        "min_interval": getattr(args, "min_interval", GOOGLE_MIN_INTERVAL),
        "balance": getattr(args, "balance", False),
        "google_api_key": os.getenv("GOOGLE_API_KEY", ""),
        "bing_api_key": os.getenv("BING_API_KEY", ""),
        "bing_region": os.getenv("BING_REGION", ""),
        "baidu_appid": os.getenv("BAIDU_APPID", ""),
        "baidu_appkey": os.getenv("BAIDU_APPKEY", ""),
        "rwkv": local_rwkv.from_args(args),
    }


def main(args) -> int:
    log, logfile = setup_logging(args.no_file_log, report.console_level(args, show_steps=True))
    cfg = build_cfg(args)
    providers, err = resolve_providers(args)
    if err:
        print(err)
        return ExitCode.USAGE
    args.no_tts = not tts_enabled(args)

    text = args.text
    detected = detect_language(text)
    languages, chain_label = resolve_languages(args)

    print("原文：%s" % text)
    print("检出语言：%s" % lang_label(detected))
    print("路线：%s（%d 次）" % (chain_label, len(languages)))
    print("路线语言：%s" % " → ".join(languages))
    print("后端：%s" % " → ".join(providers))
    print("目标语言：%s" % lang_label(TARGET_ZH))

    if args.dry_run:
        if args.mode == "rwkv20":
            print("本地一次成型：每份 1 次本地生成（不访问云端）")
        print("--dry-run：仅展示路线，未执行翻译。")
        return ExitCode.OK

    tasks = build_tasks(detected, languages)
    sig = signature(args, text, detected, languages, providers, cfg)

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
            return ExitCode.SIGNATURE
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
                  **report.bar_kwargs()) as bar:
            for idx in range(next_index, len(tasks)):
                task = tasks[idx]
                bar.set_description("翻译链 %s→%s" % (task["src"], task["tgt"]))
                log.log(report.STEP, "[%d/%d] %s → %s", idx + 1, len(tasks),
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
        return ExitCode.UNSUPPORTED_LANGUAGE
    except QuotaExceeded as exc:
        save_state(state)
        log.error("今日配额已耗尽：%s", exc)
        log.error("进度已保存（.translate_state.json），次日重跑即可续跑。")
        return ExitCode.QUOTA
    except Paused as exc:
        save_state(state)
        log.error("已暂停：%s", exc)
        log.error("进度已保存（.translate_state.json）。排查/修复后重跑即可从断点继续。")
        return ExitCode.PAUSED
    except ConnectionIssue as exc:
        save_state(state)
        log.error("所有后端均连接失败：\n%s", exc)
        log.error("请检查网络/代理（可在 .env 配置 HTTPS_PROXY 或加 --proxy）后重跑。")
        return ExitCode.CONNECTION

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
    return ExitCode.OK
