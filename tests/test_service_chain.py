"""services.chain：单文本链路的参数、断言与 TTS 行为。"""

import json
import os

import pytest

from story_tr import models
from story_tr.errors import (
    BadOutput,
    ConnectionFailed,
    ExitCode,
    ModelNotLoaded,
    QuotaExceeded,
    RateLimited,
    UnsupportedLanguage,
)
from story_tr.services import chain as chain_mod
from story_tr.services import storage
from tests.support import FakeTranslator
from tests.support import mock_translator as _mock_translator


def test_chain_writes_out_json(tmp_path, monkeypatch):
    fake = FakeTranslator(name="google", result="译文")
    _mock_translator(monkeypatch, fake)
    rc = chain_mod.run(
        text="你好世界", mode="fixed", chain="asia", no_tts=True, no_file_log=True, data_dir=str(tmp_path)
    )
    assert rc == ExitCode.OK
    out_files = list((tmp_path / "out").glob("*.json"))
    assert len(out_files) == 1
    data = json.loads(out_files[0].read_text(encoding="utf-8"))
    assert data["final"] == "译文"
    assert data["source_lang"] == "zh-CN"


def test_chain_rwkv20_requires_english(tmp_path):
    rc = chain_mod.run(text="你好世界", mode="rwkv20", no_tts=True, no_file_log=True, data_dir=str(tmp_path))
    assert rc == ExitCode.USAGE


def test_chain_rwkv20_uses_english_text(tmp_path, monkeypatch):
    fake = FakeTranslator(name="rwkv", result="这是译文")
    _mock_translator(monkeypatch, fake)
    rc = chain_mod.run(
        text="Hello, world", mode="rwkv20", no_tts=True, no_file_log=True, data_dir=str(tmp_path)
    )
    assert rc == ExitCode.OK
    data = json.loads(next((tmp_path / "out").glob("*.json")).read_text(encoding="utf-8"))
    assert data["providers"] == ["rwkv"]
    assert [s["kind"] for s in data["steps"]] == ["rwkv20"]


def test_chain_dry_run(tmp_path):
    assert chain_mod.run(text="Hello", mode="rwkv20", dry_run=True, data_dir=str(tmp_path)) == ExitCode.OK


def test_chain_signature_mismatch(tmp_path):
    state = models.ChainState(
        signature={"mode": "random"},
        source="你好",
        detected="zh-CN",
        languages=["ja"],
        text="你好",
        next_index=0,
        steps=[],
        started_at="2026-01-01T00:00:00",
    )
    storage.atomic_write_json(os.path.join(str(tmp_path), chain_mod.STATE_FILENAME), state)
    assert (
        chain_mod.run(text="你好", mode="fixed", chain="asia", no_tts=True, data_dir=str(tmp_path))
        == ExitCode.SIGNATURE
    )


@pytest.mark.parametrize(
    "error,code",
    [
        (QuotaExceeded("q"), ExitCode.QUOTA),
        (UnsupportedLanguage("u"), ExitCode.UNSUPPORTED_LANGUAGE),
        (ModelNotLoaded("m"), ExitCode.PAUSED),
        (ConnectionFailed("c"), ExitCode.CONNECTION),
        (RateLimited("r"), ExitCode.PAUSED),
        (BadOutput("b"), ExitCode.PAUSED),
    ],
)
def test_chain_run_error_branches(tmp_path, monkeypatch, error, code):
    _mock_translator(monkeypatch, FakeTranslator(name="rwkv", error=error))
    assert chain_mod.run(text="Hello", mode="rwkv20", no_tts=True, data_dir=str(tmp_path)) == code


def test_chain_tts_success(tmp_path, monkeypatch):
    _mock_translator(monkeypatch, FakeTranslator(name="google", result="译文"))
    monkeypatch.setattr(chain_mod, "tts_bytes", lambda *a, **k: b"mp3")
    assert (
        chain_mod.run(text="你好", mode="custom", languages="ja", no_file_log=True, data_dir=str(tmp_path))
        == ExitCode.OK
    )
    assert list((tmp_path / "out").glob("*.mp3"))


def test_chain_tts_failure_still_ok(tmp_path, monkeypatch):
    _mock_translator(monkeypatch, FakeTranslator(name="google", result="译文"))

    def boom(*a, **k):
        raise ConnectionFailed("tts down")

    monkeypatch.setattr(chain_mod, "tts_bytes", boom)
    assert (
        chain_mod.run(text="你好", mode="custom", languages="ja", no_file_log=True, data_dir=str(tmp_path))
        == ExitCode.OK
    )


def test_chain_file_log(tmp_path):
    handler = chain_mod._setup_file_log(str(tmp_path), no_file_log=False)
    assert handler is not None
    handler.close()
    assert chain_mod._setup_file_log(str(tmp_path), no_file_log=True) is None
