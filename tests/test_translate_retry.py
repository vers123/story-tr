"""失败登记与自动重试：轮数、退避、TTS 补生成与登记生命周期。"""

import json

import pytest

from story_tr import models
from story_tr.errors import BadOutput, ConnectionFailed, ExitCode
from story_tr.services import translate
from story_tr.services.translate import failures as failures_mod
from story_tr.services.translate import retry
from story_tr.services.translate import tts as translate_tts
from tests.support import FakeTranslator
from tests.support import setup_workspace as _setup_workspace


@pytest.fixture(autouse=True)
def _fast_backoff(monkeypatch):
    monkeypatch.setattr("story_tr.services.translate.retry.BACKOFF_BASE", 0.0)


class FlakyTranslator(FakeTranslator):
    """前 `failures` 次调用失败，之后成功（用于验证首轮失败→重试恢复）。"""

    def __init__(self, failures=1, result="译文"):
        super().__init__(name="google", result=result)
        self.remaining = failures

    def translate(self, text, src, tgt):
        self.calls += 1
        if self.remaining > 0:
            self.remaining -= 1
            raise BadOutput("bad")
        return self.result


def _run(tmp_path, monkeypatch, translator, **kwargs):
    _setup_workspace(tmp_path)
    monkeypatch.setattr("story_tr.transport.build_translator", lambda p, s: translator)
    return translate.run(
        ["10000021"],
        stories="vision",
        mode="fixed",
        chain="asia",
        no_tts=True,
        data_dir=str(tmp_path),
        **kwargs,
    )


def _registry(tmp_path):
    return failures_mod.read_registry(str(tmp_path))


def test_retry_recovers_after_first_pass_failures(tmp_path, monkeypatch):
    translator = FlakyTranslator(failures=4)
    rc = _run(tmp_path, monkeypatch, translator, retry_rounds=1)
    assert rc == ExitCode.OK
    assert _registry(tmp_path) is None
    rdir = tmp_path / "data" / "story" / "character" / "10000021" / "result" / "vision"
    assert (rdir / "seg_01.json").exists()


def test_retry_exhausts_rounds_and_keeps_registry(tmp_path, monkeypatch):
    translator = FakeTranslator(name="google", error=BadOutput("always bad"))
    rc = _run(tmp_path, monkeypatch, translator, retry_rounds=2)
    assert rc == ExitCode.PARTIAL
    registry = _registry(tmp_path)
    assert registry is not None
    assert len(registry.failures) == 4
    assert {record.stage for record in registry.failures} == {"translate"}
    # 首轮 + 2 个重试轮，每轮都写失败清单
    assert len(list((tmp_path / "logs").glob("failures_*.json"))) == 3
    assert all(record.attempts >= 2 for record in registry.failures)


def test_retry_disabled_returns_partial(tmp_path, monkeypatch):
    translator = FakeTranslator(name="google", error=BadOutput("always bad"))
    rc = _run(tmp_path, monkeypatch, translator, retry_rounds=0)
    assert rc == ExitCode.PARTIAL
    assert _registry(tmp_path) is not None


def test_fail_fast_still_enters_retry(tmp_path, monkeypatch):
    translator = FlakyTranslator(failures=1)
    rc = _run(tmp_path, monkeypatch, translator, retry_rounds=1, fail_fast=True)
    assert rc == ExitCode.OK
    assert _registry(tmp_path) is None


def test_tts_failure_retried_with_audio_only(tmp_path, monkeypatch):
    _setup_workspace(tmp_path)
    translator = FakeTranslator(name="google", result="译文")
    monkeypatch.setattr("story_tr.transport.build_translator", lambda p, s: translator)
    calls = []

    def flaky_tts(*args, **kwargs):
        calls.append(1)
        if len(calls) <= 4:  # 首轮 4 份全部失败
            raise ConnectionError("tts down")
        return b"mp3"

    monkeypatch.setattr(translate_tts, "tts_bytes", flaky_tts)
    monkeypatch.setattr(translate_tts, "TTS_BACKOFF", 0.0)
    rc = translate.run(
        ["10000021"],
        stories="vision",
        mode="fixed",
        chain="asia",
        retry_rounds=1,
        data_dir=str(tmp_path),
    )
    assert rc == ExitCode.OK
    assert _registry(tmp_path) is None
    rdir = tmp_path / "data" / "story" / "character" / "10000021" / "result" / "vision"
    assert sorted(p.name for p in rdir.glob("*.mp3")) == [
        "full.mp3",
        "seg_01.mp3",
        "seg_02.mp3",
        "title.mp3",
    ]
    summary = json.loads((rdir / "segments.json").read_text(encoding="utf-8"))
    assert summary["title"]["mp3"] == "title.mp3"


def test_registry_archived_on_next_run(tmp_path, monkeypatch):
    _setup_workspace(tmp_path)
    old = models.FailureRegistry(
        signature={"mode": "fixed"},
        started_at="2026-01-01T00:00:00",
        failures=[
            models.FailureRecord(
                char_id="10000021",
                story="vision",
                key="seg_01",
                stage="translate",
                error="BadOutput: x",
                first_at="t",
                last_at="t",
            )
        ],
    )
    failures_mod.save_registry(str(tmp_path), old)
    translator = FakeTranslator(name="google", result="译文")
    monkeypatch.setattr("story_tr.transport.build_translator", lambda p, s: translator)
    rc = translate.run(
        ["10000021"],
        stories="vision",
        mode="fixed",
        chain="asia",
        no_tts=True,
        data_dir=str(tmp_path),
    )
    assert rc == ExitCode.OK
    assert _registry(tmp_path) is None
    assert (tmp_path / (failures_mod.FAILURE_FILENAME + ".bak")).exists()


def test_retry_rounds_negative_is_usage(tmp_path):
    assert (
        translate.run(["10000021"], stories="vision", retry_rounds=-1, data_dir=str(tmp_path))
        == ExitCode.USAGE
    )


def test_backoff_seconds_progression_and_cap(monkeypatch):
    monkeypatch.setattr(retry, "BACKOFF_BASE", 5.0)
    assert retry.backoff_seconds(1) == 5.0
    assert retry.backoff_seconds(2) == 10.0
    assert retry.backoff_seconds(4) == 40.0
    assert retry.backoff_seconds(10) == 60.0


def test_retry_run_rounds_cancel():
    import threading

    failures = {"a": {"char_id": "1", "story": "s", "key": "k", "stage": "translate"}}
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(Exception) as info:
        retry.run_rounds(failures, lambda record: None, 3, cancel)
    assert info.value.code == ExitCode.CANCELLED


def test_retry_sleep_and_early_exit(monkeypatch, tmp_path):
    monkeypatch.setattr(retry, "BACKOFF_BASE", 0.01)
    retry.sleep_backoff(1, None)  # 正常等待路径

    failures = {"a": {"char_id": "1", "story": "s", "key": "k", "stage": "translate"}}

    def recover(record):
        failures.clear()

    retry.run_rounds(failures, recover, 3, None)
    assert failures == {}
    retry.run_rounds({}, lambda record: None, 3, None)  # 空失败直接返回


def test_tts_only_retries_failures(tmp_path, monkeypatch):
    _setup_workspace(tmp_path)
    fake = FakeTranslator(name="google", result="译文")
    monkeypatch.setattr("story_tr.transport.build_translator", lambda p, s: fake)
    args = dict(stories="vision", mode="fixed", chain="asia", data_dir=str(tmp_path))
    assert translate.run(["10000021"], no_tts=True, **args) == ExitCode.OK

    calls = []

    def flaky(*a, **k):
        calls.append(1)
        if len(calls) <= 12:  # 4 份 × tts_with_retry 3 次内部重试
            raise ConnectionFailed("tts down")
        return b"mp3"

    monkeypatch.setattr(translate_tts, "tts_bytes", flaky)
    monkeypatch.setattr(translate_tts, "TTS_BACKOFF", 0.0)
    rc = translate.run(["10000021"], tts_only=True, retry_rounds=1, **args)
    assert rc == ExitCode.OK
    assert _registry(tmp_path) is None
    rdir = tmp_path / "data" / "story" / "character" / "10000021" / "result" / "vision"
    assert len(list(rdir.glob("*.mp3"))) == 4


def test_runner_read_result_row_and_retry_branches(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from story_tr.services.translate import runner, tts_only

    _setup_workspace(tmp_path)
    assert runner.read_result_row(str(tmp_path / "none.json")) is None
    bad = tmp_path / "bad.json"
    bad.write_text("{bad", encoding="utf-8")
    assert runner.read_result_row(str(bad)) is None
    arr = tmp_path / "arr.json"
    arr.write_text("[]", encoding="utf-8")
    assert runner.read_result_row(str(arr)) is None

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
    # 失败记录指向不存在的任务 → 记录"任务已不存在"
    batch.retry_one({"char_id": "10000021", "story": "vision", "key": "seg_99", "stage": "translate"})
    assert "10000021|vision|seg_99" in batch.failures
    # TTS 重试但 JSON 缺失 → 回退重译，任务不存在时记录失败
    batch.failures.clear()
    batch.retry_one({"char_id": "10000021", "story": "vision", "key": "seg_98", "stage": "tts"})
    assert "10000021|vision|seg_98" in batch.failures

    # tts_only：passthrough / 空文本不生成语音
    rdir = tmp_path / "data" / "story" / "character" / "10000021" / "result" / "vision"
    rdir.mkdir(parents=True)
    (rdir / "title.json").write_text(json.dumps({"final": "……", "passthrough": True}), encoding="utf-8")
    (rdir / "full.json").write_text(json.dumps({"final": ""}), encoding="utf-8")
    tts_plan = SimpleNamespace(
        home=str(tmp_path),
        result_tag=None,
        settings=plan.settings,
    )
    failures: dict = {}
    assert tts_only._write_audio(tts_plan, "10000021", "vision", "title", failures) is False
    assert tts_only._write_audio(tts_plan, "10000021", "vision", "full", failures) is False
    assert "10000021|vision|full" in failures
    assert tts_only._write_audio(tts_plan, "10000021", "vision", "full", failures) is False
    assert failures["10000021|vision|full"]["attempts"] == 2
