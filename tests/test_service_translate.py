"""services.translate：批量任务、状态、并发与语音。"""

import json
import threading

import pytest

from story_tr import models
from story_tr.config import Settings
from story_tr.errors import (
    BadOutput,
    ConnectionFailed,
    ExitCode,
    ModelNotLoaded,
    RateLimited,
)
from story_tr.services import storage, translate
from story_tr.services.translate import chain_runner, runner
from story_tr.services.translate import (
    tasks as translate_tasks,
)
from story_tr.services.translate import tts as translate_tts
from tests.support import (
    FakeTranslator,
    ProbeTranslator,
)
from tests.support import (
    mock_translator as _mock_translator,
)
from tests.support import (
    setup_workspace as _setup_workspace,
)


@pytest.fixture(autouse=True)
def _fast_backoff(monkeypatch):
    """重试退避在测试中不真实等待。"""
    monkeypatch.setattr("story_tr.services.translate.retry.BACKOFF_BASE", 0.0)


def test_translate_writes_result(tmp_path, monkeypatch):
    _setup_workspace(tmp_path)
    fake = FakeTranslator(name="google", result="译文")
    _mock_translator(monkeypatch, fake)
    rc = translate.run(
        ["10000021"], stories="vision", mode="fixed", chain="asia", no_tts=True, data_dir=str(tmp_path)
    )
    assert rc == ExitCode.OK

    rdir = tmp_path / "data" / "story" / "character" / "10000021" / "result" / "vision"
    data = json.loads((rdir / "seg_01.json").read_text(encoding="utf-8"))
    assert data["source"] == "第一段"
    assert data["mode"] == "fixed"
    assert data["providers"] == ["google"]
    assert data["final"] == "译文"
    assert (rdir / "segments.json").exists()


def test_translate_resume_skips_existing(tmp_path, monkeypatch):
    _setup_workspace(tmp_path)
    fake = FakeTranslator(name="google", result="译文")
    _mock_translator(monkeypatch, fake)
    args = dict(stories="vision", mode="fixed", chain="asia", no_tts=True, data_dir=str(tmp_path))
    assert translate.run(["10000021"], **args) == ExitCode.OK
    first_calls = fake.calls
    assert translate.run(["10000021"], **args) == ExitCode.OK
    assert fake.calls == first_calls


def test_translate_signature_mismatch(tmp_path, monkeypatch):
    _setup_workspace(tmp_path)
    fake = FakeTranslator(name="google", result="译文")
    _mock_translator(monkeypatch, fake)
    (tmp_path / ".translate_stories_state.json").write_text(
        json.dumps(
            {
                "signature": {
                    "mode": "fixed",
                    "chain": "europe",
                    "steps": None,
                    "languages": None,
                    "providers": ["google", "bing", "baidu"],
                },
                "completed": [],
                "started_at": "2026-01-01T00:00:00",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    rc = translate.run(
        ["10000021"], stories="vision", mode="fixed", chain="asia", no_tts=True, data_dir=str(tmp_path)
    )
    assert rc == ExitCode.SIGNATURE


def test_translate_local_connection_failure(tmp_path, monkeypatch):
    _setup_workspace(tmp_path)
    fake = FakeTranslator(name="rwkv", error=ConnectionFailed("connection refused"))
    _mock_translator(monkeypatch, fake)
    rc = translate.run(
        ["10000021"],
        stories="vision",
        mode="rwkv20",
        no_tts=True,
        retry_rounds=0,
        data_dir=str(tmp_path),
    )
    assert rc == ExitCode.PARTIAL
    assert (tmp_path / ".translate_failures_state.json").exists()


def test_translate_model_not_loaded(tmp_path, monkeypatch):
    _setup_workspace(tmp_path)
    fake = FakeTranslator(name="rwkv", error=ModelNotLoaded("model not loaded"))
    _mock_translator(monkeypatch, fake)
    rc = translate.run(["10000021"], stories="vision", mode="rwkv20", no_tts=True, data_dir=str(tmp_path))
    assert rc == ExitCode.PAUSED


def test_translate_rwkv20_reads_english_source(tmp_path, monkeypatch):
    _setup_workspace(tmp_path)
    fake = FakeTranslator(name="rwkv", result="这是译文")
    _mock_translator(monkeypatch, fake)
    rc = translate.run(["10000021"], stories="vision", mode="rwkv20", no_tts=True, data_dir=str(tmp_path))
    assert rc == ExitCode.OK

    rdir = tmp_path / "data" / "story" / "character" / "10000021" / "result" / "vision"
    data = json.loads((rdir / "seg_01.json").read_text(encoding="utf-8"))
    assert data["source"] == "First paragraph"
    assert data["source_file"].endswith("en.md")
    assert [s["kind"] for s in data["steps"]] == ["rwkv20"]
    assert data["steps"][0]["text"] == "这是译文"


def test_translate_build_tasks(tmp_path):
    _setup_workspace(tmp_path)
    tasks = translate_tasks.build_tasks(str(tmp_path), "10000021", ["vision"], "both")
    keys = [t["key"] for t in tasks]
    assert "title" in keys and "full" in keys and "seg_01" in keys and "seg_02" in keys
    seg_only = translate_tasks.build_tasks(str(tmp_path), "10000021", ["vision"], "segments")
    assert [t["key"] for t in seg_only] == ["title", "seg_01", "seg_02"]
    full_only = translate_tasks.build_tasks(str(tmp_path), "10000021", ["vision"], "full")
    assert [t["key"] for t in full_only] == ["title", "full"]


def test_translate_result_is_stale(tmp_path):
    p = tmp_path / "r.json"
    p.write_text('{"source": "原文"}', encoding="utf-8")
    assert translate_tasks.result_is_stale(str(p), "原文") is False
    assert translate_tasks.result_is_stale(str(p), "变化") is True
    assert translate_tasks.result_is_stale(str(tmp_path / "none.json"), "原文") is True
    p.write_text("{bad", encoding="utf-8")
    assert translate_tasks.result_is_stale(str(p), "原文") is True


def test_translate_run_chain_passthrough():
    out = chain_runner.run_chain("……", ["ja"], [], ["google"], False, Settings())
    assert out["passthrough"] is True
    assert out["final"] == "……"


def test_translate_run_chain_detect_english():
    fake = FakeTranslator(name="google", result="你好")
    out = chain_runner.run_chain("hello world", ["ja"], [fake], ["google"], False, Settings())
    assert out["source_lang"] == "en"
    assert out["final"] == "你好"


def test_translate_usage_both_and_neither(tmp_path):
    assert translate.run([], data_dir=str(tmp_path)) == ExitCode.USAGE
    assert translate.run(["1"], all_characters=True, data_dir=str(tmp_path)) == ExitCode.USAGE


def test_translate_bad_provider_and_mode(tmp_path):
    _setup_workspace(tmp_path)
    assert (
        translate.run(["10000021"], mode="rwkv20", provider="google", data_dir=str(tmp_path))
        == ExitCode.USAGE
    )
    assert translate.run(["10000021"], mode="nope", data_dir=str(tmp_path)) == ExitCode.USAGE


def test_translate_missing_char_file(tmp_path):
    assert translate.run(["1"], data_dir=str(tmp_path)) == ExitCode.USAGE


def test_translate_no_match(tmp_path):
    _setup_workspace(tmp_path)
    assert translate.run(["999"], data_dir=str(tmp_path)) == ExitCode.USAGE


def test_translate_partial_failures(tmp_path, monkeypatch):
    _setup_workspace(tmp_path)
    _mock_translator(monkeypatch, FakeTranslator(name="google", error=BadOutput("bad")))
    rc = translate.run(
        ["10000021"],
        stories="vision",
        mode="fixed",
        chain="asia",
        no_tts=True,
        retry_rounds=0,
        data_dir=str(tmp_path),
    )
    assert rc == ExitCode.PARTIAL
    assert list((tmp_path / "logs").glob("failures_*.json"))


def test_translate_rate_limited_becomes_failure(tmp_path, monkeypatch):
    _setup_workspace(tmp_path)
    _mock_translator(monkeypatch, FakeTranslator(name="google", error=RateLimited("limited")))
    rc = translate.run(
        ["10000021"],
        stories="vision",
        mode="fixed",
        chain="asia",
        no_tts=True,
        retry_rounds=0,
        data_dir=str(tmp_path),
    )
    assert rc == ExitCode.PARTIAL


def test_translate_fail_fast(tmp_path, monkeypatch):
    _setup_workspace(tmp_path)
    _mock_translator(monkeypatch, FakeTranslator(name="google", error=BadOutput("bad")))
    rc = translate.run(
        ["10000021"],
        stories="vision",
        mode="fixed",
        chain="asia",
        no_tts=True,
        fail_fast=True,
        retry_rounds=0,
        data_dir=str(tmp_path),
    )
    assert rc == ExitCode.PARTIAL


def test_translate_max_failures(tmp_path, monkeypatch):
    _setup_workspace(tmp_path)
    _mock_translator(monkeypatch, FakeTranslator(name="google", error=BadOutput("bad")))
    rc = translate.run(
        ["10000021"],
        stories="vision",
        mode="fixed",
        chain="asia",
        no_tts=True,
        max_failures=1,
        retry_rounds=0,
        data_dir=str(tmp_path),
    )
    assert rc == ExitCode.PARTIAL


def test_translate_workers_concurrent(tmp_path, monkeypatch):
    _setup_workspace(tmp_path)
    _mock_translator(monkeypatch, FakeTranslator(name="google", result="译文"))
    rc = translate.run(
        ["10000021"],
        stories="vision",
        mode="fixed",
        chain="asia",
        workers=2,
        no_tts=True,
        data_dir=str(tmp_path),
    )
    assert rc == ExitCode.OK


def test_translate_cancel(tmp_path, monkeypatch):
    _setup_workspace(tmp_path)
    _mock_translator(monkeypatch, FakeTranslator(name="google", result="译文"))
    cancel = threading.Event()
    cancel.set()
    rc = translate.run(
        ["10000021"],
        stories="vision",
        mode="fixed",
        chain="asia",
        no_tts=True,
        data_dir=str(tmp_path),
        cancel=cancel,
    )
    assert rc == ExitCode.CANCELLED


def test_translate_tts_success_and_failure(tmp_path, monkeypatch):
    _setup_workspace(tmp_path)
    _mock_translator(monkeypatch, FakeTranslator(name="google", result="译文"))
    monkeypatch.setattr(translate_tts, "tts_bytes", lambda *a, **k: b"mp3")
    rc = translate.run(["10000021"], stories="vision", mode="fixed", chain="asia", data_dir=str(tmp_path))
    assert rc == ExitCode.OK
    rdir = tmp_path / "data" / "story" / "character" / "10000021" / "result" / "vision"
    assert (rdir / "seg_01.mp3").exists()


def test_translate_tts_retry_then_success(tmp_path, monkeypatch):
    _setup_workspace(tmp_path)
    _mock_translator(monkeypatch, FakeTranslator(name="google", result="译文"))
    calls = []

    def flaky(*args, **kwargs):
        calls.append(1)
        if len(calls) % 3:
            raise ConnectionFailed("tts down")
        return b"mp3"

    monkeypatch.setattr(translate_tts, "tts_bytes", flaky)
    monkeypatch.setattr(translate_tts, "TTS_BACKOFF", 0.0)
    rc = translate.run(["10000021"], stories="vision", mode="fixed", chain="asia", data_dir=str(tmp_path))
    assert rc == ExitCode.OK
    rdir = tmp_path / "data" / "story" / "character" / "10000021" / "result" / "vision"
    # vision：title + full + 两段 = 4 份，每份第 3 次尝试成功
    assert len(calls) == 12
    assert sorted(p.name for p in rdir.glob("*.mp3")) == ["full.mp3", "seg_01.mp3", "seg_02.mp3", "title.mp3"]
    assert not list((tmp_path / "logs").glob("tts_failures_*.json"))


def test_translate_tts_failure_is_logged(tmp_path, monkeypatch):
    _setup_workspace(tmp_path)
    _mock_translator(monkeypatch, FakeTranslator(name="google", result="译文"))

    def boom(*args, **kwargs):
        raise ConnectionFailed("tts down")

    monkeypatch.setattr(translate_tts, "tts_bytes", boom)
    monkeypatch.setattr(translate_tts, "TTS_BACKOFF", 0.0)
    rc = translate.run(
        ["10000021"],
        stories="vision",
        mode="fixed",
        chain="asia",
        retry_rounds=0,
        data_dir=str(tmp_path),
    )
    # 语音失败不影响翻译结果，但任务计入失败并返回 PARTIAL
    assert rc == ExitCode.PARTIAL
    rdir = tmp_path / "data" / "story" / "character" / "10000021" / "result" / "vision"
    assert (rdir / "seg_01.json").exists()
    assert not (rdir / "seg_01.mp3").exists()
    logs = list((tmp_path / "logs").glob("tts_failures_*.json"))
    assert len(logs) == 1
    rows = json.loads(logs[0].read_text(encoding="utf-8"))
    assert len(rows) == 4
    assert {r["stage"] for r in rows} == {"tts"}
    assert (tmp_path / ".translate_failures_state.json").exists()


def test_tts_only_fills_missing_and_refreshes_summary(tmp_path, monkeypatch):
    _setup_workspace(tmp_path)
    fake = FakeTranslator(name="google", result="译文")
    _mock_translator(monkeypatch, fake)
    args = dict(stories="vision", mode="fixed", chain="asia", data_dir=str(tmp_path))
    assert translate.run(["10000021"], no_tts=True, **args) == ExitCode.OK
    rdir = tmp_path / "data" / "story" / "character" / "10000021" / "result" / "vision"
    assert not list(rdir.glob("*.mp3"))

    translate_calls = fake.calls
    monkeypatch.setattr(translate_tts, "tts_bytes", lambda *a, **k: b"mp3")
    assert translate.run(["10000021"], tts_only=True, **args) == ExitCode.OK
    # 只补语音，不重译文本
    assert fake.calls == translate_calls
    assert sorted(p.name for p in rdir.glob("*.mp3")) == ["full.mp3", "seg_01.mp3", "seg_02.mp3", "title.mp3"]
    summary = json.loads((rdir / "segments.json").read_text(encoding="utf-8"))
    assert summary["title"]["mp3"] == "title.mp3"
    assert summary["full"]["mp3"] == "full.mp3"
    assert [s["mp3"] for s in summary["segments"]] == ["seg_01.mp3", "seg_02.mp3"]
    assert summary["provider"] == "google → bing → baidu"
    assert summary["mode"] == "fixed/asia"


def test_tts_only_skips_existing_without_overwrite(tmp_path, monkeypatch):
    _setup_workspace(tmp_path)
    _mock_translator(monkeypatch, FakeTranslator(name="google", result="译文"))
    monkeypatch.setattr(translate_tts, "tts_bytes", lambda *a, **k: b"old")
    args = dict(stories="vision", mode="fixed", chain="asia", data_dir=str(tmp_path))
    assert translate.run(["10000021"], **args) == ExitCode.OK
    rdir = tmp_path / "data" / "story" / "character" / "10000021" / "result" / "vision"

    monkeypatch.setattr(translate_tts, "tts_bytes", lambda *a, **k: b"new")
    assert translate.run(["10000021"], tts_only=True, **args) == ExitCode.OK
    assert (rdir / "seg_01.mp3").read_bytes() == b"old"
    assert translate.run(["10000021"], tts_only=True, overwrite=True, **args) == ExitCode.OK
    assert (rdir / "seg_01.mp3").read_bytes() == b"new"


def test_tts_only_records_failure(tmp_path, monkeypatch):
    _setup_workspace(tmp_path)
    _mock_translator(monkeypatch, FakeTranslator(name="google", result="译文"))
    args = dict(stories="vision", mode="fixed", chain="asia", data_dir=str(tmp_path))
    assert translate.run(["10000021"], no_tts=True, **args) == ExitCode.OK

    def boom(*a, **k):
        raise ConnectionFailed("tts down")

    monkeypatch.setattr(translate_tts, "tts_bytes", boom)
    monkeypatch.setattr(translate_tts, "TTS_BACKOFF", 0.0)
    assert translate.run(["10000021"], tts_only=True, retry_rounds=0, **args) == ExitCode.PARTIAL
    logs = list((tmp_path / "logs").glob("tts_failures_*.json"))
    assert len(logs) == 1
    assert len(json.loads(logs[0].read_text(encoding="utf-8"))) == 4
    assert (tmp_path / ".translate_failures_state.json").exists()


def test_tts_only_requires_existing_results(tmp_path, monkeypatch):
    _setup_workspace(tmp_path)
    _mock_translator(monkeypatch, FakeTranslator(name="google", result="译文"))
    assert translate.run(["10000021"], stories="vision", tts_only=True, data_dir=str(tmp_path)) == ExitCode.OK


def test_tts_only_conflicts_with_no_tts(tmp_path):
    assert (
        translate.run(["10000021"], stories="vision", tts_only=True, no_tts=True, data_dir=str(tmp_path))
        == ExitCode.USAGE
    )


def test_tts_only_cancel(tmp_path, monkeypatch):
    _setup_workspace(tmp_path)
    _mock_translator(monkeypatch, FakeTranslator(name="google", result="译文"))
    args = dict(stories="vision", mode="fixed", chain="asia", data_dir=str(tmp_path))
    assert translate.run(["10000021"], no_tts=True, **args) == ExitCode.OK
    calls = []

    def fake_tts(*a, **k):
        calls.append(1)
        return b"mp3"

    monkeypatch.setattr(translate_tts, "tts_bytes", fake_tts)
    cancel = threading.Event()
    cancel.set()
    assert (
        translate.run(["10000021"], tts_only=True, data_dir=str(tmp_path), cancel=cancel)
        == ExitCode.CANCELLED
    )
    assert calls == []


def test_translate_overwrite_and_revalidate(tmp_path, monkeypatch):
    _setup_workspace(tmp_path)
    fake = FakeTranslator(name="google", result="译文")
    _mock_translator(monkeypatch, fake)
    args = dict(stories="vision", mode="fixed", chain="asia", no_tts=True, data_dir=str(tmp_path))
    assert translate.run(["10000021"], **args) == ExitCode.OK
    first = fake.calls
    # 默认跳过已有结果
    assert translate.run(["10000021"], **args) == ExitCode.OK
    assert fake.calls == first
    # overwrite 强制重跑
    assert translate.run(["10000021"], overwrite=True, **args) == ExitCode.OK
    assert fake.calls > first


def test_translate_state_writes_are_throttled(tmp_path, monkeypatch):
    _setup_workspace(tmp_path)
    _mock_translator(monkeypatch, FakeTranslator(name="google", result="译文"))
    state_writes = []
    original = storage.atomic_write_json

    def counting(path, obj, **kwargs):
        if path.endswith(translate.STATE_FILENAME):
            state_writes.append(path)
        return original(path, obj, **kwargs)

    monkeypatch.setattr(storage, "atomic_write_json", counting)
    rc = translate.run(
        ["10000021"], stories="vision", mode="fixed", chain="asia", no_tts=True, data_dir=str(tmp_path)
    )
    assert rc == ExitCode.OK
    # vision 共 4 份结果：只在角色边界强制写 1 次（旧实现每份都写 = 4 次）
    assert len(state_writes) == 1


def test_translate_revalidate_overrides_completed_state(tmp_path, monkeypatch):
    _setup_workspace(tmp_path)
    fake = FakeTranslator(name="google", result="译文")
    _mock_translator(monkeypatch, fake)
    rdir = tmp_path / "data" / "story" / "character" / "10000021" / "result" / "vision"
    rdir.mkdir(parents=True)
    (rdir / "seg_01.json").write_text(json.dumps({"source": "旧内容", "final": "旧"}), encoding="utf-8")
    providers, _ = storage.resolve_providers(None, "fixed")
    plan, _ = runner.build_plan(
        char_ids=["10000021"],
        all_characters=False,
        stories="vision",
        mode="fixed",
        chain="asia",
        languages="",
        steps=20,
        provider=None,
        workers=1,
        min_interval=0.0,
        balance=False,
        full_only=False,
        segments_only=False,
        overwrite=False,
        reset=False,
        revalidate=True,
        fail_fast=False,
        max_failures=20,
        no_tts=True,
        tts_only=False,
        data_dir=str(tmp_path),
        on_progress=None,
    )
    assert plan is not None
    sig = runner.plan_signature(plan)
    state = models.BatchState(
        signature=sig, completed=["10000021|vision|seg_01"], started_at="2026-01-01T00:00:00"
    )
    storage.atomic_write_json(str(tmp_path / translate.STATE_FILENAME), state)

    rc = translate.run(
        ["10000021"],
        stories="vision",
        mode="fixed",
        chain="asia",
        no_tts=True,
        revalidate=True,
        data_dir=str(tmp_path),
    )
    assert rc == ExitCode.OK
    # 4 份结果 × 22 步（to_en + 20 步 + to_zh）；若被 completed 拦截则为 3 × 22
    assert fake.calls == 4 * 22
    row = json.loads((rdir / "seg_01.json").read_text(encoding="utf-8"))
    assert row["source"] == "第一段"


def test_translate_no_cache_retranslates_missing_file(tmp_path, monkeypatch):
    """--no-cache：忽略 state.completed 记录，文件缺失的任务会被重译。"""
    _setup_workspace(tmp_path)
    fake = FakeTranslator(name="google", result="译文")
    _mock_translator(monkeypatch, fake)
    args = dict(stories="vision", mode="fixed", chain="asia", no_tts=True, data_dir=str(tmp_path))
    assert translate.run(["10000021"], **args) == ExitCode.OK
    first = fake.calls
    rdir = tmp_path / "data" / "story" / "character" / "10000021" / "result" / "vision"
    (rdir / "seg_01.json").unlink()
    # 造一个「中断遗留」的批次状态：seg_01 记为完成，但文件已被删
    plan, _ = runner.build_plan(
        char_ids=["10000021"],
        all_characters=False,
        stories="vision",
        mode="fixed",
        chain="asia",
        languages="",
        steps=20,
        provider=None,
        workers=1,
        min_interval=0.0,
        balance=False,
        full_only=False,
        segments_only=False,
        overwrite=False,
        reset=False,
        revalidate=False,
        fail_fast=False,
        max_failures=20,
        no_tts=True,
        tts_only=False,
        data_dir=str(tmp_path),
        on_progress=None,
    )
    assert plan is not None
    state = models.BatchState(
        signature=runner.plan_signature(plan),
        completed=["10000021|vision|seg_01"],
        started_at="2026-01-01T00:00:00",
    )

    def write_state():
        storage.atomic_write_json(str(tmp_path / translate.STATE_FILENAME), state)

    # 默认：state 里仍记为完成 → 不重译（成功后状态文件会被删除，需再写一次）
    write_state()
    assert translate.run(["10000021"], **args) == ExitCode.OK
    assert fake.calls == first
    # --no-cache：忽略 state → 只重译缺失的 seg_01（22 步）
    write_state()
    assert translate.run(["10000021"], no_cache=True, **args) == ExitCode.OK
    assert fake.calls == first + 22


def test_translate_no_cache_still_skips_existing_files(tmp_path, monkeypatch):
    """--no-cache 只绕过状态记录，仍然按磁盘文件跳过已有结果。"""
    _setup_workspace(tmp_path)
    fake = FakeTranslator(name="google", result="译文")
    _mock_translator(monkeypatch, fake)
    args = dict(stories="vision", mode="fixed", chain="asia", no_tts=True, data_dir=str(tmp_path))
    assert translate.run(["10000021"], **args) == ExitCode.OK
    first = fake.calls
    assert translate.run(["10000021"], no_cache=True, **args) == ExitCode.OK
    assert fake.calls == first


def test_translate_preflight_unreachable_returns_connection(tmp_path, monkeypatch):
    _setup_workspace(tmp_path)
    fake = ProbeTranslator(name="rwkv", probe={"reachable": False, "error": "down"})
    _mock_translator(monkeypatch, fake)
    rc = translate.run(["10000021"], stories="vision", mode="rwkv20", no_tts=True, data_dir=str(tmp_path))
    assert rc == ExitCode.CONNECTION
    assert fake.calls == 0


def test_translate_preflight_offline_returns_paused(tmp_path, monkeypatch):
    _setup_workspace(tmp_path)
    fake = ProbeTranslator(name="rwkv", probe={"reachable": True, "status": "Offline"})
    _mock_translator(monkeypatch, fake)
    rc = translate.run(["10000021"], stories="vision", mode="rwkv20", no_tts=True, data_dir=str(tmp_path))
    assert rc == ExitCode.PAUSED
    assert fake.calls == 0


def test_translate_preflight_working_proceeds(tmp_path, monkeypatch):
    _setup_workspace(tmp_path)
    fake = ProbeTranslator(name="rwkv", result="这是译文")
    _mock_translator(monkeypatch, fake)
    rc = translate.run(["10000021"], stories="vision", mode="rwkv20", no_tts=True, data_dir=str(tmp_path))
    assert rc == ExitCode.OK
    assert fake.calls > 0


def test_translate_preflight_loading_proceeds(tmp_path, monkeypatch):
    _setup_workspace(tmp_path)
    fake = ProbeTranslator(name="rwkv", result="这是译文", probe={"reachable": True, "status": "Loading"})
    _mock_translator(monkeypatch, fake)
    rc = translate.run(["10000021"], stories="vision", mode="rwkv20", no_tts=True, data_dir=str(tmp_path))
    assert rc == ExitCode.OK
    assert fake.calls > 0


def test_translate_invalid_result_tag(tmp_path):
    _setup_workspace(tmp_path)
    assert translate.run(["10000021"], result_tag="a/b", data_dir=str(tmp_path)) == ExitCode.USAGE


def test_translate_writes_result_tag_dir(tmp_path, monkeypatch):
    _setup_workspace(tmp_path)
    fake = FakeTranslator(name="google", result="译文")
    _mock_translator(monkeypatch, fake)
    rc = translate.run(
        ["10000021"],
        stories="vision",
        mode="fixed",
        chain="asia",
        no_tts=True,
        result_tag="cloud",
        data_dir=str(tmp_path),
    )
    assert rc == ExitCode.OK
    char_dir = tmp_path / "data" / "story" / "character" / "10000021"
    assert (char_dir / "result-cloud" / "vision" / "seg_01.json").exists()
    assert (char_dir / "result-cloud" / "vision" / "segments.json").exists()
    assert not (char_dir / "result").exists()


def test_translate_result_tag_change_requires_reset(tmp_path, monkeypatch):
    _setup_workspace(tmp_path)
    fake = FakeTranslator(name="google", result="译文")
    _mock_translator(monkeypatch, fake)
    plan, _ = runner.build_plan(
        char_ids=["10000021"],
        all_characters=False,
        stories="vision",
        mode="fixed",
        chain="asia",
        languages="",
        steps=20,
        provider=None,
        workers=1,
        min_interval=0.0,
        balance=False,
        full_only=False,
        segments_only=False,
        overwrite=False,
        reset=False,
        revalidate=False,
        fail_fast=False,
        max_failures=20,
        no_tts=True,
        tts_only=False,
        data_dir=str(tmp_path),
        on_progress=None,
        result_tag="cloud",
    )
    assert plan is not None
    storage.atomic_write_json(
        str(tmp_path / translate.STATE_FILENAME),
        models.BatchState(
            signature=runner.plan_signature(plan),
            completed=[],
            started_at="2026-01-01T00:00:00",
        ),
    )
    base = dict(stories="vision", mode="fixed", chain="asia", no_tts=True, data_dir=str(tmp_path))
    assert translate.run(["10000021"], **base) == ExitCode.SIGNATURE
    assert translate.run(["10000021"], result_tag="cloud", reset=True, **base) == ExitCode.OK


def test_reconcile_rebuilds_state_from_disk(tmp_path):
    _setup_workspace(tmp_path)
    rdir = tmp_path / "data" / "story" / "character" / "10000021" / "result" / "vision"
    rdir.mkdir(parents=True)
    for key in ("title", "full", "seg_01"):
        (rdir / f"{key}.json").write_text(
            json.dumps({"mode": "fixed", "chain": "asia", "final": "x"}), encoding="utf-8"
        )
    # 异构链路的结果不计入完成记录
    (rdir / "seg_02.json").write_text(json.dumps({"mode": "rwkv20", "chain": "rwkv20"}), encoding="utf-8")
    (rdir / "segments.json").write_text("{}", encoding="utf-8")

    plan, _ = runner.build_plan(
        char_ids=["10000021"],
        all_characters=False,
        stories="vision",
        mode="fixed",
        chain="asia",
        languages="",
        steps=20,
        provider=None,
        workers=1,
        min_interval=0.0,
        balance=False,
        full_only=False,
        segments_only=False,
        overwrite=False,
        reset=False,
        revalidate=False,
        fail_fast=False,
        max_failures=20,
        no_tts=True,
        tts_only=False,
        data_dir=str(tmp_path),
        on_progress=None,
    )
    assert plan is not None
    state = models.BatchState(
        signature=runner.plan_signature(plan),
        completed=["10000021|vision|stale", "10000021|vision|seg_01"],
        started_at="2026-01-01T00:00:00",
    )
    storage.atomic_write_json(str(tmp_path / translate.STATE_FILENAME), state)

    batch, code = runner._Batch.create(plan, [])
    assert code is None and batch is not None
    batch.reconcile()
    raw = storage.load_json_with_backup(str(tmp_path / translate.STATE_FILENAME))
    assert set(raw["completed"]) == {
        "10000021|vision|title",
        "10000021|vision|full",
        "10000021|vision|seg_01",
    }


def test_reconcile_without_any_results_does_not_write_state(tmp_path):
    _setup_workspace(tmp_path)
    plan, _ = runner.build_plan(
        char_ids=["10000021"],
        all_characters=False,
        stories="vision",
        mode="fixed",
        chain="asia",
        languages="",
        steps=20,
        provider=None,
        workers=1,
        min_interval=0.0,
        balance=False,
        full_only=False,
        segments_only=False,
        overwrite=False,
        reset=False,
        revalidate=False,
        fail_fast=False,
        max_failures=20,
        no_tts=True,
        tts_only=False,
        data_dir=str(tmp_path),
        on_progress=None,
    )
    assert plan is not None
    batch, code = runner._Batch.create(plan, [])
    assert code is None and batch is not None
    batch.reconcile()
    assert not (tmp_path / translate.STATE_FILENAME).exists()
