# -*- coding: utf-8 -*-
"""`story-tr doctor`：检查运行环境（数据、可选依赖、ffmpeg、字体、密钥、状态文件、本地 RWKV）。

只读、默认不联网；只有显式 `--probe` 才访问本地 RWKV 服务（`/status`、`/v1/models`）。
缺可选依赖只给 WARN，本地服务不可达也只给 WARN；数据目录/状态文件不可用才 FAIL。
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys

from . import paths, rwkv, stories

OK, WARN, FAIL = "OK", "WARN", "FAIL"
_LABELS = {OK: "[ OK ]", WARN: "[WARN]", FAIL: "[FAIL]"}


def _row(status, name, detail=""):
    return (status, name, detail)


def _rwkv_config_row(cfg, model_path):
    """离线检查本地 RWKV 配置：标签、状态与说明（永不 FAIL）。"""
    template_ok = bool((cfg.template or "").strip())
    if not model_path:
        model_detail = "未配置 RWKV_MODEL_PATH"
    elif os.path.exists(model_path):
        model_detail = "模型文件存在"
    else:
        model_detail = "模型文件不存在: %s" % model_path
    detail = "%s（%s；%s）" % (cfg.url,
                              "模板已配置" if template_ok else "模板为空",
                              model_detail)
    ok = template_ok and (not model_path or os.path.exists(model_path))
    return _row(OK if ok else WARN, "本地 RWKV", detail)


def _rwkv_probe_row(cfg):
    """访问本地服务检查可达性与模型状态（只用于 `--probe`，异常转为 WARN）。"""
    try:
        info = rwkv.probe(cfg)
    except Exception as exc:  # noqa: BLE001 - 探测失败不应让 doctor 抛异常
        return _row(WARN, "本地 RWKV 服务", "不可达：%s" % exc)
    if not info.get("reachable"):
        return _row(WARN, "本地 RWKV 服务",
                    "不可达：%s" % (info.get("error") or "未知错误"))
    status = info.get("status")
    if status != "Working":
        return _row(WARN, "本地 RWKV 服务",
                    "可达，状态 %s（模型未加载？）" % (status or "未知"))
    return _row(OK, "本地 RWKV 服务", "Working，模型 %s" % (info.get("model") or "未知"))


def collect(probe: bool = False) -> list:
    """返回 `[(status, name, detail), …]`，status ∈ OK/WARN/FAIL。

    默认不联网；`probe=True` 时额外访问本地 RWKV 服务（短超时，不可达只给 WARN）。
    """
    rows = []
    rows.append(_row(OK, "Python", "%s (%s)" % (sys.version.split()[0], sys.platform)))

    home = paths.home()
    rwkv.load_env(home)
    rows.append(_row(OK if os.path.isdir(home) else FAIL, "工作目录", home))
    rows.append(_row(OK if os.path.exists(stories.CHAR_FILE) else FAIL,
                     "角色索引", stories.CHAR_FILE))
    data_dir = os.path.join(home, "data")
    rows.append(_row(OK if os.path.isdir(data_dir) and os.access(data_dir, os.W_OK) else WARN,
                     "data/ 可写", data_dir))

    state = stories.STATE_PATH
    if not os.path.exists(state):
        rows.append(_row(OK, "批量状态", "无（全部完成或尚未开始）"))
    else:
        try:
            with open(state, encoding="utf-8") as f:
                data = json.load(f)
            rows.append(_row(OK, "批量状态", "%d 条完成" % len(data.get("completed") or [])))
        except (OSError, json.JSONDecodeError) as exc:
            rows.append(_row(FAIL, "批量状态", "无法读取：%s" % exc))

    for module, label, required in (("PIL", "Pillow（video）", False),
                                    ("PySide6", "PySide6（gui）", False),
                                    ("mcp", "mcp（MCP Server）", False)):
        found = importlib.util.find_spec(module) is not None
        rows.append(_row(OK if found else WARN, label,
                         "已安装" if found else "未安装（对应可选功能不可用）"))
        if required and not found:
            rows[-1] = (FAIL, label, "未安装")

    for exe in ("ffmpeg", "ffprobe"):
        path = shutil.which(exe)
        rows.append(_row(OK if path else WARN, exe, path or "未找到（video 不可用）"))

    try:
        from .video import find_fonts
        bold, regular = find_fonts()
        rows.append(_row(OK, "中文字体", "%s / %s" % (os.path.basename(bold),
                                                      os.path.basename(regular))))
    except Exception as exc:  # noqa: BLE001 - 字体探测失败只影响 video
        rows.append(_row(WARN, "中文字体", str(exc)))

    env_path = os.path.join(home, ".env")
    if not os.path.exists(env_path):
        rows.append(_row(WARN, ".env", "不存在；将使用免 key 通道（可复制 .env.example）"))
    else:
        try:
            from dotenv import dotenv_values
            values = dotenv_values(env_path)
        except Exception:  # noqa: BLE001
            values = {}
        configured = [name for name in ("GOOGLE_API_KEY", "BING_API_KEY",
                                        "BAIDU_APPID", "BAIDU_APPKEY")
                      if values.get(name)]
        rows.append(_row(OK, ".env", "已配置：%s" % (", ".join(configured) or "无（免 key 通道）")))

    cfg = rwkv.from_env()
    model_path = os.environ.get("RWKV_MODEL_PATH", "").strip()
    rows.append(_rwkv_config_row(cfg, model_path))
    if probe:
        rows.append(_rwkv_probe_row(cfg))

    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
    rows.append(_row(OK, "代理", proxy or "未设置（可选；也可用 --proxy）"))
    return rows


def render(rows) -> str:
    width = max(len(name) for _, name, _ in rows) if rows else 0
    lines = []
    for status, name, detail in rows:
        lines.append("%s %s  %s" % (_LABELS.get(status, status), name.ljust(width), detail))
    return "\n".join(lines)


def add_parser(sub):
    """注册 `story-tr doctor` 子命令。"""
    p = sub.add_parser("doctor", help="检查运行环境（数据、可选依赖、ffmpeg、字体、密钥）",
                       parents=[paths.data_dir_parent()])
    p.add_argument("--probe", action="store_true",
                   help="访问本地 RWKV 服务检查可达性与模型状态；默认不联网")
    p.set_defaults(func=main)
    return p


def main(args) -> int:
    rows = collect(probe=getattr(args, "probe", False))
    print(render(rows))
    failed = [name for status, name, _ in rows if status == FAIL]
    if failed:
        print("\n检查未通过：%s" % "、".join(failed))
        return 1
    print("\n检查通过（WARN 项仅表示对应可选功能不可用）。")
    return 0
