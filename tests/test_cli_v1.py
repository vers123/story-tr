"""新 CLI（typer + rich）冒烟测试：命令注册、--help/--version、参数解析与 provider 归一。"""
from __future__ import annotations

import pytest
from typer.testing import CliRunner

from story_tr import __version__
from story_tr.cli import app
from story_tr.errors import ExitCode
from story_tr.services import translate as translate_mod

runner = CliRunner()

SUBCOMMANDS = ("fetch", "folders", "translate", "chain", "clean", "status", "doctor", "eval")


def test_help_lists_subcommands():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    out = result.output
    for name in SUBCOMMANDS:
        assert name in out
    assert "video" not in out


def test_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output


@pytest.mark.parametrize("name", SUBCOMMANDS)
def test_subcommand_help(name):
    result = runner.invoke(app, [name, "--help"])
    assert result.exit_code == 0


def test_translate_invokes_service(monkeypatch):
    calls: dict[str, object] = {}

    def fake_run(**kwargs):
        calls.update(kwargs)
        return ExitCode.OK

    monkeypatch.setattr(translate_mod, "run", fake_run)
    result = runner.invoke(app, ["translate", "1", "--mode", "rwkv20", "--provider", "rwkv"])
    assert result.exit_code == 0
    assert calls["char_ids"] == ["1"]
    assert calls["mode"] == "rwkv20"
    assert calls["provider"] == "rwkv"
    assert calls["data_dir"] is None
    assert callable(calls["on_progress"])


def test_translate_provider_auto_maps_to_none(monkeypatch):
    calls: dict[str, object] = {}

    def fake_run(**kwargs):
        calls.update(kwargs)
        return ExitCode.OK

    monkeypatch.setattr(translate_mod, "run", fake_run)
    result = runner.invoke(app, ["translate", "1", "--provider", "auto"])
    assert result.exit_code == 0
    assert calls["provider"] is None
