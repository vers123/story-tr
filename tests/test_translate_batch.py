"""services.translate.batch_runner：批量流水线与逐条路径的等价性。"""

import json

import pytest

from story_tr import transport
from story_tr.errors import BadOutput, ExitCode
from story_tr.services import translate
from tests.support import FakeBatchTranslator
from tests.support import setup_workspace as _setup_workspace

RESULT_KEYS = ("title", "full", "seg_01", "seg_02")
STEPS = 22  # 中文源：to_en + 20 步 + to_zh


def _run(home, monkeypatch, fake, **kwargs):
    _setup_workspace(home)
    monkeypatch.setattr(transport, "build_translator", lambda provider, settings, _f=fake: _f)
    rc = translate.run(
        ["10000021"],
        stories="vision",
        mode="fixed",
        chain="asia",
        no_tts=True,
        data_dir=str(home),
        **kwargs,
    )
    assert rc == ExitCode.OK
    return home / "data" / "story" / "character" / "10000021" / "result" / "vision"


def _rows(rdir):
    return {key: json.loads((rdir / f"{key}.json").read_text(encoding="utf-8")) for key in RESULT_KEYS}


def test_batch_equals_per_task_output(tmp_path, monkeypatch):
    fake_batch = FakeBatchTranslator(name="google")
    batch_dir = _run(tmp_path / "batch", monkeypatch, fake_batch, batch=True)
    fake_seq = FakeBatchTranslator(name="google")
    seq_dir = _run(tmp_path / "seq", monkeypatch, fake_seq, batch=False)

    batch_rows, seq_rows = _rows(batch_dir), _rows(seq_dir)
    for key in RESULT_KEYS:
        assert batch_rows[key]["final"] == seq_rows[key]["final"]
        assert [s["text"] for s in batch_rows[key]["steps"]] == [s["text"] for s in seq_rows[key]["steps"]]
        assert [s["kind"] for s in batch_rows[key]["steps"]] == [s["kind"] for s in seq_rows[key]["steps"]]


def test_batch_uses_one_request_per_step(tmp_path, monkeypatch):
    fake = FakeBatchTranslator(name="google")
    _run(tmp_path, monkeypatch, fake, batch=True)
    assert fake.batch_calls == STEPS
    assert fake.calls == 0


def test_no_batch_uses_per_task_requests(tmp_path, monkeypatch):
    fake = FakeBatchTranslator(name="google")
    _run(tmp_path, monkeypatch, fake, batch=False)
    assert fake.batch_calls == 0
    assert fake.calls == len(RESULT_KEYS) * STEPS


def test_batch_failure_falls_back_to_per_item(tmp_path, monkeypatch):
    fake = FakeBatchTranslator(name="google", batch_error=BadOutput("bad batch"))
    rdir = _run(tmp_path, monkeypatch, fake, batch=True)
    rows = _rows(rdir)
    assert rows["seg_01"]["final"].startswith("<zh-CN>")
    assert rows["seg_01"]["final"].endswith("第一段")
    assert fake.batch_calls == STEPS
    assert fake.calls == len(RESULT_KEYS) * STEPS


def test_batch_with_workers_runs_serially(tmp_path, monkeypatch):
    fake = FakeBatchTranslator(name="google")
    rdir = _run(tmp_path, monkeypatch, fake, batch=True, workers=2)
    assert (rdir / "seg_01.json").exists()
    assert fake.batch_calls == STEPS
    assert fake.calls == 0


def test_packages_split_by_budget():
    from story_tr.services.translate import batch_runner

    items = [("k", i, "测" * 400) for i in range(4)]
    packages = batch_runner._packages(items, max_chars=1000, max_items=2)
    assert len(packages) == 2
    assert all(len(package) <= 2 for package in packages)


def test_batch_translate_empty_output_raises(monkeypatch):
    from story_tr.services.translate import batch_runner

    class Empty:
        max_batch_chars = 1500
        max_batch_items = 32

        def translate_batch(self, texts, src, tgt):
            return ["" for _ in texts]

    with pytest.raises(BadOutput):
        batch_runner.batch_translate(Empty(), {"a": "x"}, "zh-CN", "ja")


def test_batch_handles_passthrough_paragraph(tmp_path, monkeypatch):

    _setup_workspace(tmp_path)
    story_dir = tmp_path / "data" / "story" / "character" / "10000021" / "profile" / "story" / "vision"
    (story_dir / "zh.md").write_text(
        '---\nid: "10000021"\nstory: vision\nlang: zh\ntitle: "角色详细"\n---\n\n第一段\n\n……\n',
        encoding="utf-8",
    )
    fake = FakeBatchTranslator(name="google")
    monkeypatch.setattr("story_tr.transport.build_translator", lambda p, s: fake)
    rc = translate.run(
        ["10000021"],
        stories="vision",
        mode="fixed",
        chain="asia",
        no_tts=True,
        data_dir=str(tmp_path),
        batch=True,
    )
    assert rc == ExitCode.OK
    rdir = tmp_path / "data" / "story" / "character" / "10000021" / "result" / "vision"
    row = json.loads((rdir / "seg_02.json").read_text(encoding="utf-8"))
    assert row["passthrough"] is True
    assert row["final"] == "……"
    # 批量请求只覆盖有文字的任务（title/full/seg_01），passthrough 不计
    assert fake.batch_calls == STEPS


def test_batch_per_item_failure_returns_partial(tmp_path, monkeypatch):
    fake = FakeBatchTranslator(name="google", error=BadOutput("bad"), batch_error=BadOutput("bad batch"))
    _setup_workspace(tmp_path)
    monkeypatch.setattr("story_tr.transport.build_translator", lambda p, s: fake)
    rc = translate.run(
        ["10000021"],
        stories="vision",
        mode="fixed",
        chain="asia",
        no_tts=True,
        data_dir=str(tmp_path),
        batch=True,
        retry_rounds=0,
    )
    assert rc == ExitCode.PARTIAL
