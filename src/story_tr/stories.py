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

import hashlib
import json
import logging
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from tqdm import tqdm

from . import __version__, paths, report, statefile
from . import chain as tc
from .exitcodes import ExitCode

log = logging.getLogger("translate_chain")

ROOT = paths.home()
STORY_ROOT = os.path.join(ROOT, "data", "story", "character")
CHAR_FILE = os.path.join(ROOT, "data", "character.json")
STATE_PATH = os.path.join(ROOT, ".translate_stories_state.json")
TITLE_CACHE_PATH = os.path.join(ROOT, "data", "_title_cache.json")
TITLE_CACHE_VERSION = 1


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
    """读取批量状态；损坏时自动回退 `.bak` 或隔离损坏文件（见 `statefile`）。"""
    return statefile.load_json_with_backup(STATE_PATH)


def save_state(state):
    state["updated_at"] = datetime.now().isoformat(timespec="seconds")
    statefile.atomic_write_json(STATE_PATH, state)


def clear_state():
    """删除状态文件及其备份（正常完成后调用）。"""
    statefile.remove_with_backup(STATE_PATH)


class _StateWriter:
    """批量状态去抖写入：每 `every` 份结果或 `force` 时落盘一次。

    `mark()` 由持有 `state_lock` 的调用方执行；`flush()` 自己加锁，
    避免与 worker 线程的 `completed.add` 竞争。
    """

    def __init__(self, state, completed, lock, every=50):
        self.state = state
        self.completed = completed
        self.lock = lock
        self.every = max(1, int(every))
        self._marks = 0

    def mark(self):
        self._marks += 1

    def flush(self, force=False):
        with self.lock:
            if not force and self._marks < self.every:
                return
            self.state["completed"] = sorted(self.completed)
            save_state(self.state)
            self._marks = 0


# --------------------------------------------------------------------------- #
# 单次链路
# --------------------------------------------------------------------------- #
def run_chain_many(texts, languages, providers, cfg, log, extras=None, source_lang=None):
    """多条文本同步走同一条链，返回与 texts 同序的结果 dict 列表。

    先按**检出语言**分组（同一分组才能共享每步的 src/tgt），各步用
    `tc.translate_step_many` 推进——是否合并为一次请求由 `tc.translate_many` 决定。

    `source_lang` 是源语言提示（`translate` 传 `zh-CN`）：含汉字的文本直接采用提示，
    不再逐段调用 langdetect；不含汉字的文本（纯英文引用等）仍走检测。

    **无文字内容**（纯标点 / 数字 / 空白，如角色故事里的 `……`）不参与链路：
    既判定不了语言，翻译也没有意义，直接**原样保留**，不发任何请求。
    """
    texts = list(texts)
    extras = list(extras) if extras else [{} for _ in texts]
    results = [None] * len(texts)

    trivial = [not tc.has_features(t) for t in texts]
    if any(trivial):
        log.log(report.STEP, "  %d 条无文字内容（纯符号 / 数字），原样保留、不发请求",
                sum(trivial))
        stamp = datetime.now().isoformat(timespec="seconds")
        for i, flag in enumerate(trivial):
            if not flag:
                continue
            results[i] = {
                "source": texts[i],
                "source_detected_lang": "none",
                "target_lang": tc.TARGET_ZH,
                "provider": None,
                "languages": languages,
                "steps": [],
                "final": texts[i],
                "passthrough": True,
                "started_at": stamp,
                "finished_at": stamp,
            }
            results[i].update(extras[i])

    dets = [None if trivial[i] else
            (source_lang if source_lang and tc.has_han(texts[i])
             else tc.detect_language(texts[i]))
            for i in range(len(texts))]
    for det in dict.fromkeys(d for d in dets if d is not None):
        idx = [i for i, d in enumerate(dets) if d == det]
        started = datetime.now().isoformat(timespec="seconds")
        cur = {i: texts[i] for i in idx}
        steps = {i: [] for i in idx}
        for k, task in enumerate(tc.build_tasks(det, languages)):
            provider, outs = tc.translate_step_many(
                [cur[i] for i in idx], task["src"], task["tgt"], providers, cfg, log)
            metas = tc.local_metas()
            # 逐条文本的元数据；条数对不上（合并请求等）时退回整批合计
            per_text = len(metas) == len(idx)
            fallback = None if per_text else tc.local_meta()
            for pos, i in enumerate(idx):
                cur[i] = outs[pos]
                step = {"index": k, "kind": task["kind"], "lang": task["lang"],
                        "provider": provider, "text": outs[pos]}
                meta = metas[pos] if per_text else fallback
                if meta:
                    step["rwkv"] = dict(meta)
                steps[i].append(step)
        for i in idx:
            merged = tc.merge_local_meta([s.get("rwkv") for s in steps[i] if s.get("rwkv")])
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
            if merged:
                results[i]["rwkv"] = merged
            results[i].update(extras[i])
    return results


def run_chain(text, languages, providers, cfg, log, extra, source_lang=None):
    """单条文本跑链路（`run_chain_many` 的兼容封装）。"""
    return run_chain_many([text], languages, providers, cfg, log, [extra],
                          source_lang=source_lang)[0]


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

    g = p.add_argument_group("可靠性")
    g.add_argument("--revalidate", action="store_true",
                   help="跳过已有结果前校验其 source 与当前 zh.md 是否一致（默认只按文件存在跳过）")
    g.add_argument("--no-cache", action="store_true",
                   help="不使用标题译文缓存（data/_title_cache.json）")
    g.add_argument("--fail-fast", action="store_true",
                   help="任一任务失败即终止整批（默认记录失败并继续，最后返回退出码 7）")
    g.add_argument("--max-failures", type=int, default=20,
                   help="连续失败达到该数量时提前结束（默认 20，仅 --fail-fast 关闭时生效）")
    g.add_argument("--dry-run", action="store_true",
                   help="只统计将处理的结果数量与预计步骤调用量，不翻译、不写文件")
    p.set_defaults(func=main)
    return p


def parse_char_ids(tokens, all_ids, warn=True, with_missing=False):
    """解析 char_ids（含 `起始-结束` 范围）→ 按 character.json 顺序返回其中存在的 id。

    `warn=False` 时不打印提示（MCP stdio 不能污染 stdout）；`with_missing=True`
    时返回 `(存在的 id 列表, 不存在的 id 列表)`。
    """
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
    if missing and warn:
        print("提示：以下 id 不在 character.json 中，已跳过：%s" % ",".join(missing))
    found = [i for i in all_ids if i in want]
    return (found, missing) if with_missing else found


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


def signature(args, char_ids, cfg=None):
    all_stories = args.all or not args.stories.strip()
    sig = {"characters": char_ids,
           "stories": "ALL" if all_stories else [s.strip() for s in args.stories.split(",") if s.strip()],
           "mode": args.mode,
           "chain": args.chain if args.mode == "fixed" else None,
           "steps": args.steps if args.mode == "random" else None,
           "languages": args.languages if args.mode == "custom" else None,
           "provider": args.provider}
    if args.mode == "rwkv20" or args.provider == "rwkv":
        cfg_local = (cfg or {}).get("rwkv") or tc.local_rwkv.from_args(args)
        sig["rwkv"] = tc.local_rwkv.signature(cfg_local)
    parts = parts_label(args)
    if parts != "both":  # 只产出部分内容时任务集合不同，需写进签名；默认不写以兼容旧状态
        sig["parts"] = parts
    return sig


_SIG_LABELS = (
    ("characters", "角色范围"),
    ("stories", "故事"),
    ("mode", "模式"),
    ("chain", "链"),
    ("steps", "随机步数"),
    ("languages", "自定义语言"),
    ("provider", "后端"),
    ("parts", "输出内容"),
)


def _human_sig(value):
    """把签名里的单个值渲染成人看的短字符串。"""
    if value in (None, ""):
        return "—"
    if isinstance(value, list):
        if len(value) > 6:
            return "%s…（共 %d 项）" % (",".join(str(x) for x in value[:6]), len(value))
        return ",".join(str(x) for x in value)
    return str(value)


def describe_signature_diff(prev, cur) -> str:
    """把两次批量签名的差异写成人话（只列发生变化的项）。"""
    lines = []
    for key, label in _SIG_LABELS:
        before, after = prev.get(key, "<无>"), cur.get(key, "<无>")
        if before != after:
            lines.append("  · %s：上次 %s → 本次 %s"
                         % (label, _human_sig(before), _human_sig(after)))
    return "\n".join(lines) or "  （无可比较的差异）"


def prepare_state(state, sig, reset):
    """校验/初始化批量状态，返回 (state, completed, error)。

    - 已有签名且与本次不一致（且未 `--reset`）→ 返回错误，提示加 `--reset`
    - 签名缺失（被 `clean` 重置）→ 采用新签名并**沿用已有 completed 记录**
    - `--reset` → 清空 completed
    """
    prev = (state or {}).get("signature")
    if state and not reset and prev and prev != sig:
        return None, None, (
            "参数与上次批量任务不一致，无法续跑（会与已有进度冲突）。\n"
            "差异如下：\n%s\n"
            "如需按新参数重跑，请加 --reset（GUI：点「重置批次并重跑」）。"
            % describe_signature_diff(prev, sig))
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


def providers_of(result) -> str:
    """结果各步实际用到的后端（去重保序；超过 2 个则省略）。"""
    seen = []
    for s in result.get("steps") or []:
        p = s.get("provider")
        if p and p not in seen:
            seen.append(p)
    if not seen:
        return "-"
    return "/".join(seen) if len(seen) <= 2 else "%s/…" % seen[0]


def is_fatal_local_error(exc, args, providers) -> bool:
    """本地模式下「服务不可达 / 模型未加载」是致命的：中止整批，返回 5/4。
    业务失败（Paused 且非 ModelNotLoaded）仍按部分失败处理（7）。"""
    local = getattr(args, "mode", None) == "rwkv20" or "rwkv" in list(providers)
    return local and isinstance(exc, (tc.ConnectionIssue, tc.ModelNotLoaded))


def result_is_stale(json_path: str, source: str) -> bool:
    """已有结果是否与当前源文不一致（`--revalidate` 用）。"""
    try:
        with open(json_path, encoding="utf-8") as f:
            row = json.load(f)
    except (OSError, ValueError):
        return True
    return (row.get("source") or "").strip() != (source or "").strip()


def write_failures(failures) -> str:
    """把失败任务写入 `logs/failures_<时间戳>.json`，返回路径。"""
    os.makedirs(tc.LOG_DIR, exist_ok=True)
    path = os.path.join(tc.LOG_DIR,
                        "failures_%s.json" % datetime.now().strftime("%Y%m%d_%H%M%S"))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(failures, f, ensure_ascii=False, indent=2)
    return path


def write_run_summary(info: dict) -> str:
    """把一次运行的统计写入 `logs/run_<时间戳>.json`，返回路径。"""
    os.makedirs(tc.LOG_DIR, exist_ok=True)
    path = os.path.join(tc.LOG_DIR,
                        "run_%s.json" % datetime.now().strftime("%Y%m%d_%H%M%S"))
    statefile.atomic_write_json(path, info)
    return path


def title_cache_key(text: str, args, providers, cfg=None) -> str:
    """标题译文缓存键：文本 + 路线 + 后端顺序（random 模式不缓存）。"""
    payload = [TITLE_CACHE_VERSION, text, getattr(args, "mode", None),
               getattr(args, "chain", None) if getattr(args, "mode", None) == "fixed" else None,
               getattr(args, "languages", "") if getattr(args, "mode", None) == "custom" else None,
               getattr(args, "steps", None) if getattr(args, "mode", None) == "random" else None,
               list(providers)]
    if "rwkv" in list(providers):
        # 本地模板/采样参数会改变结果，必须进缓存键；其它模式保持原 key 不变
        cfg_local = (cfg or {}).get("rwkv") or tc.local_rwkv.from_args(args)
        payload.append(tc.local_rwkv.signature(cfg_local))
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode("utf-8")).hexdigest()


def load_title_cache() -> dict:
    try:
        with open(TITLE_CACHE_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_title_cache(cache: dict) -> None:
    os.makedirs(os.path.dirname(TITLE_CACHE_PATH), exist_ok=True)
    statefile.atomic_write_json(TITLE_CACHE_PATH, cache)


def _dry_run(args, char_ids) -> int:
    """打印任务数与预计调用量；不翻译、不写文件。"""
    languages, label = tc.resolve_languages(args)
    total = 0
    active = 0

    def _log():
        return logging.getLogger("translate_chain")

    for cid in char_ids:
        base = os.path.join(STORY_ROOT, cid, "profile", "story")
        names = resolve_stories(args, cid)
        if not names:
            continue
        tasks = build_tasks(base, cid, names, _log(), parts_label(args))
        if tasks:
            active += 1
        total += len(tasks)

    steps_per_item = len(tc.build_tasks("zh-CN", languages))
    batch = not getattr(args, "no_batch", False) and args.mode != "random"
    print("--dry-run：角色 %d（有可处理故事 %d），故事 %d，结果 %d 份"
          % (len(char_ids), active, sum(len(resolve_stories(args, c)) for c in char_ids), total))
    print("路线：%s（%d 步/份；含 to_en 与 to_zh 共 %d 步）"
          % (label, len(languages), steps_per_item))
    if args.mode == "rwkv20":
        print("本地一次成型：预计本地生成约 %d 次（每份 1 次）" % total)
    print("预计步骤调用：约 %d 次；批量%s（实际请求数会因合并而更少）"
          % (total * steps_per_item, "开启" if batch else "关闭"))
    if args.mode == "random":
        print("注意：random 每份独立随机且自动关闭批量，调用量约为 fixed+批量 的 20 倍，"
              "全量可能触及 Google 免费端点 20 万/天配额。")
    return ExitCode.OK


def main(args, on_progress=None, cancel=None) -> int:
    """批量翻译入口（CLI 与 GUI 共用）。

    on_progress(dict)：进度回调，键为 `event`（`char_start` / `progress` / `char_done`）、
    `char_id`、`char_index`、`chars`、`tasks`、`done`。
    cancel：`threading.Event`；置位后在下一次检查点中止（保存进度并返回 `6`）。
    """
    if args.char_ids and args.all_characters:
        print("不能同时指定角色 id 与 --all-characters，请二选一。")
        return ExitCode.USAGE
    if not args.char_ids and not args.all_characters:
        print("请指定角色 id / 范围（如 10000021 或 10000002-10000030），"
              "或用 --all-characters 处理全部角色。")
        return ExitCode.USAGE

    log, logfile = tc.setup_logging(
        args.no_file_log or bool(getattr(args, "dry_run", False)),
        report.console_level(args))
    cfg = tc.build_cfg(args)
    providers, err = tc.resolve_providers(args)
    if err:
        print(err)
        return ExitCode.USAGE
    args.no_tts = not tc.tts_enabled(args)

    def _notify(**kw):
        """回调进度；回调自身出错不影响翻译。"""
        if on_progress is not None:
            try:
                on_progress(kw)
            except Exception:  # noqa: BLE001
                pass

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
        return ExitCode.USAGE
    if getattr(args, "dry_run", False):
        return _dry_run(args, char_ids)
    if args.mode == "random" and len(char_ids) > 1:
        log.warning("random 模式为每份结果独立随机，且会关闭批量；调用量约为 "
                    "fixed+批量 的 20 倍，全量可能触及 Google 免费端点 20 万/天配额。")

    sig = signature(args, char_ids, cfg)
    state, completed, err = prepare_state(load_state(), sig, args.reset)
    if err:
        print(err)
        return ExitCode.SIGNATURE

    processed = 0
    failures = []
    fail_fast = bool(getattr(args, "fail_fast", False))
    max_failures = max(1, int(getattr(args, "max_failures", 20)))
    revalidate = bool(getattr(args, "revalidate", False))
    use_cache = not getattr(args, "no_cache", False) and args.mode != "random"
    title_cache = load_title_cache() if use_cache else {}
    cache_dirty = {"value": False}
    cache_lock = threading.Lock()
    state_lock = threading.Lock()
    writer = _StateWriter(state, completed, state_lock)
    run_started = datetime.now().isoformat(timespec="seconds")
    planned = 0

    def _save_cache():
        if not use_cache or not cache_dirty["value"]:
            return
        with cache_lock:
            save_title_cache(title_cache)
            cache_dirty["value"] = False

    def _finish(code):
        """写运行摘要（失败不影响退出码）并返回退出码。"""
        _save_cache()
        try:
            path = write_run_summary({
                "version": __version__,
                "started_at": run_started,
                "finished_at": datetime.now().isoformat(timespec="seconds"),
                "characters": len(char_ids),
                "tasks": planned,
                "completed": processed,
                "failed": len(failures),
                "mode": args.mode,
                "chain": args.chain if args.mode == "fixed" else None,
                "providers": providers,
                "exit_code": int(code),
            })
            print("运行摘要：%s" % rel(path))
        except OSError as exc:  # 摘要失败不影响主流程
            log.warning("运行摘要写入失败：%s", exc)
        return code

    outer = tqdm(char_ids, desc="角色", unit="个", position=0,
                 **report.bar_kwargs(len(char_ids) <= 1))
    try:
        with outer:
            for char_index, char_id in enumerate(outer, 1):
                if cancel is not None and cancel.is_set():
                    raise tc.Cancelled()
                base = os.path.join(STORY_ROOT, char_id, "profile", "story")
                stories = resolve_stories(args, char_id)
                tasks = build_tasks(base, char_id, stories, log, parts) if stories else []
                if not tasks:
                    log.warning("跳过 %s：没有可处理的故事", char_id)
                    continue
                planned += len(tasks)

                log.info("角色 %s，故事 %s，共 %d 份结果（%s）",
                         char_id, ",".join(stories), len(tasks), mode_label)
                _notify(event="char_start", char_id=char_id, char_index=char_index,
                        chars=len(char_ids), tasks=len(tasks), done=0)

                done_stories = set()
                stats = {"n": 0}

                # 批量：同一故事的全部任务为一组（各步同步推进，每步合并为一次请求）
                grouped = {}
                for t in tasks:
                    grouped.setdefault(t["story"], []).append(t)
                units = list(grouped.values()) if batch else [[t] for t in tasks]

                def _skip_it(task, json_path):
                    """已完成（文件在或状态里有）则记数并返回 True。"""
                    state_key = "%s|%s|%s" % (char_id, task["story"], task["key"])
                    with state_lock:
                        exists = os.path.exists(json_path)
                        if exists and revalidate and result_is_stale(json_path, task["text"]):
                            log.log(report.STEP, "  重新翻译（源文已变化）%s/%s",
                                    task["story"], task["key"])
                            exists = False
                        if not args.overwrite and (exists or state_key in completed):
                            done_stories.add(task["story"])
                            stats["n"] += 1
                            log.log(report.STEP, "  跳过（已有结果）%s/%s",
                                    task["story"], task["key"])
                            return True
                    return False

                def _save(task, json_path, mp3_path, result, elapsed=None):
                    with open(json_path, "w", encoding="utf-8") as f:
                        json.dump(result, f, ensure_ascii=False, indent=2)
                    if not args.no_tts and not result.get("passthrough"):
                        # 纯符号（如 `……`）没有可朗读的内容，不生成语音
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
                        writer.mark()
                        done_stories.add(task["story"])
                        stats["n"] += 1
                    # 每份结果完成一行（--verbose 之外也可见）
                    cols = ["[%s]" % char_id, "%s/%s" % (task["story"], task["key"]),
                            "%d 字" % len(result.get("final") or ""), providers_of(result)]
                    if elapsed is not None:
                        cols.append("%.1fs" % elapsed)
                    log.info("  ".join(cols))

                def _process_unit(unit):
                    """处理一组任务（可被多线程并发调用；共享状态加锁），返回该组任务数。"""
                    if cancel is not None and cancel.is_set():
                        raise tc.Cancelled()
                    languages, chain_label = tc.resolve_languages(args)

                    def _extras(t):
                        return {"story": t["story"], "segment_index": t["index"],
                                "source_file": rel(os.path.join(base, t["story"], "zh.md")),
                                "mode": args.mode, "chain": chain_label}

                    todo, cached = [], []
                    for t in unit:
                        d, json_path, mp3_path = result_paths(char_id, t["story"], t["key"])
                        os.makedirs(d, exist_ok=True)
                        if _skip_it(t, json_path):
                            continue
                        if use_cache and t["key"] == "title":
                            key = title_cache_key(t["text"], args, providers, cfg)
                            with cache_lock:
                                hit = title_cache.get(key)
                            if hit:
                                cached.append((t, json_path, mp3_path, hit))
                                continue
                        todo.append((t, json_path, mp3_path))
                    for t, json_path, mp3_path, hit in cached:
                        res = dict(hit)
                        res.update(_extras(t))
                        _save(t, json_path, mp3_path, res)
                    if not todo:
                        return len(unit)
                    extras = [_extras(t) for t, _, _ in todo]
                    n = len(todo)
                    if n > 1:  # 批量组：开始/结束各一行，耗时记在组上
                        log.info(">>> %s：%d 条（%s）：%s", char_id, n,
                                 ",".join(sorted({t["story"] for t, _, _ in todo})),
                                 " → ".join(languages))
                    t0 = time.monotonic()
                    try:
                        if n > 1:
                            results = run_chain_many([t["text"] for t, _, _ in todo],
                                                     languages, providers, cfg, log, extras,
                                                     source_lang="zh-CN")
                        else:
                            results = [run_chain(t["text"], languages, providers, cfg, log,
                                                 extras[0], source_lang="zh-CN")
                                       for t, _, _ in todo]
                    except tc.Cancelled:
                        raise
                    except tc.QuotaExceeded:
                        raise
                    except tc.ChainError as exc:
                        if fail_fast:
                            raise
                        if is_fatal_local_error(exc, args, providers):
                            log.error("本地 RWKV 不可用，已中止本批：%s", exc)
                            raise
                        keys = [t["key"] for t, _, _ in todo]
                        failed_stories = sorted({t["story"] for t, _, _ in todo})
                        with state_lock:
                            failures.append({"char_id": char_id, "stories": failed_stories,
                                             "keys": keys,
                                             "error": "%s: %s" % (type(exc).__name__, exc)})
                        log.error("失败 %s/%s（%s）：%s；已记录，继续后续任务。",
                                  char_id, ",".join(failed_stories), ",".join(keys), exc)
                        return len(unit)
                    dt = time.monotonic() - t0
                    for (t, json_path, mp3_path), res in zip(todo, results):
                        _save(t, json_path, mp3_path, res, dt if n == 1 else None)
                        if use_cache and t["key"] == "title":
                            key = title_cache_key(t["text"], args, providers, cfg)
                            value = {k: v for k, v in res.items()
                                     if k not in ("story", "segment_index",
                                                  "source_file", "mode", "chain")}
                            with cache_lock:
                                title_cache[key] = value
                                cache_dirty["value"] = True
                    if n > 1:
                        log.info("<<< %s：%d 条完成，%.1fs", char_id, n, dt)
                    return len(unit)

                bar = tqdm(total=len(tasks), desc="翻译链", unit="份",
                           position=1 if len(char_ids) > 1 else 0,
                           leave=True, **report.bar_kwargs())

                def _tick():
                    _notify(event="progress", char_id=char_id, char_index=char_index,
                            chars=len(char_ids), tasks=len(tasks), done=int(bar.n))

                try:
                    if args.workers > 1:
                        with ThreadPoolExecutor(max_workers=args.workers) as ex:
                            futures = [ex.submit(_process_unit, u) for u in units]
                            for fut in as_completed(futures):
                                bar.update(fut.result())
                                writer.flush()
                                _tick()
                    else:
                        for u in units:
                            bar.update(_process_unit(u))
                            writer.flush()
                            _tick()
                finally:
                    bar.close()
                writer.flush(force=True)
                processed += stats["n"]
                _notify(event="char_done", char_id=char_id, char_index=char_index,
                        chars=len(char_ids), tasks=len(tasks), done=int(bar.n))

                for story in done_stories:
                    write_summary(char_id, story, provider_label, mode_label)
                _save_cache()

                if failures and len(failures) >= max_failures:
                    log.error("失败任务达到上限 %d（--max-failures），提前结束；"
                              "已完成进度已保存。", max_failures)
                    break
    except tc.Cancelled:
        writer.flush(force=True)
        log.warning("已取消；进度已保存（.translate_stories_state.json），重跑可续跑。")
        return _finish(ExitCode.CANCELLED)
    except tc.Paused as exc:
        writer.flush(force=True)
        log.error("已暂停：%s", exc)
        log.error("进度已保存（.translate_stories_state.json），修复后重跑即可续跑。")
        return _finish(ExitCode.PAUSED)
    except tc.ConnectionIssue as exc:
        writer.flush(force=True)
        log.error("后端连接失败：\n%s", exc)
        log.error("检查网络/代理后重跑即可续跑。")
        return _finish(ExitCode.CONNECTION)
    except tc.QuotaExceeded as exc:
        writer.flush(force=True)
        log.error("今日配额已耗尽：%s", exc)
        log.error("进度已保存（.translate_stories_state.json），次日重跑即可续跑。")
        return _finish(ExitCode.QUOTA)
    except tc.UnsupportedLanguage as exc:
        writer.flush(force=True)
        log.error("语言不受支持：%s", exc)
        return _finish(ExitCode.UNSUPPORTED_LANGUAGE)

    writer.flush(force=True)
    if failures:
        path = write_failures(failures)
        print("完成 %d 份结果，失败 %d 项（已记录，继续后续任务）。" % (processed, len(failures)))
        print("失败清单：%s" % rel(path))
        print("修复后重跑即可续跑（已完成项会跳过）；如希望失败即停用 --fail-fast。")
        return _finish(ExitCode.PARTIAL)
    clear_state()
    # 收尾摘要用 print：不受 -q 的日志级别过滤（`-q` = 只显示错误与最终摘要）
    if len(char_ids) == 1:
        print("完成。结果目录：%s" % rel(os.path.join(story_dir(char_ids[0]), "")))
    else:
        print("完成。处理 %d 个角色、%d 份结果，结果在各 {id}/result/ 下。"
              % (len(char_ids), processed))
    if logfile:
        print("日志：%s" % rel(logfile))
    return _finish(ExitCode.OK)
