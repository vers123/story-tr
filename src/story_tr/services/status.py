"""`story-tr status`：只读报告工作目录、数据覆盖、结果/语音、磁盘与批量进度。

默认不联网、不修改文件（状态文件损坏只报告）；仅 `--probe` 访问本地 RWKV 服务。
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Any

from story_tr.config import Settings, load_settings
from story_tr.errors import ExitCode
from story_tr.transport.rwkv import RwkvTranslator

from . import status_scan, storage
from .translate import failures as failures_mod

log = logging.getLogger("story_tr.services.status")

STATE_FILENAME = ".translate_stories_state.json"

_human = storage.human_size
_dir_size = status_scan.dir_size
_segment_lang = status_scan.segment_lang


def _read_state(path: str) -> tuple[dict[str, Any] | None, str | None]:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f), None
    except FileNotFoundError:
        return None, None
    except (OSError, json.JSONDecodeError) as exc:
        return None, str(exc)


def load_chars(home_: str) -> list[dict[str, Any]]:
    """读取 `data/character.json`（不存在返回空列表）。"""
    char_file = storage.char_index_path(home_)
    if not os.path.exists(char_file):
        return []
    with open(char_file, encoding="utf-8") as f:
        chars: list[dict[str, Any]] = json.load(f)
    return chars


def _state_info(home_: str) -> dict[str, Any]:
    """读取批次状态文件，归一为报告字段。"""
    state, err = _read_state(os.path.join(home_, STATE_FILENAME))
    info: dict[str, Any] = {"state": None, "state_error": err}
    if state:
        info["state"] = {
            "completed": len(state.get("completed") or []),
            "updated_at": state.get("updated_at"),
            "signature": state.get("signature"),
        }
    return info


def _rwkv_info(settings: Settings, probe: bool) -> dict[str, Any]:
    """本地 RWKV 配置摘要；`probe` 为真时才访问服务。"""
    model_path = (settings.rwkv_model_path or "").strip()
    info: dict[str, Any] = {
        "url": settings.rwkv_api_url,
        "template_present": bool((settings.rwkv_prompt_template or "").strip()),
        "model_path": model_path or None,
        "model_path_exists": bool(model_path and os.path.exists(model_path)),
        "probe": None,
    }
    if probe:
        try:
            info["probe"] = RwkvTranslator(settings).probe()
        except Exception as exc:  # noqa: BLE001 - 探测失败只报告
            info["probe"] = {"reachable": False, "error": str(exc)}
    return info


def collect(
    home_: str, deep: bool = False, probe: bool = False, result_tag: str | None = None
) -> dict[str, Any]:
    """扫描数据目录并汇总为报告字典（扫描 / 状态 / 渲染三部分之一）。"""
    settings = load_settings()
    chars = load_chars(home_)
    scanned = status_scan.scan_results(storage.story_root(home_), deep=deep, tag=result_tag)
    info: dict[str, Any] = {
        "workdir": home_,
        "characters": len(chars),
        "stories": sum(len(c.get("stories") or []) for c in chars),
        **status_scan.coverage(chars, scanned),
        **scanned["counts"],
        "result_tag": result_tag,
        "result_tags": status_scan.detect_result_tags(storage.story_root(home_)),
        "data_files": 0,
        "data_bytes": 0,
        "log_files": 0,
        "log_bytes": 0,
        "languages": None,
    }
    info["disk_tasks"] = info["titles"] + info["fulls"] + info["segments"]
    if deep:
        info["languages"] = dict(sorted(scanned["langs"].items(), key=lambda kv: -kv[1]))

    info["data_files"], info["data_bytes"] = _dir_size(os.path.join(home_, "data"))
    info["log_files"], info["log_bytes"] = _dir_size(storage.logs_dir(home_))
    info.update(_state_info(home_))
    info["pending_failures"] = _failure_info(home_)
    info["rwkv"] = _rwkv_info(settings, probe)
    return info


def _failure_info(home_: str) -> dict[str, Any] | None:
    """读取失败登记（纯读）；无未解决失败返回 `None`。"""
    registry = failures_mod.read_registry(home_)
    if not registry or not registry.failures:
        return None
    translate_count = sum(1 for record in registry.failures if record.stage == "translate")
    return {
        "count": len(registry.failures),
        "translate": translate_count,
        "tts": len(registry.failures) - translate_count,
        "updated_at": registry.updated_at,
    }


def _result_dir_line(info: dict[str, Any]) -> str:
    """结果目录行：显示当前 tag 与其他已存在的 tag 目录。"""
    tag = info.get("result_tag")
    dir_name = "result" if not tag else "result-" + tag
    others = [n for n in (info.get("result_tags") or []) if n != dir_name]
    if not others:
        return f"结果目录：{dir_name}"
    return f"结果目录：{dir_name}；另有 " + "、".join(others) + "（用 --result-tag 查看）"


def _rwkv_line(info: dict[str, Any]) -> str | None:
    """本地 RWKV 配置/探测行。"""
    rwkv_info = info.get("rwkv")
    if not rwkv_info:
        return None
    model_path = rwkv_info.get("model_path")
    if not model_path:
        model_detail = "未配置 RWKV_MODEL_PATH"
    elif rwkv_info.get("model_path_exists"):
        model_detail = "模型文件存在"
    else:
        model_detail = f"模型文件不存在: {model_path}"
    line = (
        f"本地 RWKV：{rwkv_info.get('url')}（"
        f"{'模板已配置' if rwkv_info.get('template_present') else '模板为空'}；{model_detail}）"
    )
    probed = rwkv_info.get("probe")
    if probed:
        if probed.get("reachable"):
            line += f"；服务 {probed.get('status') or '未知'}，模型 {probed.get('model') or '未知'}"
        else:
            line += f"；服务不可达：{probed.get('error') or '未知错误'}"
    return line


def _state_line(info: dict[str, Any]) -> str:
    """批量状态行：状态记录数 vs 磁盘结果数。"""
    if not info["state"]:
        if info["state_error"]:
            return f"批量状态：读取失败（{info['state_error']}）"
        return "批量状态：无（全部完成或尚未开始）"
    sig = info["state"].get("signature") or {}
    providers = ",".join(sig.get("providers") or []) or "—"
    rwkv_hint = "；rwkv=on" if sig.get("rwkv") else ""
    completed = info["state"]["completed"]
    disk_tasks = info.get("disk_tasks", 0)
    line = (
        f"批量状态：{completed} 条记录 / 磁盘 {disk_tasks} 份结果；mode={sig.get('mode')} "
        f"chain={sig.get('chain')} steps={sig.get('steps')} providers={providers}{rwkv_hint}；"
        f"更新于 {info['state'].get('updated_at')}"
    )
    if completed != disk_tasks:
        line += "（不一致：可运行 translate --reconcile 对账）"
    return line


def render(info: dict[str, Any]) -> str:
    pct = (100.0 * info["segment_mp3"] / info["segments"]) if info["segments"] else 0.0
    lines = [
        f"工作目录：{info['workdir']}",
        f"角色：{info['characters']}（有结果 {info['characters_with_result']} "
        f"/ 无结果 {info['characters_without_result']}）",
        f"故事：{info['stories']}（有结果 {info['stories_with_result']} "
        f"/ 无结果 {info['stories_without_result']}）；summary {info['summaries']} "
        f"/ title {info['titles']} / full {info['fulls']}",
        f"段落结果：{info['segments']} 个 JSON；语音 {info['segment_mp3']} 个（{pct:.1f}%）",
        f"磁盘：data {_human(info['data_bytes'])}（{info['data_files']} 文件）；"
        f"logs {_human(info['log_bytes'])}（{info['log_files']} 文件）",
        _result_dir_line(info),
    ]
    rwkv_line = _rwkv_line(info)
    if rwkv_line:
        lines.append(rwkv_line)
    lines.append(_state_line(info))
    pending = info.get("pending_failures")
    if pending:
        lines.append(
            "失败登记：{} 条未解决（translate {} / tts {}，更新于 {}）".format(
                pending["count"], pending["translate"], pending["tts"], pending["updated_at"]
            )
        )
    if info.get("languages"):
        lines.append("源语言分布：" + "，".join(f"{k} {v}" for k, v in info["languages"].items()))
    return "\n".join(lines)


def write_manifest(home_: str, info: dict[str, Any] | None = None) -> str:
    info = info or collect(home_)
    hashes: dict[str, str] = {}
    cache_path = os.path.join(home_, "data", "_fetch_all.json")
    try:
        with open(cache_path, encoding="utf-8") as f:
            for rec in json.load(f):
                if rec.get("id") and rec.get("hash"):
                    hashes[rec["id"]] = rec["hash"]
    except (OSError, ValueError):
        pass
    path = os.path.join(home_, "data", "manifest.json")
    storage.atomic_write_json(
        path,
        {
            "version": 1,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "characters": info["characters"],
            "stories": info["stories"],
            "stories_with_result": info["stories_with_result"],
            "segments": info["segments"],
            "segment_mp3": info["segment_mp3"],
            "hashes": hashes,
        },
    )
    return path


def run(
    json_out: bool = False,
    deep: bool = False,
    probe: bool = False,
    manifest: bool = False,
    data_dir: str | None = None,
    result_tag: str | None = None,
) -> ExitCode:
    try:
        result_tag = storage.validate_result_tag(result_tag)
    except ValueError as exc:
        log.error(str(exc))
        return ExitCode.USAGE
    home_ = storage.home(data_dir)
    if not os.path.exists(storage.char_index_path(home_)):
        log.error("找不到 %s；请先运行 fetch。", storage.char_index_path(home_))
        return ExitCode.USAGE
    info = collect(home_, deep=deep, probe=probe, result_tag=result_tag)
    manifest_path = write_manifest(home_, info) if manifest else None
    if json_out:
        print(json.dumps(info, ensure_ascii=False, indent=2))
    else:
        print(render(info))
    if manifest_path:
        print(f"清单已写入：{manifest_path}")
    return ExitCode.OK
