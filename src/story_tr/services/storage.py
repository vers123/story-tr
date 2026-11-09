"""services 层共享存储：工作目录、路径助手、原子读写与后端解析。

路径解析统一走 `story_tr.config.resolve_data_dir`，JSON 统一走 pydantic 序列化；
本模块不 import typer / rich。
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import shutil
import time
from datetime import datetime
from typing import Any

from story_tr import transport
from story_tr.config import Settings, resolve_data_dir

log = logging.getLogger("story_tr.services")


# --------------------------------------------------------------------------- #
# 工作目录与路径助手
# --------------------------------------------------------------------------- #
def home(data_dir: str | None = None) -> str:
    """返回工作目录（绝对路径），优先级见 `config.resolve_data_dir`。"""
    return resolve_data_dir(data_dir)


def char_index_path(home_: str) -> str:
    return os.path.join(home_, "data", "character.json")


def story_root(home_: str) -> str:
    return os.path.join(home_, "data", "story", "character")


def profile_story_dir(home_: str, cid: str) -> str:
    return os.path.join(story_root(home_), cid, "profile", "story")


def result_dir(home_: str, cid: str) -> str:
    return os.path.join(story_root(home_), cid, "result")


def logs_dir(home_: str) -> str:
    return os.path.join(home_, "logs")


def out_dir(home_: str) -> str:
    return os.path.join(home_, "out")


def rel(home_: str, path: str) -> str:
    """把绝对路径渲染成相对工作目录的 POSIX 路径。"""
    return os.path.relpath(path, home_).replace(os.sep, "/")


def human_size(size: int) -> str:
    """字节数 → 人类可读（B / KB / MB / GB）。"""
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return ("%d %s" if unit == "B" else "%.1f %s") % (value, unit)
        value /= 1024.0
    return f"{value:.1f} GB"


# --------------------------------------------------------------------------- #
# JSON 原子读写
# --------------------------------------------------------------------------- #
def _replace_with_retry(tmp: str, path: str) -> None:
    """`os.replace` 失败（Windows 杀软 / 索引短暂占用）时退避重试。"""
    for attempt in range(5):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.05 * (attempt + 1))


def _ensure_parent(path: str) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)


def atomic_write_text(path: str, text: str, *, backup: bool = False, skip_if_unchanged: bool = True) -> bool:
    """原子写文本：临时文件 + `os.replace`；可选保留上一版 `.bak`。

    `skip_if_unchanged` 为真时先读盘比较，内容一致直接返回 `False`（不落盘），
    返回 `True` 表示确实写入了文件。
    """
    if skip_if_unchanged:
        try:
            with open(path, encoding="utf-8") as f:
                if f.read() == text:
                    return False
        except OSError:
            pass
    _ensure_parent(path)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    if backup and os.path.exists(path):
        with contextlib.suppress(OSError):  # 备份失败不影响主写入
            shutil.copy2(path, path + ".bak")
    _replace_with_retry(tmp, path)
    return True


def json_text(obj: Any) -> str:
    """把 pydantic / 普通对象序列化为稳定 JSON 文本（`ensure_ascii=False, indent=2`）。"""
    if hasattr(obj, "model_dump"):
        obj = obj.model_dump(mode="json")
    return json.dumps(obj, ensure_ascii=False, indent=2)


def atomic_write_json(path: str, obj: Any, *, backup: bool = True, skip_if_unchanged: bool = True) -> bool:
    """原子写 JSON（默认保留上一版 `.bak`，内容未变则跳过写入）。"""
    return atomic_write_text(path, json_text(obj), backup=backup, skip_if_unchanged=skip_if_unchanged)


def load_json_with_backup(path: str) -> Any:
    """读取 JSON；损坏时回退 `.bak`，再失败则隔离 `.corrupt-*` 并返回 None。"""
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        backup = path + ".bak"
        if os.path.exists(backup):
            try:
                with open(backup, encoding="utf-8") as f:
                    data = json.load(f)
                log.warning("状态文件损坏（%s），已从备份 %s 恢复。", exc, os.path.basename(backup))
                return data
            except (OSError, json.JSONDecodeError):
                pass
        corrupt = path + ".corrupt-" + datetime.now().strftime("%Y%m%d_%H%M%S")
        with contextlib.suppress(OSError):
            os.replace(path, corrupt)
        log.warning("状态文件无法读取（%s），已保留为 %s，本次按无进度启动。", exc, os.path.basename(corrupt))
        return None


def remove_with_backup(path: str) -> None:
    """软删除：把主文件重命名为 `.bak`，保留最近一版可恢复。"""
    if os.path.exists(path):
        with contextlib.suppress(OSError):
            os.replace(path, path + ".bak")


# --------------------------------------------------------------------------- #
# 后端解析与构造
# --------------------------------------------------------------------------- #
def resolve_providers(provider: str | None, mode: str) -> tuple[list[str], str | None]:
    """统一 provider 解析：`auto`/空 → `DEFAULT_ORDER`；`rwkv20` 只允许 `rwkv`；
    `rwkv` 只能配 `rwkv20`。返回 `(providers, 错误文案或 None)`。
    """
    provider = (provider or "").strip()
    if mode == "rwkv20":
        if provider and provider != "rwkv":
            return [], "--mode rwkv20 只支持本地后端（rwkv），请去掉 --provider 或使用 --provider rwkv"
        return ["rwkv"], None
    if provider == "rwkv":
        return [], "--provider rwkv 仅能与 --mode rwkv20 搭配（本地模型只做一次成型）"
    if not provider or provider == "auto":
        return list(transport.DEFAULT_ORDER), None
    if provider in transport.PROVIDERS:
        return [provider], None
    return [], f"未知翻译后端：{provider}"


def build_translators(providers: list[str], settings: Settings) -> list[transport.Translator]:
    """按 provider 顺序构造翻译器实例（用 `transport.build_translator`）。"""
    return [transport.build_translator(p, settings) for p in providers]
