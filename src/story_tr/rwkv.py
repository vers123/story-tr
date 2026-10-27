#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地 RWKV 后端（通过 RWKV-Runner 的 OpenAI 兼容接口调用）。

RWKV-Runner（MIT）是独立的外部服务，本项目只通过 HTTP 调用、不复制其代码：
    POST /v1/completions  一次生成（模型已被微调成「翻译 20 次」一次成型）
    GET  /status          服务/模型状态（数字或字符串枚举）
    GET  /v1/models       当前加载的模型名

约定：
    模型名统一传 `rwkv`（RWKV-Runner 忽略该字段，只认当前已加载的模型）；
    不自动调用 /switch-model 加载模型，模型由用户在 Runner 中自行加载；
    输出需清洗（模板回声 / 角色前缀 / 包裹引号）并校验（非空、非原文、含汉字）。

错误分类：
    RwkvUnavailable  连接拒绝 / 超时 / DNS / 5xx（非模型问题）/ 429 —— 立即失败，不重试；
    RwkvModelOffline 服务在但模型未加载 —— 立即失败，提示先加载模型；
    RwkvBadOutput    空结果 / 结构异常 / 清洗后为空 / 与原文相同 / 不含汉字 —— 有限重试。
"""
from __future__ import annotations

import hashlib
import os
import re
import time
from dataclasses import dataclass

import requests

DEFAULT_URL = "http://127.0.0.1:8000"
DEFAULT_TEMPLATE = "User: {text}\n\nBot:"
DEFAULT_STOP = ["\n\nUser", "\n\nBot", "\n\nAssistant", "\n\nHuman", "\n\nQ"]

MODEL_NAME = "rwkv"

# 中文输出校验：至少含一个汉字（目标始终是中文）
_HAN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
# 行首角色前缀（中英文冒号）
_ROLE_RE = re.compile(r"(?m)^(?:User|Bot|Assistant|System)\s*[:：]\s*")
_QUOTE_PAIRS = (('"', '"'), ("“", "”"), ("'", "'"), ("「", "」"))


class RwkvError(Exception):
    """本地 RWKV 后端错误的基类。"""


class RwkvUnavailable(RwkvError):
    """服务不可用：连接拒绝 / 超时 / DNS / 5xx（非模型问题）/ 429。不重试。"""


class RwkvModelOffline(RwkvError):
    """服务在但模型未加载（`/status` 非 Working，或响应带「未加载模型」类提示）。不重试。"""


class RwkvBadOutput(RwkvError):
    """返回空 / 结构异常 / 清洗后为空 / 与原文相同 / 不含汉字：有限次重试。"""


@dataclass
class RwkvConfig:
    """本地后端配置（默认值即 RWKV-Runner 的默认监听地址与常用采样参数）。"""

    url: str = DEFAULT_URL
    template: str = DEFAULT_TEMPLATE
    max_tokens: int = 1024
    temperature: float = 1.0
    top_p: float = 0.3
    presence_penalty: float = 0.0
    frequency_penalty: float = 1.0
    timeout: float = 300.0
    chunk_chars: int = 1200
    retries: int = 2


# --------------------------------------------------------------------------- #
# 配置
# --------------------------------------------------------------------------- #
def _unescape(value: str) -> str:
    """把字面的 `\\n` / `\\r` / `\\t` 转成真实控制字符（CLI 与环境变量常见写法）。"""
    if not value:
        return value
    return (value.replace("\\r\\n", "\n").replace("\\n", "\n")
            .replace("\\r", "\r").replace("\\t", "\t"))


def _env_str(name: str, default: str) -> str:
    value = (os.environ.get(name) or "").strip()
    return value or default


def _env_num(name: str, default, cast):
    """读取数字型环境变量；空值或解析失败都用默认值（不抛异常）。"""
    value = (os.environ.get(name) or "").strip()
    if not value:
        return default
    try:
        return cast(value)
    except (TypeError, ValueError):
        return default


def from_env() -> RwkvConfig:
    """按 `RWKV_*` 环境变量构造配置（空值 / 非法数字回退默认值）。"""
    url = _env_str("RWKV_API_URL", DEFAULT_URL).rstrip("/") or DEFAULT_URL
    return RwkvConfig(
        url=url,
        template=_unescape(_env_str("RWKV_PROMPT_TEMPLATE", DEFAULT_TEMPLATE)),
        max_tokens=_env_num("RWKV_MAX_TOKENS", 1024, int),
        temperature=_env_num("RWKV_TEMPERATURE", 1.0, float),
        top_p=_env_num("RWKV_TOP_P", 0.3, float),
        timeout=_env_num("RWKV_TIMEOUT", 300.0, float),
        chunk_chars=_env_num("RWKV_CHUNK_CHARS", 1200, int),
        retries=_env_num("RWKV_RETRIES", 2, int),
    )


def load_env(home: str = None) -> None:
    """读取工作目录的 `.env`（不覆盖已有环境变量）；供不经过 chain.build_cfg 的入口使用。"""
    from dotenv import load_dotenv

    from . import paths

    load_dotenv(os.path.join(home or paths.home(), ".env"))


def from_args(args) -> RwkvConfig:
    """以 `from_env()` 为基线，用 CLI 属性覆盖（缺失 / None / 空串保留基线）。"""
    cfg = from_env()
    setters = (
        ("rwkv_url", "url", str),
        ("rwkv_template", "template", str),
        ("rwkv_max_tokens", "max_tokens", int),
        ("rwkv_temperature", "temperature", float),
        ("rwkv_top_p", "top_p", float),
        ("rwkv_timeout", "timeout", float),
        ("rwkv_chunk_chars", "chunk_chars", int),
        ("rwkv_retries", "retries", int),
    )
    for attr, field, cast in setters:
        value = getattr(args, attr, None)
        if value is None or value == "":
            continue
        if cast is str:
            value = _unescape(str(value))
        else:
            try:
                value = cast(value)
            except (TypeError, ValueError):
                continue
        setattr(cfg, field, value)
    if isinstance(cfg.url, str):
        cfg.url = cfg.url.rstrip("/") or DEFAULT_URL
    return cfg


def signature(cfg: RwkvConfig) -> dict:
    """续跑签名里的本地子对象（模板只存摘要，避免状态文件过长）。"""
    template = cfg.template or ""
    return {
        "url": cfg.url,
        "model": MODEL_NAME,
        "template_sha1": hashlib.sha1(template.encode("utf-8")).hexdigest(),
        "max_tokens": cfg.max_tokens,
        "temperature": cfg.temperature,
        "top_p": cfg.top_p,
        "chunk_chars": cfg.chunk_chars,
    }


def _base_url(cfg: RwkvConfig) -> str:
    return (cfg.url or DEFAULT_URL).rstrip("/")


# --------------------------------------------------------------------------- #
# 提示词与分块
# --------------------------------------------------------------------------- #
def render(template: str, text: str, src: str = "zh-CN", tgt: str = "zh-CN") -> str:
    """替换模板占位符；模板缺少 `{text}` 时在末尾追加原文，保证原文一定进入 prompt。"""
    tpl = template or ""
    out = tpl.replace("{text}", text).replace("{src}", src).replace("{tgt}", tgt)
    if "{text}" not in tpl:
        out = out.rstrip()
        out = out + "\n\n" + text if out else text
    return out


def split_chunks(text: str, limit: int) -> list[str]:
    """把超长文本切成 ≤ limit 的片段（优先按句末标点，必要时硬切）。"""
    text = text or ""
    if not text:
        return []
    if limit <= 0 or len(text) <= limit:
        return [text]
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


# --------------------------------------------------------------------------- #
# 输出清洗
# --------------------------------------------------------------------------- #
def _strip_quotes(text: str) -> str:
    """去掉成对包裹的引号（可多层包裹）。"""
    while len(text) >= 2:
        for left, right in _QUOTE_PAIRS:
            if text.startswith(left) and text.endswith(right):
                text = text[len(left):len(text) - len(right)].strip()
                break
        else:
            return text
    return text


def clean_output(raw: str, prompt: str, source: str) -> str:
    """清洗模型输出：去 prompt 回声、行首角色前缀、包裹引号与行尾空白（保留段落空行）。"""
    out = (raw or "").strip()
    for prefix in (prompt or "", (prompt or "").rstrip()):
        if prefix and out.startswith(prefix):
            out = out[len(prefix):]
            break
    out = out.strip()
    while True:
        stripped = _ROLE_RE.sub("", out)
        if stripped == out:
            break
        out = stripped
    out = _strip_quotes(out.strip())
    out = "\n".join(line.rstrip() for line in out.splitlines())
    return out.strip()


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
def _read_json(resp, url: str):
    """把 HTTP 响应的状态码翻译成本模块的错误类型，再返回 JSON。"""
    status = getattr(resp, "status_code", 0) or 0
    text = (getattr(resp, "text", "") or "").strip()
    if status == 429:
        raise RwkvUnavailable("本地 RWKV 服务限流（HTTP 429）：%s" % text[:120])
    low = text.lower()
    if any(k in low for k in ("model not loaded", "not loaded", "no model", "failed to load")):
        raise RwkvModelOffline("本地 RWKV 模型未加载：%s" % text[:120])
    if status >= 500:
        raise RwkvUnavailable("本地 RWKV 服务异常（HTTP %d）：%s" % (status, text[:120]))
    if status >= 400:
        raise RwkvError("本地 RWKV 接口错误（HTTP %d）：%s" % (status, text[:120]))
    try:
        return resp.json()
    except ValueError:
        raise RwkvError("本地 RWKV 响应不是 JSON：%s" % text[:120])


def _post_json(url: str, payload: dict, timeout: float):
    """POST JSON（连接类异常统一转为 `RwkvUnavailable`）。

    连接超时与读超时分离：连接不可达时最多 5s（而非等满 `RWKV_TIMEOUT`），
    模型生成本身仍可用 `RWKV_TIMEOUT` 的读超时。
    """
    read_timeout = float(timeout)
    connect_timeout = min(5.0, read_timeout) if read_timeout > 0 else 5.0
    try:
        resp = requests.post(url, json=payload, timeout=(connect_timeout, read_timeout))
    except requests.exceptions.RequestException as exc:
        raise RwkvUnavailable("本地 RWKV 请求失败（%s）：%s" % (type(exc).__name__, exc))
    return _read_json(resp, url)


def _get_json(url: str, timeout: float):
    """GET JSON（连接类异常统一转为 `RwkvUnavailable`）。"""
    try:
        resp = requests.get(url, timeout=timeout)
    except requests.exceptions.RequestException as exc:
        raise RwkvUnavailable("本地 RWKV 请求失败（%s）：%s" % (type(exc).__name__, exc))
    return _read_json(resp, url)


def status_name(value) -> str:
    """把 `/status` 的状态枚举归一化成名称（数字 0/2/3 → Offline/Loading/Working）。"""
    names = {0: "Offline", 2: "Loading", 3: "Working"}
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        return names.get(value, str(value))
    return str(value)


def probe(cfg: RwkvConfig = None, timeout: float = 2.0) -> dict:
    """探测本地服务（短超时，不影响 cfg.timeout）；`/status` 有响应即算 reachable。"""
    cfg = cfg or from_env()
    base = _base_url(cfg)
    result = {"reachable": False, "status": None, "model": None, "error": None}
    try:
        data = _get_json(base + "/status", timeout)
    except RwkvError as exc:
        result["error"] = str(exc)
        return result
    except Exception as exc:  # noqa: BLE001 - 探测不应因未知异常中断
        result["error"] = str(exc)
        return result

    result["reachable"] = True
    if isinstance(data, dict):
        value = data.get("status")
        result["status"] = status_name(value) if value is not None else None
    elif data is not None:
        result["status"] = status_name(data)

    try:
        models = _get_json(base + "/v1/models", timeout)
        items = models.get("data") if isinstance(models, dict) else None
        if items:
            result["model"] = items[0].get("id")
    except Exception:  # noqa: BLE001 - 模型名拿不到不影响 reachable
        pass
    return result


# --------------------------------------------------------------------------- #
# 翻译
# --------------------------------------------------------------------------- #
def _extract_text(data) -> str:
    """取 `choices[0].text`；结构异常按业务失败处理（可重试）。"""
    try:
        text = data["choices"][0]["text"]
    except (KeyError, IndexError, TypeError):
        raise RwkvBadOutput("返回结构异常：%s" % str(data)[:120])
    if not isinstance(text, str):
        raise RwkvBadOutput("返回结构异常：%s" % str(data)[:120])
    return text


def _as_int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _validate(out: str, source: str) -> None:
    """输出校验：空 / 与原文相同 / 不含汉字 → 业务失败（可重试）。"""
    if not out:
        raise RwkvBadOutput("清洗后为空")
    if out == (source or "").strip():
        raise RwkvBadOutput("输出与原文相同")
    if not _HAN_RE.search(out):
        raise RwkvBadOutput("输出不含汉字")


def _translate_chunks(source: str, chunks: list[str], template: str, cfg: RwkvConfig) -> dict:
    """逐块生成并拼接；返回 text / chunks / usage 等（不含 elapsed_ms）。"""
    url = _base_url(cfg) + "/v1/completions"
    parts = []
    usage = {}
    prompt_tokens = 0
    completion_tokens = 0
    for chunk in chunks:
        prompt = render(template, chunk)
        payload = {
            "prompt": prompt,
            "model": MODEL_NAME,
            "stream": False,
            "max_tokens": int(cfg.max_tokens),
            "temperature": float(cfg.temperature),
            "top_p": float(cfg.top_p),
            "presence_penalty": float(cfg.presence_penalty),
            "frequency_penalty": float(cfg.frequency_penalty),
            "stop": list(DEFAULT_STOP),
        }
        data = _post_json(url, payload, float(cfg.timeout))
        raw = _extract_text(data)
        parts.append(clean_output(raw, prompt, chunk))
        got = data.get("usage") if isinstance(data, dict) else None
        if isinstance(got, dict) and got:
            usage = got  # 多块时保留最后一块的 usage 原样，另汇总 token 数
            prompt_tokens += _as_int(got.get("prompt_tokens"))
            completion_tokens += _as_int(got.get("completion_tokens"))
    out = "".join(parts).strip()
    _validate(out, source)
    return {
        "text": out,
        "chunks": len(chunks),
        "usage": usage,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
    }


def translate(text: str, cfg: RwkvConfig = None, template: str = None) -> dict:
    """把一段文本一次生成成中文；`RwkvBadOutput` 有限重试，其它错误立即抛。"""
    cfg = cfg or from_env()
    source = (text or "").strip()
    if not source:
        return {"text": "", "chunks": 0, "usage": {}, "prompt_tokens": 0,
                "completion_tokens": 0, "elapsed_ms": 0}
    tpl = cfg.template if template is None else template
    chunks = split_chunks(source, cfg.chunk_chars)
    attempts = max(0, int(cfg.retries)) + 1
    started = time.monotonic()
    last_bad = None
    for _ in range(attempts):
        try:
            result = _translate_chunks(source, chunks, tpl, cfg)
        except RwkvBadOutput as exc:
            last_bad = exc
            continue
        result["elapsed_ms"] = int(round((time.monotonic() - started) * 1000))
        return result
    raise last_bad
