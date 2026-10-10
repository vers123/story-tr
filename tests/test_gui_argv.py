# -*- coding: utf-8 -*-
"""GUI 参数层（gui.argv）：把表单值翻译成 CLI argv，并用真实解析器解析。

不依赖 PySide6，任何环境都能跑。
"""
from story_tr.gui import argv as gargv


def test_translate_minimal_keeps_positional_id():
    a = gargv.translate_argv(char_ids="10000021")
    assert a[0] == "translate"
    assert a[1] == "10000021"


def test_translate_all_characters_when_id_empty():
    a = gargv.translate_argv(all_characters=True)
    assert "--all-characters" in a
    assert a[1] == "--all-characters"  # 位置参数为空时不占位


def test_translate_options_and_values():
    a = gargv.translate_argv(char_ids="1", stories="character_details", mode="fixed", chain="eu",
                             provider="bing", workers=4, min_interval=0.5, full_only=True,
                             overwrite=True, no_tts=True, verbose=True)
    assert a[a.index("--stories") + 1] == "character_details"
    assert a[a.index("--chain") + 1] == "eu"
    assert a[a.index("--provider") + 1] == "bing"
    assert a[a.index("--workers") + 1] == "4"
    assert a[a.index("--min-interval") + 1] == "0.5"
    for flag in ("--full-only", "--overwrite", "--no-tts", "--verbose"):
        assert flag in a
    assert "--segments-only" not in a


def test_translate_random_adds_steps_and_drops_chain():
    a = gargv.translate_argv(char_ids="1", mode="random", chain="asia", steps=7)
    assert a[a.index("--mode") + 1] == "random"
    assert a[a.index("--steps") + 1] == "7"
    assert "--chain" not in a


def test_translate_custom_adds_languages():
    a = gargv.translate_argv(char_ids="1", mode="custom", languages="en,ja")
    assert a[a.index("--languages") + 1] == "en,ja"


def test_quiet_beats_verbose():
    a = gargv.translate_argv(char_ids="1", verbose=True, quiet=True)
    assert "-q" in a and "--verbose" not in a


def test_reset_flag_only_when_requested():
    """`--reset` 只在明确要重置批次时出现（GUI 的「重置批次并重跑」）。"""
    assert "--reset" not in gargv.translate_argv(char_ids="1")
    a = gargv.translate_argv(char_ids="1", reset=True)
    assert a[a.index("--reset")] == "--reset"
    assert gargv.parse(a).reset is True


def test_fetch_modes():
    assert "--update" not in gargv.fetch_argv("incremental")
    assert "--refresh" not in gargv.fetch_argv("incremental")
    assert "--update" in gargv.fetch_argv("update")
    assert "--refresh" in gargv.fetch_argv("refresh")


def test_clean_dry_run_and_execute():
    preview = gargv.clean_argv(char_ids="1", dry_run=True)
    assert "--dry-run" in preview and "--yes" not in preview
    run = gargv.clean_argv(char_ids="1", dry_run=False)
    assert "--yes" in run and "--dry-run" not in run


def test_parse_uses_real_cli_parser():
    ns = gargv.parse(gargv.translate_argv(char_ids="10000021-10000030", full_only=True,
                                          quiet=True))
    assert ns.char_ids == ["10000021-10000030"]
    assert ns.full_only is True and ns.quiet is True
    assert ns.data_dir                       # cli.main 会注入已解析的工作目录
    assert callable(ns.func)


def test_parse_clean_carries_keep_state():
    ns = gargv.parse(gargv.clean_argv(char_ids="1", dry_run=False, keep_state=True))
    assert ns.keep_state is True and ns.yes is True and ns.dry_run is False
