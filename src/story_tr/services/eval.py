"""`story-tr eval`：简化版本地评测（替代旧 rwkv-eval）。

- 采样 `seg_*` 段落（复用 `core.segment` 与 `models`），满 `--limit` 即停
- 用 `transport.rwkv.RwkvTranslator` 按多套模板生成，用 `core.metrics` 算指标并排名
- 报告写 `logs/rwkv_eval_{ts}.json|md`；不写 `result/`、不改状态文件
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from datetime import datetime
from typing import Any

from story_tr.config import Settings, load_settings
from story_tr.core import (
    len_ratio,
    parse_frontmatter,
    repeat4,
    score,
    similarity,
    split_paragraphs,
    zh_ratio,
)
from story_tr.errors import BadOutput, ConnectionFailed, ExitCode, ModelNotLoaded
from story_tr.transport.rwkv import RwkvTranslator

from . import _io

log = logging.getLogger("story_tr.services.eval")

TARGET_ZH = "zh-CN"

BUILTIN_TEMPLATES = (
    "{text}",
    "User: {text}\n\nBot:",
    "Translate this into Chinese.\n\nEnglish: {text}\nChinese:",
    "User: 把下面这段话翻译20次，然后只输出最终结果：\n{text}\n\nBot:",
    "将下列文本翻译成20种语言再译回中文，只输出译文：\n{text}\n\n",
)


def _parse_templates(spec: str) -> list[str]:
    spec = (spec or "").strip()
    if not spec:
        return []
    if spec == "builtin":
        return list(BUILTIN_TEMPLATES)
    return [part for part in spec.split("||") if part.strip()]


def _read_paragraphs(md_path: str) -> list[str]:
    with open(md_path, encoding="utf-8") as f:
        raw = f.read()
    _, body = parse_frontmatter(raw)
    return split_paragraphs(body)


def _resolve_stories(home_: str, cid: str, names: list[str] | None) -> list[str]:
    base = _io.profile_story_dir(home_, cid)
    if names:
        return names
    if not os.path.isdir(base):
        return []
    out: list[str] = []
    for n in sorted(os.listdir(base)):
        if os.path.isdir(os.path.join(base, n)):
            md = os.path.join(base, n, "zh.md")
            if os.path.exists(md) and _read_paragraphs(md):
                out.append(n)
    return out


def _read_baseline(home_: str, cid: str, story: str, key: str) -> str | None:
    path = os.path.join(_io.result_dir(home_, cid), story, key + ".json")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    final = data.get("final") if isinstance(data, dict) else None
    return final if isinstance(final, str) else None


def _sample(home_: str, char_ids: list[str], stories: str, limit: int) -> list[dict[str, Any]]:
    limit = max(0, int(limit))
    if limit <= 0:
        return []
    names = [s.strip() for s in stories.split(",") if s.strip()] or None
    items: list[dict[str, Any]] = []
    for cid in char_ids:
        if len(items) >= limit:
            break
        for story in _resolve_stories(home_, cid, names):
            md = os.path.join(_io.profile_story_dir(home_, cid), story, "zh.md")
            for i, para in enumerate(_read_paragraphs(md), 1):
                if len(items) >= limit:
                    break
                key = f"seg_{i:02d}"
                items.append({"char_id": cid, "story": story, "key": key,
                              "text": para, "baseline": _read_baseline(home_, cid, story, key)})
    return items


def _metrics(source: str, final: str, baseline: str | None) -> dict[str, float | None]:
    return {
        "zh": zh_ratio(final),
        "len": len_ratio(final, source),
        "sim": similarity(final, baseline) if baseline else None,
        "rep": repeat4(final),
    }


def _mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 4) if values else 0.0


def _summarize(records: list[dict[str, Any]], baseline_missing: int) -> dict[str, Any]:
    samples = len(records)
    ok = [r for r in records if r.get("ok")]
    failed = samples - len(ok)
    elapsed = [int(r.get("elapsed_ms") or 0) for r in records]
    total_ms = sum(elapsed)
    total_chars = sum(len(r.get("final") or "") for r in ok)
    sims = [r["metrics"]["sim"] for r in ok if r["metrics"].get("sim") is not None]
    m: dict[str, Any] = {
        "samples": samples, "ok": len(ok), "failed": failed,
        "baseline_missing": baseline_missing,
        "avg_latency_ms": int(round(total_ms / len(elapsed))) if elapsed else 0,
        "avg_zh": _mean([r["metrics"]["zh"] for r in ok]),
        "avg_len": _mean([r["metrics"]["len"] for r in ok]),
        "avg_sim": _mean([s for s in sims]) if sims else None,
        "avg_rep": _mean([r["metrics"]["rep"] for r in ok]),
        "chars_per_sec": round(total_chars / (total_ms / 1000.0), 3) if total_ms > 0 else 0.0,
    }
    return m


def _score(m: dict[str, Any]) -> float:
    has_baseline = m.get("avg_sim") is not None
    metrics: dict[str, float] = {"zh": m["avg_zh"], "len": m["avg_len"], "rep": m["avg_rep"]}
    if m.get("avg_sim") is not None:
        metrics["sim"] = float(m["avg_sim"])
    return round(score(metrics, has_baseline), 4)


def _evaluate(template: str, items: list[dict[str, Any]],
              settings: Settings) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    overrides: dict[str, Any] = {"rwkv_prompt_template": template}
    ts = settings.model_copy(update=overrides)
    translator = RwkvTranslator(ts)
    records: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for item in items:
        started = time.monotonic()
        try:
            final = translator.translate(item["text"], "zh-CN", TARGET_ZH)
        except ModelNotLoaded as exc:
            records.append({"ok": False, "elapsed_ms": int((time.monotonic() - started) * 1000)})
            errors.append({"sample": "{}/{}/{}".format(item["char_id"], item["story"], item["key"]),
                           "kind": "model_not_loaded", "error": str(exc)})
            if not any(r.get("ok") for r in records):
                break  # 第一条就模型未加载，后续样本必然失败
            continue
        except ConnectionFailed as exc:
            records.append({"ok": False, "elapsed_ms": int((time.monotonic() - started) * 1000)})
            errors.append({"sample": "{}/{}/{}".format(item["char_id"], item["story"], item["key"]),
                           "kind": "connection", "error": str(exc)})
            if not any(r.get("ok") for r in records):
                break
            continue
        except BadOutput as exc:
            records.append({"ok": False, "elapsed_ms": int((time.monotonic() - started) * 1000)})
            errors.append({"sample": "{}/{}/{}".format(item["char_id"], item["story"], item["key"]),
                           "kind": "bad_output", "error": str(exc)})
            continue
        elapsed = int((time.monotonic() - started) * 1000)
        final = (final or "").strip()
        records.append({"ok": True, "elapsed_ms": elapsed, "final": final,
                        "metrics": _metrics(item["text"], final, item["baseline"])})
    return records, errors


def _one_line(template: str, width: int = 40) -> str:
    text = (template or "").replace("\r\n", "\n").replace("\n", "⏎").strip()
    return text if len(text) <= width else text[:width - 1] + "…"


def _render_markdown(payload: dict[str, Any], ranked: list[dict[str, Any]]) -> str:
    lines = [
        "# RWKV 本地评测报告", "",
        f"- 生成时间：{payload['generated_at']}",
        f"- 样本数：{payload['samples']}",
        f"- 基线缺失：{payload['baseline_missing']}",
        f"- 本地服务：{payload['rwkv']['url']}", "",
        "## 模板排名", "",
        "| 排名 | 模板 | 分数 | 相似度 | 长度比 | 汉字比 | 重复率 | 失败 | 平均耗时 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for index, row in enumerate(ranked, 1):
        m = row["metrics"]
        template = _one_line(row["template"]).replace("|", "\\|")
        sim = f"{m['avg_sim']:.3f}" if m.get("avg_sim") is not None else "—"
        lines.append(
            f"| {index} | `{template}` | {row['score']:.3f} | {sim} | {m['avg_len']:.2f} "
            f"| {m['avg_zh']:.2f} | {m['avg_rep']:.2f} | {m['failed']} | {m['avg_latency_ms']:.0f} ms |"
        )
    recommended = _one_line(ranked[0]["template"]).replace("|", "\\|")
    lines += ["", f"推荐模板：`{recommended}`"]
    return "\n".join(lines) + "\n"


def run(
    char_ids: list[str] | None = None,
    all_characters: bool = False,
    stories: str = "",
    limit: int = 20,
    templates: str = "",
    out: str | None = None,
    rwkv_url: str | None = None,
    rwkv_template: str | None = None,
    data_dir: str | None = None,
) -> ExitCode:
    if int(limit) < 1:
        log.error("--limit 必须 ≥ 1。")
        return ExitCode.USAGE

    home_ = _io.home(data_dir)
    if not os.path.exists(_io.char_index_path(home_)):
        log.error("找不到 %s；请先运行 fetch。", _io.char_index_path(home_))
        return ExitCode.USAGE
    with open(_io.char_index_path(home_), encoding="utf-8") as f:
        chars = json.load(f)
    all_ids = [c["id"] for c in chars if c.get("id")]
    if not all_ids:
        log.error("没有匹配到任何角色 id。")
        return ExitCode.USAGE
    selected = all_ids if all_characters else list(char_ids or all_ids)

    items = _sample(home_, selected, stories, limit)
    if not items:
        log.error("没有采样到任何段落：所选角色 / 故事的 zh.md 没有正文。")
        return ExitCode.USAGE

    settings = load_settings()
    if rwkv_url:
        settings = settings.model_copy(update={"rwkv_api_url": rwkv_url})
    if rwkv_template:
        settings = settings.model_copy(update={"rwkv_prompt_template": rwkv_template})
    template_list = _parse_templates(templates) or [settings.rwkv_prompt_template]

    baseline_missing = sum(1 for item in items if item["baseline"] is None)
    results: list[dict[str, Any]] = []
    for template in template_list:
        records, errors = _evaluate(template, items, settings)
        metrics = _summarize(records, baseline_missing)
        results.append({"template": template, "score": _score(metrics),
                        "metrics": metrics, "errors": errors})
    ranked = sorted(results, key=lambda r: r["score"], reverse=True)

    payload: dict[str, Any] = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "limit": limit,
        "samples": len(items),
        "baseline_missing": baseline_missing,
        "rwkv": {"url": settings.rwkv_api_url,
                 "template_sha1": hashlib.sha1(settings.rwkv_prompt_template.encode("utf-8")).hexdigest()},
        "results": ranked,
    }
    out_dir = out or _io.logs_dir(home_)
    os.makedirs(out_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = os.path.join(out_dir, f"rwkv_eval_{stamp}.json")
    md_path = os.path.join(out_dir, f"rwkv_eval_{stamp}.md")
    _io.atomic_write_json(json_path, payload)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(_render_markdown(payload, ranked))

    log.info("样本 %d 段（基线缺失 %d 段），服务 %s", len(items), baseline_missing,
             settings.rwkv_api_url)
    log.info("推荐模板：%s", _one_line(ranked[0]["template"]))
    log.info("报告：%s", json_path)
    log.info("      %s", md_path)

    if any(r["metrics"]["ok"] for r in results):
        return ExitCode.OK
    kinds = {e["kind"] for r in results for e in r["errors"]}
    if kinds and kinds <= {"connection"}:
        log.error("所有模板均连接本地服务失败：请启动 RWKV-Runner 或检查 --rwkv-url（当前 %s）。",
                  settings.rwkv_api_url)
        return ExitCode.CONNECTION
    if kinds and kinds <= {"model_not_loaded"}:
        log.error("所有模板均因模型未加载失败：请先在 RWKV-Runner 中加载模型。")
        return ExitCode.PAUSED
    return ExitCode.OK
