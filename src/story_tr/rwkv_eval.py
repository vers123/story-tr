#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`story-tr rwkv-eval`：用已有云端结果作基线，评测本地 RWKV 一次成型输出。

- 采样：按 `data/character.json` 顺序取角色，每个角色取逐段任务（`seg_NN`），满 `--limit` 即停
- 基线：读取 `{id}/result/{story}/{key}.json` 的 `final`；缺失/损坏则只统计本地侧指标
- 指标：汉字占比、长度比、与基线相似度（difflib）、重复 4-gram 占比、空/异常率、
  平均耗时与字符/秒
- 模板：留空 = 当前配置模板；`builtin` = 4 套内置候选；其它 = 用 `||` 分隔的多套模板
- 报告：`logs/rwkv_eval_<时间戳>.json` 与 `.md`；**不写 `result/`、不改状态文件**

只读取已有结果、不重新跑云端；本地模型仅供本地评测（微调权重许可未知，不得再分发）。
"""
from __future__ import annotations

import difflib
import json
import logging
import os
import re
import sys
import time
import types
from datetime import datetime

from . import paths, rwkv, stories
from .exitcodes import ExitCode

log = logging.getLogger("translate_chain")

BUILTIN_TEMPLATES = (
    "{text}",
    "User: {text}\n\nBot:",
    "User: 把下面这段话翻译20次，然后只输出最终结果：\n{text}\n\nBot:",
    "将下列文本翻译成20种语言再译回中文，只输出译文：\n{text}\n\n",
)

_HAN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_SEG_RE = re.compile(r"^seg_\d+$")
_WS_RE = re.compile(r"\s+")


# --------------------------------------------------------------------------- #
# 模板
# --------------------------------------------------------------------------- #
def parse_templates(spec: str) -> list[str]:
    """解析 `--templates`：空返回 `[]`（表示用当前配置模板），否则按 `builtin` / `||` 展开。"""
    spec = (spec or "").strip()
    if not spec:
        return []
    if spec == "builtin":
        return list(BUILTIN_TEMPLATES)
    return [part for part in spec.split("||") if part.strip()]


# --------------------------------------------------------------------------- #
# 指标
# --------------------------------------------------------------------------- #
def _repeat4(text: str) -> float:
    """重复 4-gram 占比 = 1 - 唯一 4-gram / 总 4-gram（长度 < 4 记 0）。"""
    text = text or ""
    if len(text) < 4:
        return 0.0
    grams = [text[i:i + 4] for i in range(len(text) - 3)]
    return 1.0 - len(set(grams)) / len(grams)


def score_metrics(source: str, final: str, baseline: str = None) -> dict:
    """计算单条样本的指标（纯函数，便于测试）。

    - `zh_ratio`：汉字数 / 非空白字符数
    - `len_ratio`：`len(final) / max(len(source), 1)`
    - `similarity`：与基线的 `difflib` 相似度（无基线时为 None）
    - `repeat4`：重复 4-gram 占比
    """
    source = source or ""
    final = final or ""
    visible = len(_WS_RE.sub("", final))
    han = len(_HAN_RE.findall(final))
    similarity = None
    if baseline:
        similarity = difflib.SequenceMatcher(None, baseline, final).ratio()
    return {
        "zh_ratio": round(han / visible, 4) if visible else 0.0,
        "len_ratio": round(len(final) / max(len(source), 1), 4),
        "similarity": round(similarity, 4) if similarity is not None else None,
        "repeat4": round(_repeat4(final), 4),
    }


def _avg(values):
    values = list(values)
    return round(sum(values) / len(values), 4) if values else 0.0


def summarize(records, baseline_missing: int = 0) -> dict:
    """把逐条记录汇总成模板级指标。"""
    samples = len(records)
    ok = [r for r in records if r.get("ok")]
    failed = samples - len(ok)
    empty = sum(1 for r in ok if not r.get("final"))
    elapsed = [int(r.get("elapsed_ms") or 0) for r in records]
    total_ms = sum(elapsed)
    total_chars = sum(len(r.get("final") or "") for r in ok)
    sims = [r["similarity"] for r in ok if r.get("similarity") is not None]
    return {
        "samples": samples,
        "ok": len(ok),
        "failed": failed,
        "baseline_missing": baseline_missing,
        "empty_rate": round((empty + failed) / samples, 4) if samples else 0.0,
        "avg_latency_ms": int(round(total_ms / len(elapsed))) if elapsed else 0,
        "avg_zh_ratio": _avg(r["zh_ratio"] for r in ok),
        "avg_len_ratio": _avg(r["len_ratio"] for r in ok),
        "avg_similarity": _avg(sims) if sims else None,
        "avg_repeat4": _avg(r["repeat4"] for r in ok),
        "chars_per_sec": round(total_chars / (total_ms / 1000.0), 3) if total_ms > 0 else 0.0,
    }


def score_of(metrics: dict) -> float:
    """模板排序分数（仅用于推荐）：有基线偏重相似度，无基线看本地侧质量。"""
    zh = min(max(metrics.get("avg_zh_ratio") or 0.0, 0.0), 1.0)
    len_pen = 1.0 - min(abs((metrics.get("avg_len_ratio") or 0.0) - 1.0), 1.0)
    rep = 1.0 - min(max(metrics.get("avg_repeat4") or 0.0, 0.0), 1.0)
    similarity = metrics.get("avg_similarity")
    if similarity is not None:
        score = 0.5 * similarity + 0.2 * zh + 0.2 * len_pen + 0.1 * rep
    else:
        score = 0.5 * zh + 0.3 * len_pen + 0.2 * rep
    samples = metrics.get("samples") or 0
    failed = metrics.get("failed") or 0
    if failed and samples:
        score -= 0.2 * failed / samples
    return round(max(0.0, score), 4)


# --------------------------------------------------------------------------- #
# 采样与基线
# --------------------------------------------------------------------------- #
def _read_baseline(char_id: str, story: str, key: str):
    """读取云端基线 `final`；文件不存在 / JSON 损坏 / 无 final 均返回 None。"""
    path = os.path.join(stories.story_dir(char_id), story, key + ".json")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    final = data.get("final") if isinstance(data, dict) else None
    return final if isinstance(final, str) else None


def sample_items(char_ids, limit: int, story_names: str = "", logger=None) -> list[dict]:
    """按角色/故事顺序采样逐段任务，凑满 `limit` 条即停；只保留 `seg_NN`（排除标题/全文）。"""
    logger = logger or log
    limit = max(0, int(limit))
    if limit <= 0:
        return []
    names = [s.strip() for s in (story_names or "").split(",") if s.strip()]
    selector = types.SimpleNamespace(stories=story_names or "", all=not names)
    items = []
    for char_id in char_ids:
        if len(items) >= limit:
            break
        selected = stories.resolve_stories(selector, char_id)
        if not selected:
            continue
        base = os.path.join(stories.STORY_ROOT, char_id, "profile", "story")
        for task in stories.build_tasks(base, char_id, selected, logger, parts="segments"):
            if len(items) >= limit:
                break
            if not _SEG_RE.match(task.get("key") or ""):
                continue
            items.append({
                "char_id": char_id,
                "story": task["story"],
                "key": task["key"],
                "text": task["text"],
                "baseline": _read_baseline(char_id, task["story"], task["key"]),
            })
    return items


# --------------------------------------------------------------------------- #
# 评测
# --------------------------------------------------------------------------- #
def evaluate_template(template: str, items, cfg) -> tuple[list, list]:
    """对一套模板逐条生成并计算指标；单条失败只记录、不中断其它样本。"""
    records, errors = [], []
    for index, item in enumerate(items):
        started = time.monotonic()
        try:
            result = rwkv.translate(item["text"], cfg, template=template)
        except rwkv.RwkvError as exc:
            elapsed = int(round((time.monotonic() - started) * 1000))
            records.append({"ok": False, "elapsed_ms": elapsed})
            errors.append({
                "sample": "%s/%s/%s" % (item["char_id"], item["story"], item["key"]),
                "error": type(exc).__name__,
                "message": str(exc),
            })
            if index == 0 and isinstance(exc, (rwkv.RwkvUnavailable, rwkv.RwkvModelOffline)):
                # 第一条就连不上 / 没模型：后续样本也必然失败，提前结束该模板
                break
            continue
        elapsed = result.get("elapsed_ms")
        if not isinstance(elapsed, int):
            elapsed = int(round((time.monotonic() - started) * 1000))
        final = (result.get("text") or "").strip()
        record = {"ok": True, "elapsed_ms": elapsed, "final": final}
        record.update(score_metrics(item["text"], final, item["baseline"]))
        records.append(record)
    return records, errors


def exit_code_for(results) -> int:
    """正常即有样本成功 → 0；全部模板都因同一类连接问题失败 → 5（不可用）/ 4（模型未加载）。"""
    if not results:
        return ExitCode.USAGE
    if any(r["metrics"].get("ok") for r in results):
        return ExitCode.OK
    kinds = {e["error"] for r in results for e in r["errors"]}
    if kinds and kinds <= {"RwkvUnavailable"}:
        return ExitCode.CONNECTION
    if kinds and kinds <= {"RwkvModelOffline"}:
        return ExitCode.PAUSED
    return ExitCode.OK


def _resolve_char_ids(args):
    all_ids = stories.load_char_ids()
    if getattr(args, "all_characters", False):
        return all_ids
    tokens = list(getattr(args, "char_ids", []) or [])
    if not tokens:
        return all_ids  # 默认按 character.json 顺序取，直到采满 --limit
    return stories.parse_char_ids(tokens, all_ids)


# --------------------------------------------------------------------------- #
# 输出
# --------------------------------------------------------------------------- #
def _one_line(template: str, width: int = 40) -> str:
    text = (template or "").replace("\r\n", "\n").replace("\n", "⏎").strip()
    return text if len(text) <= width else text[:width - 1] + "…"


def _fmt(value, pattern: str) -> str:
    return pattern % value if value is not None else "—"


def _table_row(index: int, row: dict) -> str:
    m = row["metrics"]
    cells = [
        str(index), _one_line(row["template"]), "%.3f" % row["score"],
        _fmt(m.get("avg_similarity"), "%.3f"),
        "%.2f" % (m.get("avg_len_ratio") or 0.0),
        "%.2f" % (m.get("avg_zh_ratio") or 0.0),
        "%.2f" % (m.get("avg_repeat4") or 0.0),
        str(m.get("failed", 0)),
        "%.0fms" % (m.get("avg_latency_ms") or 0),
    ]
    return " | ".join(cells)


def render_markdown(payload: dict, ranked) -> str:
    lines = [
        "# RWKV 本地评测报告", "",
        "- 生成时间：%s" % payload["generated_at"],
        "- 样本数：%d" % payload["samples"],
        "- 基线缺失：%d" % payload["baseline_missing"],
        "- 本地服务：%s" % payload["rwkv"]["url"],
        "- 配置模板摘要：`%s`" % payload["rwkv"]["template_sha1"], "",
        "## 模板排名", "",
        "| 排名 | 模板 | 分数 | 相似度 | 长度比 | 汉字比 | 重复率 | 失败 | 平均耗时 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for index, row in enumerate(ranked, 1):
        m = row["metrics"]
        template = _one_line(row["template"]).replace("|", "\\|")
        lines.append("| %d | `%s` | %.3f | %s | %.2f | %.2f | %.2f | %d | %.0f ms |" % (
            index, template, row["score"], _fmt(m.get("avg_similarity"), "%.3f"),
            m.get("avg_len_ratio") or 0.0, m.get("avg_zh_ratio") or 0.0,
            m.get("avg_repeat4") or 0.0, m.get("failed", 0), m.get("avg_latency_ms") or 0))
    lines += [
        "", "推荐模板：`%s`" % _one_line(ranked[0]["template"]).replace("|", "\\|"), "",
        "## 注意事项", "",
        "- 本地模型仅供本地使用；微调权重许可未知，不得再分发。",
        "- 相似度只在与云端基线对齐的样本上统计；基线缺失时仅比较本地侧指标。",
        "- 本报告只读取已有结果，未重新运行云端链路，也未修改 `result/` 与状态文件。",
    ]
    return "\n".join(lines) + "\n"


def write_report(out_dir: str, payload: dict, markdown: str):
    """写出 JSON + Markdown 报告，返回两个路径（目录不存在时自动创建）。"""
    os.makedirs(out_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = os.path.join(out_dir, "rwkv_eval_%s.json" % stamp)
    md_path = os.path.join(out_dir, "rwkv_eval_%s.md" % stamp)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(markdown)
    return json_path, md_path


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def run_eval(args) -> int:
    """评测主流程（CLI 与测试共用）；返回 `ExitCode`。"""
    limit = int(getattr(args, "limit", 20) or 0)
    if limit < 1:
        print("--limit 必须 ≥ 1。")
        return ExitCode.USAGE
    char_ids = _resolve_char_ids(args)
    if not char_ids:
        print("没有匹配到任何角色 id；请对照 data/character.json 检查。")
        return ExitCode.USAGE
    items = sample_items(char_ids, limit, getattr(args, "stories", "") or "")
    if not items:
        print("没有采样到任何段落：所选角色 / 故事的 zh.md 没有正文。"
              "请检查 char_ids 与 --stories。")
        return ExitCode.USAGE

    cfg = rwkv.from_args(types.SimpleNamespace(
        rwkv_url=getattr(args, "rwkv_url", "") or None,
        rwkv_template=getattr(args, "rwkv_template", "") or None))
    templates = parse_templates(getattr(args, "templates", "") or "") or [cfg.template]
    baseline_missing = sum(1 for item in items if item["baseline"] is None)

    results = []
    for template in templates:
        records, errors = evaluate_template(template, items, cfg)
        metrics = summarize(records, baseline_missing)
        results.append({"template": template, "score": score_of(metrics),
                        "metrics": metrics, "errors": errors})
    ranked = sorted(results, key=lambda r: r["score"], reverse=True)

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "limit": limit,
        "samples": len(items),
        "baseline_missing": baseline_missing,
        "rwkv": {"url": cfg.url, "template_sha1": rwkv.signature(cfg)["template_sha1"]},
        "results": ranked,
    }
    out_dir = getattr(args, "out", "") or os.path.join(paths.home(), "logs")
    reports = None
    try:
        reports = write_report(out_dir, payload, render_markdown(payload, ranked))
    except OSError as exc:  # 报告写失败不影响返回码
        print("报告写入失败（%s）：%s" % (out_dir, exc), file=sys.stderr)

    print("样本 %d 段（基线缺失 %d 段），服务 %s" % (len(items), baseline_missing, cfg.url))
    print("序号 | 模板 | 分数 | 相似度 | 长度比 | 汉字比 | 重复率 | 失败 | 平均耗时")
    for index, row in enumerate(ranked, 1):
        print(_table_row(index, row))
    print("推荐模板：%s" % _one_line(ranked[0]["template"]))
    if reports:
        print("报告：%s" % reports[0])
        print("      %s" % reports[1])

    code = exit_code_for(ranked)
    if code == ExitCode.CONNECTION:
        print("所有模板均连接本地服务失败：请启动 RWKV-Runner 或检查 --rwkv-url（当前 %s）。"
              % cfg.url)
    elif code == ExitCode.PAUSED:
        print("所有模板均因模型未加载失败：请先在 RWKV-Runner 中加载 translate-20times-3B.pth。")
    return code


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def add_parser(sub):
    """注册 `story-tr rwkv-eval` 子命令。"""
    p = sub.add_parser(
        "rwkv-eval",
        help="用已有云端结果作基线，评测本地 RWKV 一次成型输出并探测提示词模板",
        parents=[paths.data_dir_parent()],
        epilog="示例：story-tr rwkv-eval --all-characters --templates builtin   |   "
               "story-tr rwkv-eval 10000021 --stories vision --limit 20",
    )
    p.add_argument("char_ids", nargs="*",
                   help="角色 id / 范围（空格或逗号分隔）：10000021 / 10000002-10000030；"
                        "不指定时按 character.json 顺序采样")

    g = p.add_argument_group("采样")
    g.add_argument("--all-characters", action="store_true",
                   help="按 data/character.json 顺序采样全部角色（直到采满 --limit）")
    g.add_argument("--stories", default="",
                   help="只评测指定故事文件夹，逗号分隔（不指定 = 全部故事）")
    g.add_argument("--limit", type=int, default=20, help="最多采样段落数（默认 20）")

    g = p.add_argument_group("模板探测")
    g.add_argument("--templates", default="",
                   help="builtin（4 套内置）或自定义模板用 || 分隔；留空 = 只用当前配置模板")

    g = p.add_argument_group("本地服务")
    g.add_argument("--rwkv-url", default="", help="覆盖 RWKV_API_URL（默认 http://127.0.0.1:8000）")
    g.add_argument("--rwkv-template", default="", help="覆盖 RWKV_PROMPT_TEMPLATE")

    g = p.add_argument_group("输出")
    g.add_argument("--out", default="", help="报告目录（默认 <工作目录>/logs）")

    p.set_defaults(func=main)
    return p


def main(args) -> int:
    rwkv.load_env()
    return run_eval(args)
