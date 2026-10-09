# -*- coding: utf-8 -*-
"""CLI 冒烟测试：子命令注册、`--help` / `--version`、参数解析。"""
import pytest

from story_tr import __version__, cli

SUBCOMMANDS = ("fetch", "folders", "chain", "translate")


def test_version(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["--version"])
    assert e.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_help_lists_subcommands(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["--help"])
    assert e.value.code == 0
    out = capsys.readouterr().out
    for name in SUBCOMMANDS:
        assert name in out


def test_requires_subcommand():
    with pytest.raises(SystemExit) as e:
        cli.main([])
    assert e.value.code == 2


@pytest.mark.parametrize("name", SUBCOMMANDS)
def test_subcommand_help(name):
    with pytest.raises(SystemExit) as e:
        cli.main([name, "--help"])
    assert e.value.code == 0


def test_translate_parses_char_ids_and_options(monkeypatch):
    """translate：解析 id / 范围与批量参数，并注入已解析的工作目录。"""
    from story_tr import stories

    seen = {}

    def fake(args):
        seen["args"] = args
        return 0

    monkeypatch.setattr(stories, "main", fake)
    rc = cli.main(["translate", "10000002-10000030", "--workers", "4", "--min-interval", "0.25"])
    assert rc == 0
    args = seen["args"]
    assert args.char_ids == ["10000002-10000030"]
    assert args.workers == 4
    assert args.min_interval == 0.25
    assert args.mode == "fixed"
    assert args.data_dir


def test_data_dir_before_subcommand(monkeypatch, tmp_path):
    """全局与子命令两种位置的 --data-dir 都能生效。"""
    from story_tr import folders

    seen = {}

    def fake(args):
        seen["data_dir"] = args.data_dir
        return 0

    monkeypatch.setattr(folders, "main", fake)
    assert cli.main(["--data-dir", str(tmp_path), "folders"]) == 0
    assert seen["data_dir"] == str(tmp_path)
    assert cli.main(["folders", "--data-dir", str(tmp_path)]) == 0
    assert seen["data_dir"] == str(tmp_path)
