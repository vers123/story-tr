"""`story-tr status`：只读报告工作目录、数据覆盖、结果/语音、磁盘与批量进度。

默认不联网、不修改文件（状态文件损坏只报告）；仅 `--probe` 访问本地 RWKV 服务。
"""
from __future__ import annotations

import glob
import json
import logging
import os
from datetime import datetime
from typing import Any

from story_tr.config import load_settings
from story_tr.errors import ExitCode
from story_tr.transport.rwkv import RwkvTranslator

from . import _io

log = logging.getLogger("story_tr.services.status")

STATE_FILENAME = ".translate_stories_state.json"


def _dir_size(path: str) -> tuple[int, int]:
    files = size = 0
    if not os.path.isdir(path):
        return 0, 0
    for root, _, names in os.walk(path):
        for name in names:
            try:
                size += os.path.getsize(os.path.join(root, name))
                files += 1
            except OSError:
                pass
    return files, size


def _human(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return ("%d %s" if unit == "B" else "%.1f %s") % (value, unit)
        value /= 1024.0
    return f"{value:.1f} GB"


def _read_state(path: str) -> tuple[dict[str, Any] | None, str | None]:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f), None
    except FileNotFoundError:
        return None, None
    except (OSError, json.JSONDecodeError) as exc:
        return None, str(exc)


def collect(home_: str, deep: bool = False, probe: bool = False) -> dict[str, Any]:
    settings = load_settings()
    char_file = _io.char_index_path(home_)
    chars: list[dict[str, Any]] = []
    if os.path.exists(char_file):
        with open(char_file, encoding="utf-8") as f:
            chars = json.load(f)

    root = _io.story_root(home_)
    info: dict[str, Any] = {
        "workdir": home_,
        "characters": len(chars),
        "stories": sum(len(c.get("stories") or []) for c in chars),
        "characters_with_result": 0,
        "characters_without_result": 0,
        "stories_with_result": 0,
        "stories_without_result": 0,
        "summaries": 0,
        "titles": 0,
        "fulls": 0,
        "segments": 0,
        "segment_mp3": 0,
        "data_files": 0,
        "data_bytes": 0,
        "log_files": 0,
        "log_bytes": 0,
        "state": None,
        "state_error": None,
        "languages": None,
    }

    for c in chars:
        cid = c.get("id")
        if not cid:
            continue
        rdir = os.path.join(root, cid, "result")
        if os.path.isdir(rdir) and os.listdir(rdir):
            info["characters_with_result"] += 1
        else:
            info["characters_without_result"] += 1
        for story in c.get("stories") or []:
            sdir = os.path.join(rdir, story)
            if glob.glob(os.path.join(sdir, "seg_*.json")):
                info["stories_with_result"] += 1
            else:
                info["stories_without_result"] += 1

    pattern = os.path.join(root, "*", "result", "*")
    info["summaries"] = len(glob.glob(os.path.join(pattern, "segments.json")))
    info["titles"] = len(glob.glob(os.path.join(pattern, "title.json")))
    info["fulls"] = len(glob.glob(os.path.join(pattern, "full.json")))
    info["segments"] = len(glob.glob(os.path.join(pattern, "seg_*.json")))
    info["segment_mp3"] = len(glob.glob(os.path.join(pattern, "seg_*.mp3")))

    info["data_files"], info["data_bytes"] = _dir_size(os.path.join(home_, "data"))
    info["log_files"], info["log_bytes"] = _dir_size(_io.logs_dir(home_))

    state, err = _read_state(os.path.join(home_, STATE_FILENAME))
    info["state_error"] = err
    if state:
        info["state"] = {
            "completed": len(state.get("completed") or []),
            "updated_at": state.get("updated_at"),
            "signature": state.get("signature"),
        }

    model_path = (settings.rwkv_model_path or "").strip()
    info["rwkv"] = {
        "url": settings.rwkv_api_url,
        "template_present": bool((settings.rwkv_prompt_template or "").strip()),
        "model_path": model_path or None,
        "model_path_exists": bool(model_path and os.path.exists(model_path)),
        "probe": None,
    }
    if probe:
        try:
            info["rwkv"]["probe"] = RwkvTranslator(settings).probe()
        except Exception as exc:  # noqa: BLE001 - 探测失败只报告
            info["rwkv"]["probe"] = {"reachable": False, "error": str(exc)}

    if deep:
        langs: dict[str, int] = {}
        for path in glob.glob(os.path.join(pattern, "seg_*.json")):
            try:
                with open(path, encoding="utf-8") as f:
                    row = json.load(f)
            except (OSError, ValueError):
                continue
            lang = row.get("source_lang") or row.get("source_detected_lang") or "unknown"
            langs[lang] = langs.get(lang, 0) + 1
        info["languages"] = dict(sorted(langs.items(), key=lambda kv: -kv[1]))
    return info


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
    ]
    rwkv_info = info.get("rwkv")
    if rwkv_info:
        model_path = rwkv_info.get("model_path")
        if not model_path:
            model_detail = "未配置 RWKV_MODEL_PATH"
        elif rwkv_info.get("model_path_exists"):
            model_detail = "模型文件存在"
        else:
            model_detail = f"模型文件不存在: {model_path}"
        line = f"本地 RWKV：{rwkv_info.get('url')}（" \
               f"{'模板已配置' if rwkv_info.get('template_present') else '模板为空'}；{model_detail}）"
        probed = rwkv_info.get("probe")
        if probed:
            if probed.get("reachable"):
                line += f"；服务 {probed.get('status') or '未知'}，模型 {probed.get('model') or '未知'}"
            else:
                line += f"；服务不可达：{probed.get('error') or '未知错误'}"
        lines.append(line)
    if info["state"]:
        sig = info["state"].get("signature") or {}
        lines.append(
            f"批量状态：{info['state']['completed']} 条完成；mode={sig.get('mode')} "
            f"chain={sig.get('chain')} steps={sig.get('steps')} provider={sig.get('provider')}；"
            f"更新于 {info['state'].get('updated_at')}")
    elif info["state_error"]:
        lines.append(f"批量状态：读取失败（{info['state_error']}）")
    else:
        lines.append("批量状态：无（全部完成或尚未开始）")
    if info.get("languages"):
        lines.append("源语言分布：" + "，".join(
            f"{k} {v}" for k, v in info["languages"].items()))
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
    _io.atomic_write_json(path, {
        "version": 1,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "characters": info["characters"],
        "stories": info["stories"],
        "stories_with_result": info["stories_with_result"],
        "segments": info["segments"],
        "segment_mp3": info["segment_mp3"],
        "hashes": hashes,
    })
    return path


def run(json_out: bool = False, deep: bool = False, probe: bool = False,
        manifest: bool = False, data_dir: str | None = None) -> ExitCode:
    home_ = _io.home(data_dir)
    if not os.path.exists(_io.char_index_path(home_)):
        log.error("找不到 %s；请先运行 fetch。", _io.char_index_path(home_))
        return ExitCode.USAGE
    info = collect(home_, deep=deep, probe=probe)
    manifest_path = write_manifest(home_, info) if manifest else None
    if json_out:
        print(json.dumps(info, ensure_ascii=False, indent=2))
    else:
        print(render(info))
    if manifest_path:
        print(f"清单已写入：{manifest_path}")
    return ExitCode.OK
