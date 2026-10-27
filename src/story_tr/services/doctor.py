"""`story-tr doctor`：检查运行环境（数据、状态文件、密钥、代理、本地 RWKV）。

只读、默认不联网；`--probe` 才访问本地 RWKV 服务。数据/状态损坏 → FAIL（返回 1）；
本地服务不可达 → WARN。已移除 ffmpeg / 字体 / Pillow / PySide6 / mcp 检查。
"""
from __future__ import annotations

import json
import logging
import os
import sys
from typing import Any

from story_tr.config import load_settings
from story_tr.errors import ExitCode
from story_tr.transport.rwkv import RwkvTranslator

from . import _io

log = logging.getLogger("story_tr.services.doctor")

OK, WARN, FAIL = "OK", "WARN", "FAIL"
_LABELS = {OK: "[ OK ]", WARN: "[WARN]", FAIL: "[FAIL]"}

STATE_FILENAME = ".translate_stories_state.json"


def _rwkv_config_row(settings: Any) -> tuple[str, str, str]:
    template_ok = bool((settings.rwkv_prompt_template or "").strip())
    model_path = (settings.rwkv_model_path or "").strip()
    if not model_path:
        model_detail = "未配置 RWKV_MODEL_PATH"
    elif os.path.exists(model_path):
        model_detail = "模型文件存在"
    else:
        model_detail = f"模型文件不存在: {model_path}"
    detail = "{}（{}；{}）".format(settings.rwkv_api_url,
                              "模板已配置" if template_ok else "模板为空", model_detail)
    ok = template_ok and (not model_path or os.path.exists(model_path))
    return (OK if ok else WARN, "本地 RWKV", detail)


def _rwkv_probe_row(settings: Any) -> tuple[str, str, str]:
    try:
        info = RwkvTranslator(settings).probe()
    except Exception as exc:  # noqa: BLE001 - 探测失败只告警
        return (WARN, "本地 RWKV 服务", f"不可达：{exc}")
    if not info.get("reachable"):
        return (WARN, "本地 RWKV 服务", "不可达：%s" % (info.get("error") or "未知错误"))
    status = info.get("status")
    if status != "Working":
        return (WARN, "本地 RWKV 服务", "可达，状态 %s（模型未加载？）" % (status or "未知"))
    return (OK, "本地 RWKV 服务", "Working，模型 %s" % (info.get("model") or "未知"))


def collect(home_: str, probe: bool = False) -> list[tuple[str, str, str]]:
    settings = load_settings()
    rows: list[tuple[str, str, str]] = []
    rows.append((OK, "Python", f"{sys.version.split()[0]} ({sys.platform})"))
    rows.append((OK if os.path.isdir(home_) else FAIL, "工作目录", home_))

    char_file = _io.char_index_path(home_)
    rows.append((OK if os.path.exists(char_file) else FAIL, "角色索引", char_file))

    data_dir = os.path.join(home_, "data")
    rows.append((OK if os.path.isdir(data_dir) and os.access(data_dir, os.W_OK) else WARN,
                 "data/ 可写", data_dir))

    state_path = os.path.join(home_, STATE_FILENAME)
    if not os.path.exists(state_path):
        rows.append((OK, "批量状态", "无（全部完成或尚未开始）"))
    else:
        try:
            with open(state_path, encoding="utf-8") as f:
                data = json.load(f)
            rows.append((OK, "批量状态", f"{len(data.get('completed') or [])} 条完成"))
        except (OSError, json.JSONDecodeError) as exc:
            rows.append((FAIL, "批量状态", f"无法读取：{exc}"))

    env_path = os.path.join(home_, ".env")
    if not os.path.exists(env_path):
        rows.append((WARN, ".env", "不存在；将使用免 key 通道（可复制 .env.example）"))
    else:
        configured = [name for name, value in (
            ("GOOGLE_API_KEY", settings.google_api_key),
            ("BING_API_KEY", settings.bing_api_key),
            ("BAIDU_APPID", settings.baidu_appid),
            ("BAIDU_APPKEY", settings.baidu_appkey),
        ) if value]
        rows.append((OK, ".env", "已配置：%s" % (", ".join(configured) or "无（免 key 通道）")))

    rows.append(_rwkv_config_row(settings))
    if probe:
        rows.append(_rwkv_probe_row(settings))

    proxy = settings.proxy or settings.http_proxy
    rows.append((OK, "代理", proxy or "未设置（可选）"))
    return rows


def render(rows: list[tuple[str, str, str]]) -> str:
    width = max(len(name) for _, name, _ in rows) if rows else 0
    lines = []
    for status, name, detail in rows:
        lines.append(f"{_LABELS.get(status, status)} {name.ljust(width)}  {detail}")
    return "\n".join(lines)


def run(probe: bool = False, data_dir: str | None = None) -> ExitCode:
    home_ = _io.home(data_dir)
    rows = collect(home_, probe=probe)
    print(render(rows))
    failed = [name for status, name, _ in rows if status == FAIL]
    if failed:
        log.error("检查未通过：%s", "、".join(failed))
        return ExitCode.USAGE
    log.info("检查通过（WARN 项仅表示对应可选功能不可用）。")
    return ExitCode.OK
