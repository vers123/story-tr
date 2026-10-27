"""本地 RWKV 后端（RWKV-Runner 的 OpenAI 兼容接口）。

约定：
    模型名统一传 `rwkv`（RWKV-Runner 忽略该字段，只认当前已加载的模型）；
    输出需清洗（模板回声 / 角色前缀 / 包裹引号）并校验（非空、非原文、含汉字）。

错误分类：
    连接 / 超时 / DNS / 5xx → ConnectionFailed；
    模型未加载（响应文本含关键词）→ ModelNotLoaded；
    空结果 / 与原文相同 / 不含汉字 → BadOutput（有限重试）。
"""
from __future__ import annotations

import re
from typing import Any

from story_tr.config import Settings
from story_tr.errors import BadOutput

from ..core.segment import clean_output
from .base import BaseTranslator, split_chunks

DEFAULT_URL = "http://127.0.0.1:8000"
MODEL_NAME = "rwkv"
DEFAULT_STOP = ["\n\nUser", "\n\nBot", "\n\nAssistant", "\n\nHuman", "\n\nQ"]

# 中文输出校验：至少含一个汉字（目标始终是中文）
_HAN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


def render(template: str, text: str, src: str = "zh-CN", tgt: str = "zh-CN") -> str:
    """替换模板占位符；模板缺少 {text} 时在末尾追加原文，保证原文一定进入 prompt。"""
    tpl = template or ""
    out = tpl.replace("{text}", text).replace("{src}", src).replace("{tgt}", tgt)
    if "{text}" not in tpl:
        out = out.rstrip()
        out = out + "\n\n" + text if out else text
    return out


def status_name(value: Any) -> str:
    """把 `/status` 的状态枚举归一化为名称（数字 0/2/3 → Offline/Loading/Working）。"""
    names = {0: "Offline", 2: "Loading", 3: "Working"}
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        return names.get(value, str(value))
    return str(value)


class RwkvTranslator(BaseTranslator):
    """本地 RWKV（RWKV-Runner 的 OpenAI 兼容接口），一次成型。"""

    name = "rwkv"
    min_interval = 0.0

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        # 上次生成的元数据（供 services 层写入 RwkvMeta）
        self.last_usage: dict[str, Any] = {}
        self.last_chunks = 0
        self.last_prompt_tokens = 0
        self.last_completion_tokens = 0

    @property
    def _base_url(self) -> str:
        return (self.settings.rwkv_api_url or DEFAULT_URL).rstrip("/")

    def translate(self, text: str, src: str, tgt: str) -> str:
        source = (text or "").strip()
        if not source:
            return ""
        template = self.settings.rwkv_template_unescaped
        chunks = split_chunks(source, self.settings.rwkv_chunk_chars)
        attempts = max(0, self.settings.rwkv_retries) + 1
        last_bad = BadOutput("本地 RWKV 输出异常")
        for _ in range(attempts):
            try:
                return self._translate_chunks(source, chunks, template, src, tgt)
            except BadOutput as exc:
                last_bad = exc
        raise last_bad

    def _translate_chunks(
        self, source: str, chunks: list[str], template: str, src: str, tgt: str
    ) -> str:
        """逐块生成并拼接；更新 last_usage / token 计数等元数据。"""
        url = self._base_url + "/v1/completions"
        self.last_chunks = len(chunks)
        self.last_usage = {}
        self.last_prompt_tokens = 0
        self.last_completion_tokens = 0
        parts: list[str] = []
        for chunk in chunks:
            prompt = render(template, chunk, src, tgt)
            payload: dict[str, Any] = {
                "prompt": prompt,
                "model": MODEL_NAME,
                "stream": False,
                "max_tokens": int(self.settings.rwkv_max_tokens),
                "temperature": float(self.settings.rwkv_temperature),
                "top_p": float(self.settings.rwkv_top_p),
                "presence_penalty": 0.0,
                "frequency_penalty": 1.0,
                "stop": list(DEFAULT_STOP),
            }
            data = self._post_json(url, payload)
            raw = self._extract_text(data)
            parts.append(clean_output(raw, prompt, chunk))
            got = data.get("usage") if isinstance(data, dict) else None
            if isinstance(got, dict) and got:
                self.last_usage = got
                self.last_prompt_tokens += self._as_int(got.get("prompt_tokens"))
                self.last_completion_tokens += self._as_int(got.get("completion_tokens"))
        out = "".join(parts).strip()
        self._validate(out, source)
        return out

    def _post_json(self, url: str, payload: dict[str, Any]) -> Any:
        # 连接超时与读超时分离：连接不可达最多 5s，生成仍用 RWKV_TIMEOUT
        read = float(self.settings.rwkv_timeout)
        resp = self._post(url, json=payload, timeout=(5.0, read))
        return self._read_json(resp)

    def _get_json(self, url: str, timeout: float) -> Any:
        resp = self._get(url, timeout=timeout)
        return self._read_json(resp)

    @staticmethod
    def _extract_text(data: Any) -> str:
        try:
            text = data["choices"][0]["text"]
        except (KeyError, IndexError, TypeError):
            raise BadOutput(f"返回结构异常：{str(data)[:120]}") from None
        if not isinstance(text, str):
            raise BadOutput(f"返回结构异常：{str(data)[:120]}")
        return text

    @staticmethod
    def _as_int(value: Any) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _validate(out: str, source: str) -> None:
        if not out:
            raise BadOutput("清洗后为空")
        if out == (source or "").strip():
            raise BadOutput("输出与原文相同")
        if not _HAN_RE.search(out):
            raise BadOutput("输出不含汉字")

    def probe(self) -> dict[str, Any]:
        """探测本地服务；返回 {reachable, status, model, error}。"""
        base = self._base_url
        result: dict[str, Any] = {"reachable": False, "status": None, "model": None, "error": None}
        try:
            data = self._get_json(base + "/status", 2.0)
        except Exception as exc:  # 探测不应因未知异常中断
            result["error"] = str(exc)
            return result
        result["reachable"] = True
        if isinstance(data, dict):
            value = data.get("status")
            result["status"] = status_name(value) if value is not None else None
        elif data is not None:
            result["status"] = status_name(data)
        try:
            models = self._get_json(base + "/v1/models", 2.0)
            items = models.get("data") if isinstance(models, dict) else None
            if items:
                result["model"] = items[0].get("id")
        except Exception:  # 模型名拿不到不影响 reachable
            pass
        return result
