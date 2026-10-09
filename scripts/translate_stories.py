#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""对某角色的故事（zh.md）跑「来回翻译 N 次」链路，结果落入 {id}/result/{story}/。

- 分段：按 zh.md 正文段落（去 frontmatter，空行分隔），每段独立跑完整链路
- 全文：整篇再独立跑一遍完整链路
- 输出：result/{story}/full.json|mp3、seg_NN.json|mp3、segments.json（汇总）
- 续跑：根目录 .translate_stories_state.json（参数需一致；也可靠已有结果跳过）
- 已有结果默认跳过，--overwrite 重跑；进度条用 tqdm
- 批量：--all-characters 处理 data/character.json 中的所有角色

默认：--stories amber_journal、--mode fixed --chain asia（20 次）。
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from datetime import datetime

from tqdm import tqdm

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import translate_chain as tc  # noqa: E402

ROOT = tc.ROOT
STORY_ROOT = os.path.join(ROOT, "data", "story", "character")
CHAR_FILE = os.path.join(ROOT, "data", "character.json")
STATE_PATH = os.path.join(ROOT, ".translate_stories_state.json")


def load_char_ids():
    """读取 data/character.json，返回全部角色 id。"""
    with open(CHAR_FILE, encoding="utf-8") as f:
        return [c["id"] for c in json.load(f) if c.get("id")]


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #
def read_paragraphs(md_path: str):
    with open(md_path, encoding="utf-8") as f:
        raw = f.read()
    if raw.lstrip().startswith("---"):
        parts = raw.split("---", 2)
        if len(parts) >= 3:
            raw = parts[2]
    return [p.strip() for p in re.split(r"\n\s*\n", raw) if p.strip()]


def story_dir(char_id: str) -> str:
    return os.path.join(STORY_ROOT, char_id, "result")


def result_paths(char_id: str, story: str, key: str):
    d = os.path.join(story_dir(char_id), story)
    return d, os.path.join(d, key + ".json"), os.path.join(d, key + ".mp3")


def rel(path: str) -> str:
    return os.path.relpath(path, ROOT).replace(os.sep, "/")


# --------------------------------------------------------------------------- #
# 状态
# --------------------------------------------------------------------------- #
def load_state():
    if not os.path.exists(STATE_PATH):
        return None
    with open(STATE_PATH, encoding="utf-8") as f:
        return json.load(f)


def save_state(state):
    state["updated_at"] = datetime.now().isoformat(timespec="seconds")
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# --------------------------------------------------------------------------- #
# 单次链路
# --------------------------------------------------------------------------- #
def run_chain(text, languages, providers, cfg, log, extra):
    detected = tc.detect_language(text)
    tasks = tc.build_tasks(detected, languages)
    started = datetime.now().isoformat(timespec="seconds")
    cur = text
    steps = []
    for i, task in enumerate(tasks):
        provider, out = tc.translate_step(cur, task["src"], task["tgt"], providers, cfg, log)
        cur = out
        steps.append({"index": i, "kind": task["kind"], "lang": task["lang"],
                      "provider": provider, "text": out})
    result = {
        "source": text,
        "source_detected_lang": detected,
        "target_lang": tc.TARGET_ZH,
        "provider": providers[0] if len(providers) == 1 else providers,
        "languages": languages,
        "steps": steps,
        "final": cur,
        "started_at": started,
        "finished_at": datetime.now().isoformat(timespec="seconds"),
    }
    result.update(extra)
    return result


# --------------------------------------------------------------------------- #
# 汇总
# --------------------------------------------------------------------------- #
def write_summary(char_id: str, story: str, provider, mode_label):
    d = os.path.join(story_dir(char_id), story)
    if not os.path.isdir(d):
        return
    summary = {"id": char_id, "story": story,
               "source_file": rel(os.path.join(STORY_ROOT, char_id, "profile", "story", story, "zh.md")),
               "provider": provider, "mode": mode_label,
               "generated_at": datetime.now().isoformat(timespec="seconds"),
               "full": None, "segments": []}
    full = os.path.join(d, "full.json")
    if os.path.exists(full):
        with open(full, encoding="utf-8") as f:
            r = json.load(f)
        summary["full"] = {"source": r["source"], "languages": r["languages"],
                           "final": r["final"], "json": "full.json",
                           "mp3": "full.mp3" if os.path.exists(os.path.join(d, "full.mp3")) else None}
    seg_files = sorted(n for n in os.listdir(d)
                       if re.fullmatch(r"seg_\d+\.json", n))
    for name in seg_files:
        with open(os.path.join(d, name), encoding="utf-8") as f:
            r = json.load(f)
        key = name[:-5]
        summary["segments"].append({"index": r.get("segment_index"),
                                    "source": r["source"], "languages": r["languages"],
                                    "final": r["final"], "json": name,
                                    "mp3": (key + ".mp3") if os.path.exists(os.path.join(d, key + ".mp3")) else None})
    with open(os.path.join(d, "segments.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def parse_args(argv=None):
    p = argparse.ArgumentParser(description="对角色故事跑翻译链并把结果落入 result/")
    p.add_argument("char_id", nargs="?", help="角色 id，如 10000021（配合 --all-characters 时可省略）")
    p.add_argument("--all-characters", action="store_true",
                   help="处理 data/character.json 中的所有角色")
    p.add_argument("--stories", default="amber_journal",
                   help="故事文件夹名，逗号分隔（默认 amber_journal）")
    p.add_argument("--all", action="store_true", help="处理该角色所有含 zh 内容的故事")
    p.add_argument("--mode", choices=["fixed", "random", "custom"], default="fixed")
    p.add_argument("--chain", choices=list(tc.CHAINS), default="asia")
    p.add_argument("--languages", default="")
    p.add_argument("--steps", type=int, default=20)
    p.add_argument("--provider", choices=["google", "bing", "baidu"], default=None)
    p.add_argument("--proxy", default="")
    p.add_argument("--min-interval", type=float, default=tc.GOOGLE_MIN_INTERVAL,
                   help="Google 请求最小间隔秒数，避免触发限流（默认 %.1f）" % tc.GOOGLE_MIN_INTERVAL)
    p.add_argument("--overwrite", action="store_true", help="已有结果也重跑")
    p.add_argument("--reset", action="store_true", help="忽略旧批量状态")
    p.add_argument("--no-tts", action="store_true", help="不生成语音")
    p.add_argument("--no-file-log", action="store_true", help="不写文件日志")
    return p.parse_args(argv)


def resolve_stories(args, char_id):
    base = os.path.join(STORY_ROOT, char_id, "profile", "story")
    if args.all:
        if not os.path.isdir(base):
            return []
        out = []
        for n in sorted(os.listdir(base)):
            if not os.path.isdir(os.path.join(base, n)):
                continue
            md = os.path.join(base, n, "zh.md")
            if os.path.exists(md) and read_paragraphs(md):
                out.append(n)
        return out
    return [s.strip() for s in args.stories.split(",") if s.strip()]


def signature(args, char_ids):
    return {"characters": char_ids,
            "stories": "ALL" if args.all else [s.strip() for s in args.stories.split(",") if s.strip()],
            "mode": args.mode,
            "chain": args.chain if args.mode == "fixed" else None,
            "steps": args.steps if args.mode == "random" else None,
            "languages": args.languages if args.mode == "custom" else None,
            "provider": args.provider}


def build_tasks(base, char_id, stories, log):
    tasks = []
    for story in stories:
        md = os.path.join(base, story, "zh.md")
        if not os.path.exists(md):
            log.warning("跳过 %s/%s：找不到 %s", char_id, story, rel(md))
            continue
        paras = read_paragraphs(md)
        if not paras:
            log.warning("跳过 %s/%s：zh.md 无正文", char_id, story)
            continue
        tasks.append({"story": story, "key": "full", "index": None, "text": "\n\n".join(paras)})
        for i, para in enumerate(paras, 1):
            tasks.append({"story": story, "key": "seg_%02d" % i, "index": i, "text": para})
    return tasks


def main(argv=None) -> int:
    args = parse_args(argv)
    if not args.char_id and not args.all_characters:
        print("请指定 char_id，或用 --all-characters 处理全部角色。")
        return 1

    log, logfile = tc.setup_logging(args.no_file_log)
    # 控制台只保留告警，避免与 tqdm 进度条混排（明细仍进文件日志）
    for h in log.handlers:
        if type(h) is logging.StreamHandler:
            h.setLevel(logging.WARNING)
    cfg = tc.build_cfg(args)
    providers = [args.provider] if args.provider else ["google", "bing", "baidu"]
    provider_label = providers[0] if len(providers) == 1 else " → ".join(providers)
    mode_label = args.mode + ("/" + args.chain if args.mode == "fixed" else "")

    char_ids = load_char_ids() if args.all_characters else [args.char_id]
    if not args.all_characters and not resolve_stories(args, args.char_id):
        print("没有可处理的故事（检查 --stories / --all / zh.md 是否为空）")
        return 1

    sig = signature(args, char_ids)
    state = load_state()
    if state and not args.reset and state.get("signature") != sig:
        print("参数与上次批量任务不一致，请加 --reset 后重跑。")
        print("  上次：%s" % json.dumps(state.get("signature"), ensure_ascii=False))
        print("  本次：%s" % json.dumps(sig, ensure_ascii=False))
        return 3
    if args.reset or not state or state.get("signature") != sig:
        state = {"signature": sig, "completed": [], "started_at": datetime.now().isoformat(timespec="seconds")}
    completed = set(state.get("completed", []))

    processed = 0
    outer = tqdm(char_ids, desc="角色", unit="个", position=0,
                 disable=not args.all_characters, dynamic_ncols=True)
    try:
        with outer:
            for char_id in outer:
                base = os.path.join(STORY_ROOT, char_id, "profile", "story")
                stories = resolve_stories(args, char_id)
                tasks = build_tasks(base, char_id, stories, log) if stories else []
                if not tasks:
                    log.warning("跳过 %s：没有可处理的故事", char_id)
                    continue

                line = "角色 %s，故事 %s，共 %d 份结果（%s）" % (
                    char_id, ",".join(stories), len(tasks), mode_label)
                if args.all_characters:
                    tqdm.write(line)
                else:
                    print(line)

                done_stories = set()
                for task in tqdm(tasks, desc="翻译链", unit="份",
                                 position=1 if args.all_characters else 0,
                                 leave=not args.all_characters, dynamic_ncols=True):
                    story, key, idx, text = task["story"], task["key"], task["index"], task["text"]
                    d, json_path, mp3_path = result_paths(char_id, story, key)
                    os.makedirs(d, exist_ok=True)
                    state_key = "%s|%s|%s" % (char_id, story, key)
                    if not args.overwrite and (os.path.exists(json_path) or state_key in completed):
                        done_stories.add(story)
                        processed += 1
                        continue
                    languages, chain_label = tc.resolve_languages(args)
                    log.info(">>> %s/%s/%s (%d 字)：%s", char_id, story, key, len(text),
                             " → ".join(languages))
                    result = run_chain(text, languages, providers, cfg, log,
                                       {"story": story, "segment_index": idx,
                                        "source_file": rel(os.path.join(base, story, "zh.md")),
                                        "mode": args.mode, "chain": chain_label})
                    with open(json_path, "w", encoding="utf-8") as f:
                        json.dump(result, f, ensure_ascii=False, indent=2)
                    if not args.no_tts:
                        try:
                            audio = tc.google_tts_bytes(result["final"], tc.TARGET_ZH, cfg.get("proxies"))
                            with open(mp3_path, "wb") as f:
                                f.write(audio)
                        except Exception as exc:  # noqa: BLE001
                            log.warning("语音生成失败（%s/%s/%s）：%s", char_id, story, key, exc)
                    completed.add(state_key)
                    state["completed"] = sorted(completed)
                    save_state(state)
                    done_stories.add(story)
                    processed += 1

                for story in done_stories:
                    write_summary(char_id, story, provider_label, mode_label)
    except tc.Paused as exc:
        save_state(state)
        print("已暂停：%s" % exc)
        print("进度已保存（.translate_stories_state.json），修复后重跑即可续跑。")
        return 4
    except tc.ConnectionIssue as exc:
        save_state(state)
        print("后端连接失败：\n%s" % exc)
        print("检查网络/代理后重跑即可续跑。")
        return 5
    except tc.UnsupportedLanguage as exc:
        save_state(state)
        print("语言不受支持：%s" % exc)
        return 2

    if os.path.exists(STATE_PATH):
        os.remove(STATE_PATH)
    if args.all_characters:
        print("完成。处理 %d 个角色、%d 份结果，结果在各 {id}/result/ 下。" % (len(char_ids), processed))
    else:
        print("完成。结果目录：%s" % rel(os.path.join(story_dir(args.char_id), "")))
    if logfile:
        print("日志：%s" % rel(logfile))
    return 0


if __name__ == "__main__":
    sys.exit(main())
