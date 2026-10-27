# -*- coding: utf-8 -*-
"""`story-tr status`：只读报告工作目录、数据覆盖、结果/语音/视频与批量进度。

默认不联网、不修改任何文件（状态文件损坏时只报告，不自动修复）；
只有显式 `--probe` 才访问本地 RWKV 服务（`/status`、`/v1/models`）。
"""
from __future__ import annotations

import glob
import json
import os
from datetime import datetime

from . import paths, rwkv, statefile, stories


def _dir_size(path: str):
    """返回 (文件数, 字节数)；目录不存在时返回 (0, 0)。"""
    files = size = 0
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
    return "%.1f GB" % value


def _read_state(path: str):
    """只读读取状态文件；损坏时返回 (None, 错误说明)。"""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f), None
    except FileNotFoundError:
        return None, None
    except (OSError, json.JSONDecodeError) as exc:
        return None, str(exc)


def collect(deep: bool = False, probe: bool = False) -> dict:
    """收集状态；`deep=True` 时额外扫描每个 seg JSON 的语言分布。

    `probe=False`（默认）不联网；`probe=True` 时额外访问本地 RWKV 服务。
    """
    home = paths.home()
    rwkv.load_env(home)
    char_file = stories.CHAR_FILE
    chars = []
    if os.path.exists(char_file):
        with open(char_file, encoding="utf-8") as f:
            chars = json.load(f)

    root = stories.STORY_ROOT
    info = {
        "workdir": home,
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
        "videos": 0,
        "video_bytes": 0,
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
    info["videos"] = len(glob.glob(os.path.join(root, "*", "result", "video", "*.mp4")))
    for vdir in glob.glob(os.path.join(root, "*", "result", "video")):
        info["video_bytes"] += _dir_size(vdir)[1]

    info["data_files"], info["data_bytes"] = _dir_size(os.path.join(home, "data"))
    info["log_files"], info["log_bytes"] = _dir_size(os.path.join(home, "logs"))

    state, err = _read_state(stories.STATE_PATH)
    info["state_error"] = err
    if state:
        info["state"] = {
            "completed": len(state.get("completed") or []),
            "updated_at": state.get("updated_at"),
            "signature": state.get("signature"),
        }

    cfg = rwkv.from_env()
    model_path = os.environ.get("RWKV_MODEL_PATH", "").strip()
    info["rwkv"] = {
        "url": cfg.url,
        "template_present": bool((cfg.template or "").strip()),
        "model_path": model_path or None,
        "model_path_exists": bool(model_path and os.path.exists(model_path)),
        "probe": None,
    }
    if probe:
        try:
            info["rwkv"]["probe"] = rwkv.probe(cfg)
        except Exception as exc:  # noqa: BLE001 - 探测失败只报告，不中断 status
            info["rwkv"]["probe"] = {"reachable": False, "error": str(exc)}

    if deep:
        langs = {}
        for path in glob.glob(os.path.join(pattern, "seg_*.json")):
            try:
                with open(path, encoding="utf-8") as f:
                    row = json.load(f)
            except (OSError, ValueError):
                continue
            lang = row.get("source_detected_lang") or "unknown"
            langs[lang] = langs.get(lang, 0) + 1
        info["languages"] = dict(sorted(langs.items(), key=lambda kv: -kv[1]))
    return info


def render(info: dict) -> str:
    """把状态渲染成人类可读文本。"""
    lines = [
        "工作目录：%s" % info["workdir"],
        "角色：%d（有结果 %d / 无结果 %d）"
        % (info["characters"], info["characters_with_result"],
           info["characters_without_result"]),
        "故事：%d（有结果 %d / 无结果 %d）；summary %d / title %d / full %d"
        % (info["stories"], info["stories_with_result"], info["stories_without_result"],
           info["summaries"], info["titles"], info["fulls"]),
        "段落结果：%d 个 JSON；语音 %d 个（%.1f%%）"
        % (info["segments"], info["segment_mp3"],
           (100.0 * info["segment_mp3"] / info["segments"]) if info["segments"] else 0.0),
        "视频：%d 个；%s" % (info["videos"], _human(info["video_bytes"])),
        "磁盘：data %s（%d 文件）；logs %s（%d 文件）"
        % (_human(info["data_bytes"]), info["data_files"],
           _human(info["log_bytes"]), info["log_files"]),
    ]
    rwkv_info = info.get("rwkv")
    if rwkv_info:
        model_path = rwkv_info.get("model_path")
        if not model_path:
            model_detail = "未配置 RWKV_MODEL_PATH"
        elif rwkv_info.get("model_path_exists"):
            model_detail = "模型文件存在"
        else:
            model_detail = "模型文件不存在: %s" % model_path
        line = "本地 RWKV：%s（%s；%s）" % (
            rwkv_info.get("url"),
            "模板已配置" if rwkv_info.get("template_present") else "模板为空",
            model_detail)
        probed = rwkv_info.get("probe")
        if probed:
            if probed.get("reachable"):
                line += "；服务 %s，模型 %s" % (probed.get("status") or "未知",
                                               probed.get("model") or "未知")
            else:
                line += "；服务不可达：%s" % (probed.get("error") or "未知错误")
        lines.append(line)
    if info["state"]:
        sig = info["state"].get("signature") or {}
        lines.append("批量状态：%d 条完成；mode=%s chain=%s steps=%s parts=%s provider=%s；更新于 %s"
                     % (info["state"]["completed"], sig.get("mode"), sig.get("chain"),
                        sig.get("steps"), sig.get("parts"), sig.get("provider"),
                        info["state"].get("updated_at")))
    elif info["state_error"]:
        lines.append("批量状态：读取失败（%s）" % info["state_error"])
    else:
        lines.append("批量状态：无（全部完成或尚未开始）")
    if info.get("languages"):
        lines.append("源语言分布：" + "，".join(
            "%s %d" % (k, v) for k, v in info["languages"].items()))
    return "\n".join(lines)


def write_manifest(info=None) -> str:
    """把状态与抓取缓存 hash 写入 `data/manifest.json`，返回路径。"""
    info = info or collect()
    hashes = {}
    cache_path = os.path.join(paths.home(), "data", "_fetch_all.json")
    try:
        with open(cache_path, encoding="utf-8") as f:
            for rec in json.load(f):
                if rec.get("id") and rec.get("hash"):
                    hashes[rec["id"]] = rec["hash"]
    except (OSError, ValueError):
        pass
    path = os.path.join(paths.home(), "data", "manifest.json")
    statefile.atomic_write_json(path, {
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


def add_parser(sub):
    """注册 `story-tr status` 子命令。"""
    p = sub.add_parser("status", help="只读状态报告（角色/故事/结果/磁盘/批量进度）",
                       parents=[paths.data_dir_parent()])
    p.add_argument("--json", action="store_true", help="输出 JSON（便于脚本处理）")
    p.add_argument("--deep", action="store_true",
                   help="额外扫描段落语言分布（较慢，需读取全部 seg JSON）")
    p.add_argument("--manifest", action="store_true",
                   help="把角色/故事/结果统计与抓取 hash 写入 data/manifest.json")
    p.add_argument("--probe", action="store_true",
                   help="额外访问本地 RWKV 服务，默认不联网")
    p.set_defaults(func=main)
    return p


def main(args) -> int:
    if not os.path.exists(stories.CHAR_FILE):
        print("找不到 %s；请先运行 story-tr fetch（或检查 --data-dir）。"
              % stories.CHAR_FILE)
        return 1
    info = collect(deep=getattr(args, "deep", False),
                   probe=getattr(args, "probe", False))
    manifest_path = write_manifest(info) if getattr(args, "manifest", False) else None
    if getattr(args, "json", False):
        print(json.dumps(info, ensure_ascii=False, indent=2))
    else:
        print(render(info))
    if manifest_path:
        print("清单已写入：%s" % manifest_path)
    return 0
