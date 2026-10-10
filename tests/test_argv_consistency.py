# -*- coding: utf-8 -*-
"""参数一致性护栏：所有「argv 构造器」产出的命令行，必须能被真实 CLI 解析器接受。

覆盖 GUI（`gui.argv`）与 MCP（`mcp.tools`）两条不经过 `argparse` 生成命令行的路径 ——
它们最容易与 CLI 参数漂移。例如 `clean` 曾不接受 `-q/--verbose`，而 GUI 的「显示明细」
会把它传下去，导致任务直接以「参数不合法」失败。
"""
import pytest

from story_tr import cli
from story_tr.gui import argv as gargv
from story_tr.mcp import tools as mtools

GUI_CASES = [
    gargv.translate_argv(char_ids="10000021"),
    gargv.translate_argv(char_ids="10000021", verbose=True),
    gargv.translate_argv(char_ids="10000021", quiet=True),
    gargv.translate_argv(all_characters=True, full_only=True, no_tts=True, workers=4,
                         provider="bing", min_interval=1.1),
    gargv.translate_argv(char_ids="1", mode="random", steps=5),
    gargv.translate_argv(char_ids="1", mode="custom", languages="en,ja"),
    gargv.translate_argv(char_ids="1", stories="character_details", overwrite=True,
                         segments_only=True, no_batch=True, balance=True),
    gargv.translate_argv(char_ids="1", reset=True),
    gargv.translate_argv(char_ids="1", mode="random", steps=7, reset=True, overwrite=True),
    gargv.fetch_argv("incremental"),
    gargv.fetch_argv("update", verbose=True),
    gargv.fetch_argv("refresh", quiet=True),
    gargv.clean_argv(char_ids="10000021", dry_run=True),
    gargv.clean_argv(char_ids="10000021", dry_run=True, verbose=True),
    gargv.clean_argv(char_ids="10000021", dry_run=True, quiet=True),
    gargv.clean_argv(char_ids="10000021", dry_run=False, keep_state=True),
    gargv.clean_argv(all_characters=True, dry_run=True),
]

MCP_CASES = [
    mtools.translate_argv("10000021"),
    mtools.translate_argv("10000021", mode="custom", languages="en,ja"),
    mtools.translate_argv("10000021", provider="baidu", full_only=True),
    mtools.fetch_argv(True),
    mtools.fetch_argv(False),
]


@pytest.mark.parametrize("argv", GUI_CASES, ids=lambda a: " ".join(a[:2]))
def test_gui_argv_is_parseable(argv):
    cli.build_parser().parse_args(argv)  # 不接受即抛 SystemExit


@pytest.mark.parametrize("argv", MCP_CASES, ids=lambda a: " ".join(a[5:7]))
def test_mcp_argv_is_parseable(argv):
    """MCP 命令行为 [python, -m, story_tr, --data-dir, HOME, <子命令>…]，需剥掉前缀。"""
    assert argv[1:4] == ["-m", "story_tr", "--data-dir"]
    cli.build_parser().parse_args(argv[5:])


def test_clean_accepts_verbosity_flags():
    """回归：clean 曾无 -q/--verbose，而 GUI 会传 → 任务直接失败。"""
    ns = gargv.parse(gargv.clean_argv(char_ids="10000021", dry_run=True, verbose=True))
    assert ns.verbose is True and ns.dry_run is True
    ns = gargv.parse(gargv.clean_argv(char_ids="10000021", dry_run=True, quiet=True))
    assert ns.quiet is True and ns.verbose is False


def test_mcp_custom_mode_carries_languages():
    """回归：custom 模式缺 --languages 会被 CLI 拒绝（SystemExit）。"""
    argv = mtools.translate_argv("10000021", mode="custom", languages="en,ja")
    assert argv[argv.index("--languages") + 1] == "en,ja"
    ns = cli.build_parser().parse_args(argv[5:])
    assert ns.mode == "custom" and ns.languages == "en,ja"


def test_mcp_fixed_mode_has_no_languages():
    argv = mtools.translate_argv("10000021", mode="fixed")
    assert "--languages" not in argv
