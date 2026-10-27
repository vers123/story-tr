"""补充 CLI 单测：各命令参数映射、互斥校验、进度回调与退出码。"""
from __future__ import annotations

import sys

import pytest
from rich.progress import Progress
from typer.testing import CliRunner

from story_tr.cli import app
from story_tr.cli.app import (
    _fetch_progress_callback,
    _provider_arg,
    _translate_progress_callback,
)
from story_tr.errors import ExitCode, StoryTrError
from story_tr.services import chain as chain_svc
from story_tr.services import clean as clean_svc
from story_tr.services import doctor as doctor_svc
from story_tr.services import eval as eval_svc
from story_tr.services import fetch as fetch_svc
from story_tr.services import folders as folders_svc
from story_tr.services import status as status_svc
from story_tr.services import translate as translate_svc

runner = CliRunner()


def test_provider_arg_mapping():
    assert _provider_arg("auto") is None
    assert _provider_arg("google") == "google"


def test_verbose_quiet_conflict():
    result = runner.invoke(app, ["--verbose", "--quiet", "translate", "1"])
    assert result.exit_code == 2


def test_fetch_update_refresh_conflict():
    result = runner.invoke(app, ["fetch", "--update", "--refresh"])
    assert result.exit_code == 2


def test_fetch_mode_mapping(monkeypatch):
    calls = []

    def fake_run(**kwargs):
        calls.append(kwargs)
        return ExitCode.OK

    monkeypatch.setattr(fetch_svc, "run", fake_run)
    assert runner.invoke(app, ["fetch"]).exit_code == 0
    assert runner.invoke(app, ["fetch", "--update"]).exit_code == 0
    assert runner.invoke(app, ["fetch", "--refresh"]).exit_code == 0
    assert [c["mode"] for c in calls] == ["incremental", "update", "refresh"]


def test_folders_command(monkeypatch):
    calls = {}

    def fake_run(**kwargs):
        calls.update(kwargs)
        return ExitCode.OK

    monkeypatch.setattr(folders_svc, "run", fake_run)
    assert runner.invoke(app, ["folders"]).exit_code == 0
    assert calls["data_dir"] is None


def test_chain_command(monkeypatch):
    calls = {}

    def fake_run(**kwargs):
        calls.update(kwargs)
        return ExitCode.OK

    monkeypatch.setattr(chain_svc, "run", fake_run)
    result = runner.invoke(app, ["chain", "你好", "--mode", "rwkv20", "--provider", "rwkv"])
    assert result.exit_code == 0
    assert calls["text"] == "你好"
    assert calls["mode"] == "rwkv20"
    assert calls["provider"] == "rwkv"


def test_clean_command(monkeypatch):
    calls = {}

    def fake_run(**kwargs):
        calls.update(kwargs)
        return ExitCode.OK

    monkeypatch.setattr(clean_svc, "run", fake_run)
    result = runner.invoke(app, ["clean", "1", "--all-characters", "--dry-run"])
    assert result.exit_code == 0
    assert calls["all_characters"] is True
    assert calls["dry_run"] is True


def test_status_command(monkeypatch):
    calls = {}

    def fake_run(**kwargs):
        calls.update(kwargs)
        return ExitCode.OK

    monkeypatch.setattr(status_svc, "run", fake_run)
    result = runner.invoke(app, ["status", "--json", "--deep"])
    assert result.exit_code == 0
    assert calls["json_out"] is True
    assert calls["deep"] is True


def test_doctor_command(monkeypatch):
    calls = {}

    def fake_run(**kwargs):
        calls.update(kwargs)
        return ExitCode.OK

    monkeypatch.setattr(doctor_svc, "run", fake_run)
    result = runner.invoke(app, ["doctor", "--probe"])
    assert result.exit_code == 0
    assert calls["probe"] is True


def test_eval_command(monkeypatch):
    calls = {}

    def fake_run(**kwargs):
        calls.update(kwargs)
        return ExitCode.OK

    monkeypatch.setattr(eval_svc, "run", fake_run)
    result = runner.invoke(app, ["eval", "10000021", "--limit", "5"])
    assert result.exit_code == 0
    assert calls["char_ids"] == ["10000021"]
    assert calls["limit"] == 5


def test_command_exit_code_propagated(monkeypatch):
    monkeypatch.setattr(status_svc, "run", lambda **kwargs: ExitCode.CONNECTION)
    result = runner.invoke(app, ["status"])
    assert result.exit_code == int(ExitCode.CONNECTION)


def test_translate_command_full_options(monkeypatch):
    calls = {}

    def fake_run(**kwargs):
        calls.update(kwargs)
        return ExitCode.OK

    monkeypatch.setattr(translate_svc, "run", fake_run)
    result = runner.invoke(app, [
        "translate", "1-3", "--mode", "fixed", "--provider", "bing", "--workers", "2",
        "--stories", "vision", "--no-tts", "--reset",
    ])
    assert result.exit_code == 0
    assert calls["char_ids"] == ["1-3"]
    assert calls["mode"] == "fixed"
    assert calls["provider"] == "bing"
    assert calls["workers"] == 2
    assert calls["no_tts"] is True
    assert calls["reset"] is True


def test_fetch_progress_callback():
    with Progress() as progress:
        cb = _fetch_progress_callback(progress)
        cb({"event": "nope"})  # 非 progress 事件直接忽略
        cb({"event": "progress", "done": 1, "total": 3, "char_id": "10000021"})


def test_translate_progress_callback():
    with Progress() as progress:
        cb = _translate_progress_callback(progress)
        cb({"event": "char_start", "chars": 2, "tasks": 3, "char_index": 1, "char_id": "a"})
        cb({"event": "progress", "chars": 2, "tasks": 3, "done": 1})
        cb({"event": "char_done", "chars": 2, "tasks": 3, "char_index": 1})


def _invoke_entrypoint(monkeypatch, exc, capsys):
    def boom(**kwargs):
        raise exc

    monkeypatch.setattr(status_svc, "run", boom)
    monkeypatch.setattr(sys, "argv", ["story-tr", "status"])
    original = sys.excepthook
    try:
        with pytest.raises(SystemExit) as info:
            app()
    finally:
        sys.excepthook = original
    return info.value.code, capsys.readouterr().err


def test_unexpected_error_is_friendly(monkeypatch, capsys):
    code, err = _invoke_entrypoint(monkeypatch, RuntimeError("boom"), capsys)
    assert code == 1
    assert "意外错误" in err
    assert "RuntimeError" in err


def test_story_tr_error_maps_to_usage(monkeypatch, capsys):
    code, err = _invoke_entrypoint(monkeypatch, StoryTrError("数据损坏"), capsys)
    assert code == int(ExitCode.USAGE)
    assert "错误：数据损坏" in err
