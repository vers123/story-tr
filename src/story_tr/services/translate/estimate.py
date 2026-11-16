"""`translate --plan`：只读估算任务量、请求数与状态一致性。

不联网、不写文件：状态文件用纯 `json.load` 读取（不会触发 `.corrupt-*` 写入），
RWKV 只输出配置，实际可达性请用 `doctor --probe` / `status --probe`。
"""

from __future__ import annotations

import json
import math
import os
from typing import Any

from story_tr import models
from story_tr.core import has_features, signatures_match
from story_tr.services import storage

from .. import status_scan
from . import failures as failures_mod
from . import runner
from . import tasks as tasks_mod

TARGET_ZH = "zh-CN"
BATCH_MAX_CHARS = 1500


def read_state(home_: str) -> models.BatchState | None:
    """纯读批次状态；缺失 / 损坏返回 `None`（不写任何文件）。"""
    path = os.path.join(home_, runner.STATE_FILENAME)
    try:
        with open(path, encoding="utf-8") as f:
            return models.BatchState.model_validate(json.load(f))
    except (OSError, ValueError):
        return None


def _iter_tasks(plan: runner.Plan) -> Any:
    """枚举选中角色的全部任务，产出 `(cid, task)`。"""
    for cid in plan.selected:
        names = tasks_mod.story_names(plan.home, cid, plan.stories, plan.src_file)
        if not names:
            continue
        for task in tasks_mod.build_tasks(plan.home, cid, names, plan.parts, plan.src_file):
            yield cid, task


def _task_kind(key: str) -> str:
    if key == "title":
        return "title"
    if key == "full":
        return "full"
    return "segments"


def _result_path(plan: runner.Plan, cid: str, story: str, key: str) -> str:
    return os.path.join(storage.result_dir(plan.home, cid, plan.result_tag), story, key + ".json")


def _batch_enabled(plan: runner.Plan) -> bool:
    return bool(plan.batch and plan.mode != "rwkv20" and "google" in plan.providers)


def _batch_requests(plan: runner.Plan, texts: list[str]) -> int:
    if plan.mode == "rwkv20":
        return len(texts)
    steps = len(plan.langs) + 2  # 中文源：to_en + N 步 + to_zh
    return steps * max(1, math.ceil(sum(len(t) for t in texts) / BATCH_MAX_CHARS))


def _per_task_requests(plan: runner.Plan) -> int:
    if plan.mode == "rwkv20":
        return 1
    return len(plan.langs) + 2


def _check_mismatch(plan: runner.Plan, json_path: str) -> bool:
    row = runner.read_result_row(json_path) if os.path.exists(json_path) else None
    return bool(row and (row.get("mode") != plan.mode or row.get("chain") != plan.chain_label))


def _wanted_keys(story_dir: str, parts: str) -> list[str]:
    keys = ["title"] if os.path.exists(os.path.join(story_dir, "title.json")) else []
    if parts in ("both", "full") and os.path.exists(os.path.join(story_dir, "full.json")):
        keys.append("full")
    if parts in ("both", "segments"):
        keys += [
            name[:-5]
            for name in sorted(os.listdir(story_dir))
            if name.startswith("seg_") and name.endswith(".json")
        ]
    return keys


def _tts_jobs(plan: runner.Plan) -> int:
    """统计待补语音份数（JSON 存在而 mp3 缺失；`--overwrite` 时全部计入）。"""
    jobs = 0
    wanted = [s.strip() for s in plan.stories.split(",") if s.strip()]
    for cid in plan.selected:
        rdir = storage.result_dir(plan.home, cid, plan.result_tag)
        if not os.path.isdir(rdir):
            continue
        story_names = wanted or sorted(
            name for name in os.listdir(rdir) if os.path.isdir(os.path.join(rdir, name))
        )
        for story in story_names:
            story_dir = os.path.join(rdir, story)
            for key in _wanted_keys(story_dir, plan.parts):
                missing = not os.path.exists(os.path.join(story_dir, key + ".mp3"))
                jobs += int(plan.overwrite or missing)
    return jobs


def _rwkv_info(plan: runner.Plan) -> dict[str, Any]:
    model_path = (plan.settings.rwkv_model_path or "").strip()
    return {
        "url": plan.settings.rwkv_api_url,
        "model_path": model_path or None,
        "model_path_exists": bool(model_path and os.path.exists(model_path)),
    }


def estimate(plan: runner.Plan, tts_only: bool = False) -> dict[str, Any]:
    """只读估算：任务拆分、跳过/待跑、请求数、状态与参数一致性。"""
    state = read_state(plan.home)
    completed = set(state.completed) if state else set()
    signature_match = state is None or signatures_match(state.signature, runner.plan_signature(plan))
    counts = {"title": 0, "full": 0, "segments": 0, "passthrough": 0, "existing": 0, "todo": 0}
    stories: set[tuple[str, str]] = set()
    batch_texts: dict[tuple[str, str], list[str]] = {}
    per_task_requests = 0
    mismatch = 0
    for cid, task in _iter_tasks(plan):
        stories.add((cid, task["story"]))
        counts[_task_kind(task["key"])] += 1
        json_path = _result_path(plan, cid, task["story"], task["key"])
        state_key = f"{cid}|{task['story']}|{task['key']}"
        skip, _reason = runner.should_skip_task(plan, completed, json_path, state_key, task)
        if skip:
            counts["existing"] += 1
            mismatch += int(_check_mismatch(plan, json_path))
            continue
        counts["todo"] += 1
        if not has_features(task["text"]):
            counts["passthrough"] += 1
        elif _batch_enabled(plan):
            batch_texts.setdefault((cid, task["story"]), []).append(task["text"])
        else:
            per_task_requests += _per_task_requests(plan)

    requests = sum(_batch_requests(plan, texts) for texts in batch_texts.values())
    requests += per_task_requests
    scanned = status_scan.scan_results(storage.story_root(plan.home), tag=plan.result_tag)["counts"]
    disk_tasks = scanned["titles"] + scanned["fulls"] + scanned["segments"]
    registry = failures_mod.read_registry(plan.home)
    min_interval = max(0.0, float(plan.min_interval))
    return {
        "workdir": plan.home,
        "result_tag": plan.result_tag,
        "characters": len(plan.selected),
        "stories": len(stories),
        "tasks": counts,
        "mode": plan.mode,
        "chain": plan.chain_label,
        "providers": plan.providers,
        "languages": len(plan.langs),
        "batch": _batch_enabled(plan),
        "requests": requests,
        "requests_per_task": _per_task_requests(plan),
        "lower_bound_seconds": requests * min_interval,
        "state_completed": len(completed),
        "disk_tasks": disk_tasks,
        "signature_match": signature_match,
        "param_mismatch": mismatch,
        "pending_failures": len(registry.failures) if registry else 0,
        "tts_jobs": _tts_jobs(plan) if tts_only else None,
        "rwkv": _rwkv_info(plan),
    }


def _hours(seconds: float) -> str:
    if seconds <= 0:
        return "—"
    return f"{seconds / 3600:.1f} 小时"


def _state_text(info: dict[str, Any]) -> str:
    if info["signature_match"]:
        return f"批量状态：{info['state_completed']} 条记录 / 磁盘 {info['disk_tasks']} 份结果（签名匹配）"
    return (
        f"批量状态：{info['state_completed']} 条记录 / 磁盘 {info['disk_tasks']} 份结果"
        "（签名不一致：实际运行会以退出码 3 结束，需 --reset）"
    )


def render(info: dict[str, Any]) -> str:
    """把估算结果渲染为人类可读文本。"""
    tasks = info["tasks"]
    dir_name = "result" if not info["result_tag"] else "result-" + info["result_tag"]
    lines = [
        f"工作目录：{info['workdir']}",
        f"结果目录：{dir_name}",
        f"选择：{info['characters']} 个角色 / {info['stories']} 个故事",
        "任务：title {title} / full {full} / 段 {segments}；已存在跳过 {existing}，"
        "待跑 {todo}（其中 passthrough {passthrough}）".format(**tasks),
        "路线：{}，后端 {}，中间语言 {}；批量模式：{}".format(
            info["mode"] + ("/" + info["chain"] if info["mode"] == "fixed" else ""),
            " → ".join(info["providers"]),
            info["languages"],
            "开" if info["batch"] else "关",
        ),
        f"预计请求：{info['requests']} 次（"
        f"{'批量模式按每步打包估算' if info['batch'] else '逐条模式估算'}）；"
        f"按最小间隔理论下限 {_hours(info['lower_bound_seconds'])}（实际受延迟/限流影响）",
        _state_text(info),
    ]
    if info["param_mismatch"]:
        lines.append(f"已有结果参数不一致：{info['param_mismatch']} 份（继续会跳过；需重译请 --overwrite）")
    if info["pending_failures"]:
        lines.append(f"遗留失败登记：{info['pending_failures']} 条（不会跨运行自动重试）")
    if info["tts_jobs"] is not None:
        lines.append(f"待补语音：{info['tts_jobs']} 份")
    rwkv = info["rwkv"]
    model = "模型文件存在" if rwkv["model_path_exists"] else "模型文件不存在/未配置"
    lines.append(f"本地 RWKV：{rwkv['url']}（{model}；可达性请用 doctor --probe）")
    lines.append("（只读估算，未写任何文件）")
    return "\n".join(lines)
