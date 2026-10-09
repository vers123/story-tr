#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""对某角色的故事（zh.md）跑「来回翻译 N 次」链路，结果落入 {id}/result/{story}/。

- 分段：按 zh.md 正文段落（去 frontmatter，空行分隔），每段独立跑完整链路
- 全文：整篇再独立跑一遍完整链路
- 标题：frontmatter 的 title（如「角色详细」）也跑一遍完整链路
- 输出：result/{story}/full.json|mp3、seg_NN.json|mp3、title.json|mp3、segments.json（汇总）
- 续跑：根目录 .translate_stories_state.json（参数需一致；也可靠已有结果跳过）
- 已有结果默认跳过，--overwrite 重跑；进度条用 tqdm
- 批量：位置参数支持 id / 范围（如 10000002-10000030），或 --all-characters 处理全部角色
- 故事：不指定 --stories 时处理该角色的全部含内容故事

默认：全部含内容故事、--mode fixed --chain asia（20 次）。
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from tqdm import tqdm

from . import chain as tc
from . import paths

ROOT = paths.home()
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


def read_title(md_path: str):
    """读取 md frontmatter 里的 `title` 值（JSON 编码的字符串，如 `"角色详细"`）；无则返回 None。"""
    with open(md_path, encoding="utf-8") as f:
        raw = f.read()
    if not raw.lstrip().startswith("---"):
        return None
    parts = raw.split("---", 2)
    if len(parts) < 3:
        return None
    for line in parts[1].splitlines():
        line = line.strip()
        if line.startswith("title:"):
            val = line[len("title:"):].strip()
            if not val:
                return None
            try:
                return json.loads(val)
            except json.JSONDecodeError:
                return val.strip().strip('"')
    return None


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
def run_chain_many(texts, languages, providers, cfg, log, extras=None):
    """多条文本同步走同一条链，返回与 texts 同序的结果 dict 列表。

    先按**检出语言**分组（同一分组才能共享每步的 src/tgt），各步用
    `tc.translate_step_many` 推进——是否合并为一次请求由 `tc.translate_many` 决定。
    """
    texts = list(texts)
    extras = list(extras) if extras else [{} for _ in texts]
    dets = [tc.detect_language(t) for t in texts]
    results = [None] * len(texts)
    for det in dict.fromkeys(dets):
        idx = [i for i, d in enumerate(dets) if d == det]
        started = datetime.now().isoformat(timespec="seconds")
        cur = {i: texts[i] for i in idx}
        steps = {i: [] for i in idx}
        for k, task in enumerate(tc.build_tasks(det, languages)):
            provider, outs = tc.translate_step_many(
                [cur[i] for i in idx], task["src"], task["tgt"], providers, cfg, log)
            for pos, i in enumerate(idx):
                cur[i] = outs[pos]
                steps[i].append({"index": k, "kind": task["kind"], "lang": task["lang"],
                                 "provider": provider, "text": outs[pos]})
        for i in idx:
            results[i] = {
                "source": texts[i],
                "source_detected_lang": det,
                "target_lang": tc.TARGET_ZH,
                "provider": providers[0] if len(providers) == 1 else providers,
                "languages": languages,
                "steps": steps[i],
                "final": cur[i],
                "started_at": started,
                "finished_at": datetime.now().isoformat(timespec="seconds"),
            }
            results[i].update(extras[i])
    return results


def run_chain(text, languages, providers, cfg, log, extra):
    """单条文本跑链路（`run_chain_many` 的兼容封装）。"""
    return run_chain_many([text], languages, providers, cfg, log, [extra])[0]


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
               "full": None, "title": None, "segments": []}
    full = os.path.join(d, "full.json")
    if os.path.exists(full):
        with open(full, encoding="utf-8") as f:
            r = json.load(f)
        summary["full"] = {"source": r["source"], "languages": r["languages"],
                           "final": r["final"], "json": "full.json",
                           "mp3": "full.mp3" if os.path.exists(os.path.join(d, "full.mp3")) else None}
    title = os.path.join(d, "title.json")
    if os.path.exists(title):
        with open(title, encoding="utf-8") as f:
            r = json.load(f)
        summary["title"] = {"source": r["source"], "languages": r["languages"],
                            "final": r["final"], "json": "title.json",
                            "mp3": "title.mp3" if os.path.exists(os.path.join(d, "title.mp3")) else None}
    seg_files = sorted(n for n in os.listdir(d)
                       if re.fullmatch(r"seg_\d+\.json", n))
    for name in seg_files:
        with open(os.path.join(d, name), encoding="utf-8") as f:
            r = json.load(f)
        key = name[:-5]
        has_mp3 = os.path.exists(os.path.join(d, key + ".mp3"))
        summary["segments"].append({"index": r.get("segment_index"),
                                    "source": r["source"], "languages": r["languages"],
                                    "final": r["final"], "json": name,
                                    "mp3": (key + ".mp3") if has_mp3 else None})
    with open(os.path.join(d, "segments.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def add_parser(sub):
    """注册 `story-tr translate` 子命令。"""
    p = sub.add_parser(
        "translate",
        help="批量翻译角色故事（支持 id / 范围），结果落入 {id}/result/",
        parents=[paths.data_dir_parent()],
        epilog="示例：story-tr translate 10000021   |   story-tr translate 10000002-10000030   |   "
               "story-tr translate 10000021,10000025-10000030 --stories amber_journal,vision",
    )
    p.add_argument("char_ids", nargs="*",
                   help="角色 id / 范围（空格或逗号分隔）："
                        "10000021 / 10000002-10000030 / 10000021,10000025-10000030")

    g = p.add_argument_group("角色选择")
    g.add_argument("--all-characters", action="store_true",
                   help="处理 data/character.json 中的全部角色（与 char_ids 互斥）")

    g = p.add_argument_group("故事选择")
    g.add_argument("--stories", default="",
                   help="只处理指定故事文件夹，逗号分隔（不指定 = 处理全部含内容故事）")
    g.add_argument("--all", action="store_true",
                   help="处理选中角色的全部含内容故事（即不指定 --stories 时的默认行为）")

    g = p.add_argument_group("输出内容")
    gsel = g.add_mutually_exclusive_group()
    gsel.add_argument("--full-only", action="store_true",
                      help="只跑全文（跳过逐段任务；标题始终翻译。配合 --no-batch 时调用量约降到 1/7）")
    gsel.add_argument("--segments-only", action="store_true",
                      help="只跑逐段任务（跳过全文；标题始终翻译）")

    tc.add_common_args(p, default_mode="fixed")

    g = p.add_argument_group("批量")
    g.add_argument("--workers", type=int, default=1,
                   help="并发线程数（默认 1；建议 3~4，配合 --min-interval 控制总速率）")
    g.add_argument("--no-batch", action="store_true",
                   help="关闭批量：同一故事的逐段在每步各自发请求（默认合并为一次）")
    g.add_argument("--overwrite", action="store_true", help="已有结果也重跑（默认跳过）")
    g.add_argument("--reset", action="store_true", help="忽略旧批量状态")
    p.set_defaults(func=main)
    return p


def parse_char_ids(tokens, all_ids):
    """解析 char_ids（含 `起始-结束` 范围）→ 按 character.json 顺序返回其中存在的 id。"""
    want = set()
    for tok in tokens:
        for part in tok.replace(" ", "").split(","):
            if not part:
                continue
            if "-" in part:
                a, b = part.split("-", 1)
                try:
                    start, end = int(a), int(b)
                except ValueError:
                    raise SystemExit("无法解析 id 范围：%s（应形如 10000002-10000030）" % part)
                if start > end:
                    start, end = end, start
                want.update(str(i) for i in range(start, end + 1))
            else:
                want.add(part)
    have = set(all_ids)
    missing = sorted(want - have)
    if missing:
        print("提示：以下 id 不在 character.json 中，已跳过：%s" % ",".join(missing))
    return [i for i in all_ids if i in want]


def resolve_stories(args, char_id):
    """返回该角色要处理的故事：指定了 --stories 就只用它，否则取全部含内容故事。"""
    names = [s.strip() for s in args.stories.split(",") if s.strip()]
    if names and not args.all:
        return names
    base = os.path.join(STORY_ROOT, char_id, "profile", "story")
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


def signature(args, char_ids):
    all_stories = args.all or not args.stories.strip()
    sig = {"characters": char_ids,
           "stories": "ALL" if all_stories else [s.strip() for s in args.stories.split(",") if s.strip()],
           "mode": args.mode,
           "chain": args.chain if args.mode == "fixed" else None,
           "steps": args.steps if args.mode == "random" else None,
           "languages": args.languages if args.mode == "custom" else None,
           "provider": args.provider}
    parts = parts_label(args)
    if parts != "both":  # 只产出部分内容时任务集合不同，需写进签名；默认不写以兼容旧状态
        sig["parts"] = parts
    return sig


def prepare_state(state, sig, reset):
    """校验/初始化批量状态，返回 (state, completed, error)。

    - 已有签名且与本次不一致（且未 `--reset`）→ 返回错误，提示加 `--reset`
    - 签名缺失（被 `clean` 重置）→ 采用新签名并**沿用已有 completed 记录**
    - `--reset` → 清空 completed
    """
    prev = (state or {}).get("signature")
    if state and not reset and prev and prev != sig:
        return None, None, ("参数与上次批量任务不一致，请加 --reset 后重跑。\n"
                            "  上次：%s\n  本次：%s"
                            % (json.dumps(prev, ensure_ascii=False),
                               json.dumps(sig, ensure_ascii=False)))
    completed = set() if reset else set((state or {}).get("completed", []))
    new_state = {"signature": sig, "completed": sorted(completed),
                 "started_at": (state or {}).get("started_at")
                 or datetime.now().isoformat(timespec="seconds")}
    return new_state, completed, None


def parts_label(args):
    """本次要产出的内容：both（全文+分段）/ full / segments。"""
    if args.full_only:
        return "full"
    if args.segments_only:
        return "segments"
    return "both"


def build_tasks(base, char_id, stories, log, parts="both"):
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
        title = read_title(md)
        if title:
            # 标题始终翻译（不受 --full-only / --segments-only 影响）
            tasks.append({"story": story, "key": "title", "index": None, "text": title})
        if parts in ("both", "full"):
            tasks.append({"story": story, "key": "full", "index": None, "text": "\n\n".join(paras)})
        if parts in ("both", "segments"):
            for i, para in enumerate(paras, 1):
                tasks.append({"story": story, "key": "seg_%02d" % i, "index": i, "text": para})
    return tasks


def main(args) -> int:
    if args.char_ids and args.all_characters:
        print("不能同时指定角色 id 与 --all-characters，请二选一。")
        return 1
    if not args.char_ids and not args.all_characters:
        print("请指定角色 id / 范围（如 10000021 或 10000002-10000030），"
              "或用 --all-characters 处理全部角色。")
        return 1

    log, logfile = tc.setup_logging(args.no_file_log)
    # 控制台只保留告警，避免与 tqdm 进度条混排（明细仍进文件日志）
    for h in log.handlers:
        if type(h) is logging.StreamHandler:
            h.setLevel(logging.WARNING)
    cfg = tc.build_cfg(args)
    providers = [args.provider] if args.provider else ["google", "bing", "baidu"]
    provider_label = providers[0] if len(providers) == 1 else " → ".join(providers)
    parts = parts_label(args)
    batch = not args.no_batch and args.mode != "random"  # random 下每份独立随机，不做批量
    mode_label = args.mode + ("/" + args.chain if args.mode == "fixed" else "")
    if batch:
        mode_label += "+批量"

    all_ids = load_char_ids()
    char_ids = all_ids if args.all_characters else parse_char_ids(args.char_ids, all_ids)
    if not char_ids:
        print("没有匹配到任何角色 id；请对照 data/character.json 检查。")
        return 1

    sig = signature(args, char_ids)
    state, completed, err = prepare_state(load_state(), sig, args.reset)
    if err:
        print(err)
        return 3

    processed = 0
    outer = tqdm(char_ids, desc="角色", unit="个", position=0,
                 disable=len(char_ids) <= 1, dynamic_ncols=True)
    try:
        with outer:
            for char_id in outer:
                base = os.path.join(STORY_ROOT, char_id, "profile", "story")
                stories = resolve_stories(args, char_id)
                tasks = build_tasks(base, char_id, stories, log, parts) if stories else []
                if not tasks:
                    log.warning("跳过 %s：没有可处理的故事", char_id)
                    continue

                line = "角色 %s，故事 %s，共 %d 份结果（%s）" % (
                    char_id, ",".join(stories), len(tasks), mode_label)
                if len(char_ids) > 1:
                    tqdm.write(line)
                else:
                    print(line)

                done_stories = set()
                stats = {"n": 0}
                state_lock = threading.Lock()

                # 批量：同一故事的全部任务为一组（各步同步推进，每步合并为一次请求）
                grouped = {}
                for t in tasks:
                    grouped.setdefault(t["story"], []).append(t)
                units = list(grouped.values()) if batch else [[t] for t in tasks]

                def _skip_it(task, json_path):
                    """已完成（文件在或状态里有）则记数并返回 True。"""
                    state_key = "%s|%s|%s" % (char_id, task["story"], task["key"])
                    with state_lock:
                        if not args.overwrite and (os.path.exists(json_path)
                                                   or state_key in completed):
                            done_stories.add(task["story"])
                            stats["n"] += 1
                            return True
                    return False

                def _save(task, json_path, mp3_path, result):
                    with open(json_path, "w", encoding="utf-8") as f:
                        json.dump(result, f, ensure_ascii=False, indent=2)
                    if not args.no_tts:
                        try:
                            audio = tc.google_tts_bytes(result["final"], tc.TARGET_ZH,
                                                        cfg.get("proxies"))
                            with open(mp3_path, "wb") as f:
                                f.write(audio)
                        except Exception as exc:  # noqa: BLE001
                            log.warning("语音生成失败（%s/%s/%s）：%s", char_id, task["story"],
                                        task["key"], exc)
                    with state_lock:
                        completed.add("%s|%s|%s" % (char_id, task["story"], task["key"]))
                        state["completed"] = sorted(completed)
                        save_state(state)
                        done_stories.add(task["story"])
                        stats["n"] += 1

                def _process_unit(unit):
                    """处理一组任务（可被多线程并发调用；共享状态加锁），返回该组任务数。"""
                    todo = []
                    for t in unit:
                        d, json_path, mp3_path = result_paths(char_id, t["story"], t["key"])
                        os.makedirs(d, exist_ok=True)
                        if not _skip_it(t, json_path):
                            todo.append((t, json_path, mp3_path))
                    if todo:
                        languages, chain_label = tc.resolve_languages(args)
                        extras = [{"story": t["story"], "segment_index": t["index"],
                                   "source_file": rel(os.path.join(base, t["story"], "zh.md")),
                                   "mode": args.mode, "chain": chain_label}
                                  for t, _, _ in todo]
                        log.info(">>> %s：%d 条（%s）：%s", char_id, len(todo),
                                 ",".join(sorted({t["story"] for t, _, _ in todo})),
                                 " → ".join(languages))
                        if len(todo) > 1:
                            results = run_chain_many([t["text"] for t, _, _ in todo],
                                                     languages, providers, cfg, log, extras)
                        else:
                            results = [run_chain(t["text"], languages, providers, cfg, log,
                                                 extras[0]) for t, _, _ in todo]
                        for (t, json_path, mp3_path), res in zip(todo, results):
                            _save(t, json_path, mp3_path, res)
                    return len(unit)

                bar = tqdm(total=len(tasks), desc="翻译链", unit="份",
                           position=1 if len(char_ids) > 1 else 0,
                           leave=len(char_ids) <= 1, dynamic_ncols=True)
                try:
                    if args.workers > 1:
                        with ThreadPoolExecutor(max_workers=args.workers) as ex:
                            futures = [ex.submit(_process_unit, u) for u in units]
                            for fut in as_completed(futures):
                                bar.update(fut.result())
                    else:
                        for u in units:
                            bar.update(_process_unit(u))
                finally:
                    bar.close()
                processed += stats["n"]

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
    if len(char_ids) == 1:
        print("完成。结果目录：%s" % rel(os.path.join(story_dir(char_ids[0]), "")))
    else:
        print("完成。处理 %d 个角色、%d 份结果，结果在各 {id}/result/ 下。"
              % (len(char_ids), processed))
    if logfile:
        print("日志：%s" % rel(logfile))
    return 0
