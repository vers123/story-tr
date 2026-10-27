"""批量翻译编排：把 `core` 路线与 `transport` 后端组装成用例。

- 读 `data/character.json`、解析 id/范围、选故事
- 按 `core.routes.build_tasks` 与 `core.routes.resolve_languages` 跑链
- 结果落 `result/{story}/{title,full,seg_NN}.json`（`TranslationResult`）+ `segments.json` 汇总 + 可选 mp3
- 续跑：`BatchState` 存 `.translate_stories_state.json`，签名不一致要求 `--reset`
"""
from __future__ import annotations

import contextlib
import hashlib
import itertools
import json
import logging
import os
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any

from story_tr import models, transport
from story_tr.config import Settings, load_settings
from story_tr.core import (
    build_signature,
    build_tasks,
    detect_language,
    has_features,
    has_han,
    parse_frontmatter,
    resolve_languages,
    signatures_match,
    split_paragraphs,
)
from story_tr.errors import (
    BadOutput,
    ConnectionFailed,
    ExitCode,
    ModelNotLoaded,
    QuotaExceeded,
    RateLimited,
    UnsupportedLanguage,
)
from story_tr.transport.tts import tts_bytes

from . import _io

log = logging.getLogger("story_tr.services.translate")

TARGET_ZH = "zh-CN"
STATE_FILENAME = ".translate_stories_state.json"

_balance_cursor = itertools.count()
_balance_lock = threading.Lock()


class _Abort(Exception):
    """把致命错误/取消转成退出码的控制流异常。"""

    def __init__(self, code: ExitCode, reason: str = "") -> None:
        super().__init__(reason)
        self.code = code
        self.reason = reason


# --------------------------------------------------------------------------- #
# 签名与后端
# --------------------------------------------------------------------------- #
def _rwkv_signature(settings: Settings) -> dict[str, Any]:
    template = settings.rwkv_prompt_template or ""
    return {
        "url": (settings.rwkv_api_url or "http://127.0.0.1:8000").rstrip("/"),
        "model": "rwkv",
        "template_sha1": hashlib.sha1(template.encode("utf-8")).hexdigest(),
        "max_tokens": settings.rwkv_max_tokens,
        "temperature": settings.rwkv_temperature,
        "top_p": settings.rwkv_top_p,
        "chunk_chars": settings.rwkv_chunk_chars,
    }


def _signature(mode: str, chain: str, languages: str, steps: int,
               providers: list[str], settings: Settings) -> dict[str, Any]:
    rwkv_sig = _rwkv_signature(settings) if (mode == "rwkv20" or "rwkv" in providers) else None
    return build_signature(mode=mode, chain=chain, languages=languages, steps=steps,
                           providers=providers, rwkv_signature=rwkv_sig)


def _order(translators: list[transport.Translator], providers: list[str],
           balance: bool) -> list[tuple[str, transport.Translator]]:
    pairs = list(zip(providers, translators, strict=False))
    if not balance or len(pairs) < 2:
        return pairs
    with _balance_lock:
        i = next(_balance_cursor) % len(pairs)
    return pairs[i:] + pairs[:i]


def _rwkv_meta(translator: transport.Translator, settings: Settings) -> models.RwkvMeta | None:
    if getattr(translator, "name", None) != "rwkv":
        return None
    sig = _rwkv_signature(settings)
    return models.RwkvMeta(
        url=sig["url"], model=sig["model"], template_sha1=sig["template_sha1"],
        chunks=int(getattr(translator, "last_chunks", 0) or 0),
        prompt_tokens=int(getattr(translator, "last_prompt_tokens", 0) or 0),
        completion_tokens=int(getattr(translator, "last_completion_tokens", 0) or 0),
    )


def _translate_step(text: str, src: str, tgt: str, translators: list[transport.Translator],
                    providers: list[str], balance: bool,
                    settings: Settings) -> tuple[str, str, models.RwkvMeta | None]:
    reasons: list[str] = []
    last_bad: BadOutput | None = None
    for pname, translator in _order(translators, providers, balance):
        ok, reason = translator.available()
        if not ok:
            reasons.append(f"{pname} 不可用：{reason}")
            continue
        try:
            out = translator.translate(text, src, tgt)
        except UnsupportedLanguage:
            raise
        except QuotaExceeded:
            raise
        except ModelNotLoaded:
            raise
        except ConnectionFailed as exc:
            reasons.append(f"{pname} 连接失败：{exc}")
            continue
        except RateLimited as exc:
            reasons.append(f"{pname} 被限流：{exc}")
            continue
        except BadOutput as exc:
            last_bad = exc
            reasons.append(f"{pname} 业务失败：{exc}")
            continue
        out = (out or "").strip()
        if not out:
            last_bad = BadOutput(f"{pname} 返回空结果")
            reasons.append(str(last_bad))
            continue
        return pname, out, _rwkv_meta(translator, settings)
    if last_bad is not None:
        raise BadOutput("所有后端业务失败：\n    - " + "\n    - ".join(reasons))
    raise ConnectionFailed("所有后端连接失败：\n    - " + "\n    - ".join(reasons))


def _merge_rwkv(steps: list[models.TranslationStep]) -> models.RwkvMeta | None:
    metas = [s.rwkv for s in steps if s.rwkv is not None]
    if not metas:
        return None
    first = metas[0]
    return models.RwkvMeta(
        url=first.url, model=first.model, template_sha1=first.template_sha1,
        chunks=sum(m.chunks for m in metas),
        prompt_tokens=sum(m.prompt_tokens for m in metas),
        completion_tokens=sum(m.completion_tokens for m in metas),
    )


def _run_chain(text: str, languages: list[str], translators: list[transport.Translator],
               providers: list[str], balance: bool, settings: Settings,
               source_lang: str | None = None) -> dict[str, Any]:
    started = datetime.now().isoformat(timespec="seconds")
    if not has_features(text):
        return {"source": text, "source_lang": "none", "target_lang": TARGET_ZH,
                "languages": list(languages), "steps": [], "final": text,
                "passthrough": True, "rwkv": None,
                "started_at": started, "finished_at": started}
    det = "en" if list(languages) == ["rwkv20"] else (
        source_lang if (source_lang and has_han(text)) else detect_language(text))
    tasks = build_tasks(det, languages)
    steps: list[models.TranslationStep] = []
    cur = text
    for k, task in enumerate(tasks):
        provider, out, meta = _translate_step(cur, str(task["src"]), str(task["tgt"]),
                                              translators, providers, balance, settings)
        steps.append(models.TranslationStep(index=k, kind=str(task["kind"]), lang=str(task["lang"]),
                                            provider=provider, text=out, rwkv=meta))
        cur = out
    return {"source": text, "source_lang": det, "target_lang": TARGET_ZH,
            "languages": list(languages), "steps": steps, "final": cur,
            "passthrough": False, "rwkv": _merge_rwkv(steps),
            "started_at": started, "finished_at": datetime.now().isoformat(timespec="seconds")}


def _providers_of(steps: list[models.TranslationStep]) -> list[str]:
    seen: list[str] = []
    for s in steps:
        if s.provider and s.provider not in seen:
            seen.append(s.provider)
    return seen


# --------------------------------------------------------------------------- #
# 输入解析
# --------------------------------------------------------------------------- #
def _load_char_ids(home_: str) -> list[str]:
    with open(_io.char_index_path(home_), encoding="utf-8") as f:
        data = json.load(f)
    return [c["id"] for c in data if c.get("id")]


def _parse_char_ids(tokens: list[str], all_ids: list[str]) -> list[str]:
    want: set[str] = set()
    for tok in tokens:
        for part in tok.replace(" ", "").split(","):
            if not part:
                continue
            if "-" in part:
                a, b = part.split("-", 1)
                try:
                    start, end = int(a), int(b)
                except ValueError:
                    raise ValueError(f"无法解析 id 范围：{part}") from None
                if start > end:
                    start, end = end, start
                want.update(str(i) for i in range(start, end + 1))
            else:
                want.add(part)
    missing = sorted(want - set(all_ids))
    if missing:
        log.warning("提示：以下 id 不在 character.json 中，已跳过：%s", ",".join(missing))
    return [i for i in all_ids if i in want]


def _read_paragraphs(md_path: str) -> list[str]:
    with open(md_path, encoding="utf-8") as f:
        raw = f.read()
    _, body = parse_frontmatter(raw)
    return split_paragraphs(body)


def _read_title(md_path: str) -> str | None:
    with open(md_path, encoding="utf-8") as f:
        raw = f.read()
    fm, _ = parse_frontmatter(raw)
    if not fm:
        return None
    return fm.get("title") or None


def _parts_label(full_only: bool, segments_only: bool) -> str:
    if full_only:
        return "full"
    if segments_only:
        return "segments"
    return "both"


def _resolve_stories(home_: str, cid: str, names: list[str], src_file: str = "zh.md") -> list[str]:
    base = _io.profile_story_dir(home_, cid)
    if names:
        return names
    if not os.path.isdir(base):
        return []
    out: list[str] = []
    for n in sorted(os.listdir(base)):
        if os.path.isdir(os.path.join(base, n)):
            md = os.path.join(base, n, src_file)
            if os.path.exists(md) and _read_paragraphs(md):
                out.append(n)
    return out


def _build_tasks(home_: str, cid: str, stories: list[str], parts: str,
                 src_file: str = "zh.md") -> list[dict[str, Any]]:
    base = _io.profile_story_dir(home_, cid)
    tasks: list[dict[str, Any]] = []
    for story in stories:
        md = os.path.join(base, story, src_file)
        if not os.path.exists(md):
            log.warning("跳过 %s/%s：找不到 %s", cid, story, _io.rel(home_, md))
            continue
        paras = _read_paragraphs(md)
        if not paras:
            log.warning("跳过 %s/%s：%s 无正文", cid, story, src_file)
            continue
        title = _read_title(md)
        if title:
            tasks.append({"story": story, "key": "title", "index": None, "text": title})
        if parts in ("both", "full"):
            tasks.append({"story": story, "key": "full", "index": None,
                          "text": "\n\n".join(paras)})
        if parts in ("both", "segments"):
            for i, para in enumerate(paras, 1):
                tasks.append({"story": story, "key": f"seg_{i:02d}", "index": i, "text": para})
    return tasks


def _result_is_stale(json_path: str, source: str) -> bool:
    try:
        with open(json_path, encoding="utf-8") as f:
            row = json.load(f)
    except (OSError, ValueError):
        return True
    return (row.get("source") or "").strip() != (source or "").strip()


def _write_failures(home_: str, failures: list[dict[str, Any]]) -> str:
    os.makedirs(_io.logs_dir(home_), exist_ok=True)
    path = os.path.join(_io.logs_dir(home_),
                        "failures_{}.json".format(datetime.now().strftime("%Y%m%d_%H%M%S")))
    _io.atomic_write_json(path, failures)
    return path


def _write_summary(home_: str, cid: str, story: str, provider_label: str, mode_label: str,
                   src_file: str = "zh.md") -> None:
    d = os.path.join(_io.result_dir(home_, cid), story)
    if not os.path.isdir(d):
        return
    summary: dict[str, Any] = {
        "id": cid, "story": story,
        "source_file": _io.rel(home_, os.path.join(_io.profile_story_dir(home_, cid), story, src_file)),
        "provider": provider_label, "mode": mode_label,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "full": None, "title": None, "segments": [],
    }

    def _entry(key: str) -> dict[str, Any] | None:
        p = os.path.join(d, key + ".json")
        if not os.path.exists(p):
            return None
        with open(p, encoding="utf-8") as f:
            r = json.load(f)
        return {"source": r["source"], "languages": r["languages"], "final": r["final"],
                "json": key + ".json",
                "mp3": (key + ".mp3") if os.path.exists(os.path.join(d, key + ".mp3")) else None}

    summary["full"] = _entry("full")
    summary["title"] = _entry("title")
    for name in sorted(n for n in os.listdir(d) if n.startswith("seg_") and n.endswith(".json")):
        key = name[:-5]
        p = os.path.join(d, name)
        with open(p, encoding="utf-8") as f:
            r = json.load(f)
        summary["segments"].append({
            "index": r.get("segment_index"), "source": r["source"], "languages": r["languages"],
            "final": r["final"], "json": name,
            "mp3": (key + ".mp3") if os.path.exists(os.path.join(d, key + ".mp3")) else None})
    _io.atomic_write_json(os.path.join(d, "segments.json"), summary)


# --------------------------------------------------------------------------- #
# run
# --------------------------------------------------------------------------- #
def run(
    char_ids: list[str],
    all_characters: bool = False,
    stories: str = "",
    mode: str = "fixed",
    chain: str = "asia",
    languages: str = "",
    steps: int = 20,
    provider: str | None = None,
    workers: int = 1,
    min_interval: float = 0.3,
    balance: bool = False,
    full_only: bool = False,
    segments_only: bool = False,
    overwrite: bool = False,
    reset: bool = False,
    revalidate: bool = False,
    no_cache: bool = False,
    fail_fast: bool = False,
    max_failures: int = 20,
    no_tts: bool = False,
    data_dir: str | None = None,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
    cancel: Any = None,
) -> ExitCode:
    if char_ids and all_characters:
        log.error("不能同时指定角色 id 与 --all-characters，请二选一。")
        return ExitCode.USAGE
    if not char_ids and not all_characters:
        log.error("请指定角色 id / 范围，或用 --all-characters 处理全部角色。")
        return ExitCode.USAGE

    home_ = _io.home(data_dir)
    settings = load_settings()
    providers, err = _io.resolve_providers(provider, mode)
    if err:
        log.error(err)
        return ExitCode.USAGE
    try:
        langs, chain_label = resolve_languages(mode, chain, languages, steps)
    except ValueError as exc:
        log.error(str(exc))
        return ExitCode.USAGE

    char_file = _io.char_index_path(home_)
    if not os.path.exists(char_file):
        log.error("找不到 %s；请先运行 fetch。", char_file)
        return ExitCode.USAGE
    all_ids = _load_char_ids(home_)
    try:
        selected = all_ids if all_characters else _parse_char_ids(list(char_ids), all_ids)
    except ValueError as exc:
        log.error(str(exc))
        return ExitCode.USAGE
    if not selected:
        log.error("没有匹配到任何角色 id；请对照 data/character.json 检查。")
        return ExitCode.USAGE

    translators = _io.build_translators(providers, settings)
    for tr in translators:
        tr.min_interval = max(  # type: ignore[attr-defined]
            float(getattr(tr, "min_interval", 0.0) or 0.0), float(min_interval))

    sig = _signature(mode, chain, languages, steps, providers, settings)
    state_path = os.path.join(home_, STATE_FILENAME)
    raw = _io.load_json_with_backup(state_path)
    prev_state = models.BatchState.model_validate(raw) if raw else None
    if prev_state and not reset and not signatures_match(prev_state.signature, sig):
        log.error("参数与上次批量任务不一致，无法续跑（会与已有进度冲突）；如需按新参数重跑，请加 --reset。")
        log.error("差异：%s", prev_state.signature)
        return ExitCode.SIGNATURE
    completed: set[str] = set(prev_state.completed) if (prev_state and not reset) else set()
    started_at = prev_state.started_at if prev_state else datetime.now().isoformat(timespec="seconds")
    state = models.BatchState(signature=sig, completed=sorted(completed), started_at=started_at)

    parts = _parts_label(full_only, segments_only)
    src_file = "en.md" if mode == "rwkv20" else "zh.md"
    provider_label = providers[0] if len(providers) == 1 else " → ".join(providers)
    mode_label = mode + ("/" + chain if mode == "fixed" else "")

    state_lock = threading.Lock()
    done_lock = threading.Lock()
    failures: list[dict[str, Any]] = []
    stats = {"n": 0}

    def _emit(**kw: Any) -> None:
        if on_progress is not None:
            with contextlib.suppress(Exception):
                on_progress(kw)

    def _save_state() -> None:
        with state_lock:
            state.updated_at = datetime.now().isoformat(timespec="seconds")
            state.completed = sorted(completed)
            _io.atomic_write_json(state_path, state)

    def _process_task(task: dict[str, Any], cid: str, base: str) -> None:
        d = os.path.join(_io.result_dir(home_, cid), task["story"])
        os.makedirs(d, exist_ok=True)
        json_path = os.path.join(d, task["key"] + ".json")
        mp3_path = os.path.join(d, task["key"] + ".mp3")
        state_key = "{}|{}|{}".format(cid, task["story"], task["key"])

        with state_lock:
            exists = os.path.exists(json_path)
            if exists and revalidate and _result_is_stale(json_path, task["text"]):
                log.info("  重新翻译（源文已变化）%s/%s", task["story"], task["key"])
                exists = False
            if not overwrite and (exists or state_key in completed):
                with done_lock:
                    stats["n"] += 1
                return

        try:
            result = _run_chain(task["text"], langs, translators, providers, balance,
                                settings, source_lang=TARGET_ZH)
        except QuotaExceeded as exc:
            raise _Abort(ExitCode.QUOTA, str(exc)) from None
        except UnsupportedLanguage as exc:
            raise _Abort(ExitCode.UNSUPPORTED_LANGUAGE, str(exc)) from None
        except ModelNotLoaded as exc:
            raise _Abort(ExitCode.PAUSED, str(exc)) from None
        except ConnectionFailed as exc:
            raise _Abort(ExitCode.CONNECTION, str(exc)) from None
        except BadOutput as exc:
            failure = {"char_id": cid, "stories": [task["story"]], "keys": [task["key"]],
                       "error": f"{type(exc).__name__}: {exc}"}
            with done_lock:
                failures.append(failure)
            log.error("失败 %s/%s（%s）：%s；已记录，继续后续任务。",
                      cid, task["story"], task["key"], exc)
            if fail_fast:
                raise _Abort(ExitCode.PARTIAL, str(exc)) from None
            return

        tr = models.TranslationResult(
            source=result["source"], source_lang=result["source_lang"],
            target_lang=result["target_lang"], mode=mode, providers=_providers_of(result["steps"]),
            languages=result["languages"], steps=result["steps"], final=result["final"],
            chain=chain_label, passthrough=result.get("passthrough", False),
            rwkv=result.get("rwkv"), story=task["story"], segment_index=task["index"],
            source_file=_io.rel(home_, os.path.join(base, task["story"], src_file)),
            started_at=result.get("started_at"), finished_at=result.get("finished_at"))
        _io.atomic_write_json(json_path, tr)
        if not no_tts and not tr.passthrough:
            try:
                audio = tts_bytes(tr.final, TARGET_ZH, settings.proxy or "")
                with open(mp3_path, "wb") as f:
                    f.write(audio)
            except Exception as exc:  # noqa: BLE001 - 语音失败不影响翻译结果
                log.warning("语音生成失败（%s/%s/%s）：%s", cid, task["story"], task["key"], exc)
        with state_lock:
            completed.add(state_key)
        with done_lock:
            stats["n"] += 1
        log.info("[%s] %s/%s  %d 字", cid, task["story"], task["key"], len(tr.final))

    try:
        for char_index, cid in enumerate(selected, 1):
            if cancel is not None and cancel.is_set():
                raise _Abort(ExitCode.CANCELLED)
            base = _io.profile_story_dir(home_, cid)
            story_names = _resolve_stories(home_, cid,
                                           [s.strip() for s in stories.split(",") if s.strip()],
                                           src_file)
            tasks = _build_tasks(home_, cid, story_names, parts, src_file) if story_names else []
            if not tasks:
                log.warning("跳过 %s：没有可处理的故事", cid)
                continue

            log.info("角色 %s，故事 %s，共 %d 份结果（%s）", cid, ",".join(story_names),
                     len(tasks), mode_label)
            _emit(event="char_start", char_id=cid, char_index=char_index,
                  chars=len(selected), tasks=len(tasks), done=0)

            def _finish_task(done: int, cid: str, char_index: int,
                             tasks: list[dict[str, Any]]) -> None:
                _emit(event="progress", char_id=cid, char_index=char_index,
                      chars=len(selected), tasks=len(tasks), done=done)

            if workers > 1:
                with ThreadPoolExecutor(max_workers=max(1, int(workers))) as ex:
                    futs = [ex.submit(_process_task, t, cid, base) for t in tasks]
                    for done, fut in enumerate(as_completed(futs), 1):
                        fut.result()  # 可能抛 _Abort
                        _save_state()
                        _finish_task(done, cid, char_index, tasks)
            else:
                for done, t in enumerate(tasks, 1):
                    if cancel is not None and cancel.is_set():
                        raise _Abort(ExitCode.CANCELLED)
                    _process_task(t, cid, base)
                    _save_state()
                    _finish_task(done, cid, char_index, tasks)

            done_stories = {t["story"] for t in tasks if os.path.exists(
                os.path.join(_io.result_dir(home_, cid), t["story"], t["key"] + ".json"))}
            for story in done_stories:
                _write_summary(home_, cid, story, provider_label, mode_label, src_file)
            _emit(event="char_done", char_id=cid, char_index=char_index,
                  chars=len(selected), tasks=len(tasks), done=len(tasks))

            if failures and len(failures) >= max(1, int(max_failures)):
                log.error("失败任务达到上限 %d（--max-failures），提前结束；已完成进度已保存。",
                          max(1, int(max_failures)))
                raise _Abort(ExitCode.PARTIAL)
    except _Abort as exc:
        _save_state()
        log.warning("已中止：%s", exc.reason)
        return exc.code

    if failures:
        path = _write_failures(home_, failures)
        log.info("完成 %d 份结果，失败 %d 项（已记录）。失败清单：%s",
                 stats["n"], len(failures), _io.rel(home_, path))
        return ExitCode.PARTIAL

    _io.remove_with_backup(state_path)
    log.info("完成。处理 %d 个角色、%d 份结果。", len(selected), stats["n"])
    return ExitCode.OK
