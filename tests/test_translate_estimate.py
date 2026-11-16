"""services.translate.estimate：`translate --plan` 的只读估算。"""

import json

from story_tr import models
from story_tr.errors import ExitCode
from story_tr.services import translate
from story_tr.services.translate import estimate, runner
from tests.support import FakeTranslator
from tests.support import setup_workspace as _setup_workspace


def _plan(home, **overrides):
    kwargs = dict(
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
        data_dir=str(home),
        on_progress=None,
    )
    kwargs.update(overrides)
    plan, code = runner.build_plan(**kwargs)
    assert code is None and plan is not None
    return plan


def _result_dir(home):
    return home / "data" / "story" / "character" / "10000021" / "result" / "vision"


def _write_result(rdir, key, mode="fixed", chain="asia"):
    rdir.mkdir(parents=True, exist_ok=True)
    (rdir / f"{key}.json").write_text(
        json.dumps({"mode": mode, "chain": chain, "final": "x"}), encoding="utf-8"
    )


def test_estimate_counts_and_batch_requests(tmp_path):
    _setup_workspace(tmp_path)
    info = estimate.estimate(_plan(tmp_path))
    assert info["characters"] == 1
    assert info["stories"] == 1
    assert info["tasks"]["title"] == 1
    assert info["tasks"]["full"] == 1
    assert info["tasks"]["segments"] == 2
    assert info["tasks"]["passthrough"] == 0
    assert info["tasks"]["existing"] == 0
    assert info["tasks"]["todo"] == 4
    assert info["batch"] is True
    assert info["requests"] == 22  # 22 步 × 1 个请求包


def test_estimate_per_task_requests_when_batch_off(tmp_path):
    _setup_workspace(tmp_path)
    info = estimate.estimate(_plan(tmp_path, batch=False))
    assert info["batch"] is False
    assert info["requests"] == 4 * 22


def test_estimate_counts_existing_and_state(tmp_path):
    _setup_workspace(tmp_path)
    rdir = _result_dir(tmp_path)
    for key in ("title", "full", "seg_01"):
        _write_result(rdir, key)
    info = estimate.estimate(_plan(tmp_path))
    assert info["tasks"]["existing"] == 3
    assert info["tasks"]["todo"] == 1
    assert info["disk_tasks"] == 3
    assert info["signature_match"] is True


def test_estimate_signature_mismatch(tmp_path):
    _setup_workspace(tmp_path)
    state = models.BatchState(
        signature={"mode": "fixed", "chain": "europe"}, completed=[], started_at="2026-01-01T00:00:00"
    )
    (tmp_path / translate.STATE_FILENAME).write_text(
        json.dumps(state.model_dump(mode="json")), encoding="utf-8"
    )
    info = estimate.estimate(_plan(tmp_path))
    assert info["signature_match"] is False
    assert "退出码 3" in estimate.render(info)


def test_estimate_param_mismatch(tmp_path):
    _setup_workspace(tmp_path)
    _write_result(_result_dir(tmp_path), "seg_01", mode="fixed", chain="europe")
    info = estimate.estimate(_plan(tmp_path))
    assert info["param_mismatch"] == 1
    assert "参数不一致" in estimate.render(info)


def test_estimate_tts_jobs(tmp_path):
    _setup_workspace(tmp_path)
    _write_result(_result_dir(tmp_path), "seg_01")
    info = estimate.estimate(_plan(tmp_path), tts_only=True)
    assert info["tts_jobs"] == 1
    assert "待补语音" in estimate.render(info)


def test_estimate_is_read_only(tmp_path):
    _setup_workspace(tmp_path)
    before = sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*"))
    estimate.render(estimate.estimate(_plan(tmp_path)))
    after = sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*"))
    assert before == after


def test_estimate_reports_pending_failures(tmp_path):
    from story_tr.services.translate import failures as failures_mod

    _setup_workspace(tmp_path)
    failures_mod.save_registry(
        str(tmp_path),
        models.FailureRegistry(
            signature={"mode": "fixed"},
            started_at="t",
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
        ),
    )
    info = estimate.estimate(_plan(tmp_path))
    assert info["pending_failures"] == 1
    assert "遗留失败登记：1 条" in estimate.render(info)


def test_plan_only_service_prints_and_returns_ok(tmp_path, monkeypatch, capsys):
    _setup_workspace(tmp_path)
    fake = FakeTranslator(name="google", result="译文")
    monkeypatch.setattr("story_tr.transport.build_translator", lambda p, s: fake)
    rc = translate.run(
        ["10000021"],
        stories="vision",
        mode="fixed",
        chain="asia",
        no_tts=True,
        plan_only=True,
        data_dir=str(tmp_path),
    )
    out = capsys.readouterr().out
    assert rc == ExitCode.OK
    assert "预计请求" in out
    assert "只读估算" in out
    assert fake.calls == 0
