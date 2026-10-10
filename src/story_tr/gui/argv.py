# -*- coding: utf-8 -*-
"""把 GUI 表单值翻译成 CLI 参数（纯 Python，不依赖 PySide6，便于测试）。

用**真实 CLI 解析器**构造 `argparse.Namespace`，保证 GUI 与命令行的参数语义完全一致 ——
不会因为手写默认值而与 `argparse` 漂移。
"""
from __future__ import annotations

import argparse

from .. import chain as tc
from .. import cli, paths

DEFAULT_MIN_INTERVAL = tc.GOOGLE_MIN_INTERVAL


def _verbosity(argv: list, verbose: bool, quiet: bool) -> list:
    if quiet:
        argv.append("-q")
    elif verbose:
        argv.append("--verbose")
    return argv


def translate_argv(char_ids="", stories="", all_characters=False, mode="fixed", chain="asia",
                   languages="", steps=20, provider="", workers=1, full_only=False,
                   segments_only=False, overwrite=False, no_tts=False, no_batch=False,
                   min_interval=DEFAULT_MIN_INTERVAL, balance=False, reset=False,
                   verbose=False, quiet=False) -> list:
    """组装 `story-tr translate` 的 argv。"""
    argv = ["translate"]
    if char_ids.strip():
        argv.append(char_ids.strip())
    if all_characters:
        argv.append("--all-characters")
    if stories.strip():
        argv += ["--stories", stories.strip()]
    argv += ["--mode", mode]
    if mode == "fixed" and chain:
        argv += ["--chain", chain]
    if mode == "custom" and languages.strip():
        argv += ["--languages", languages.strip()]
    if mode == "random":
        argv += ["--steps", str(steps)]
    if provider:
        argv += ["--provider", provider]
    argv += ["--workers", str(workers), "--min-interval", str(min_interval)]
    if full_only:
        argv.append("--full-only")
    if segments_only:
        argv.append("--segments-only")
    if overwrite:
        argv.append("--overwrite")
    if reset:
        argv.append("--reset")
    if no_batch:
        argv.append("--no-batch")
    if balance:
        argv.append("--balance")
    if no_tts:
        argv.append("--no-tts")
    return _verbosity(argv, verbose, quiet)


def fetch_argv(mode="incremental", verbose=False, quiet=False) -> list:
    """组装 `story-tr fetch` 的 argv（mode：incremental / update / refresh）。"""
    argv = ["fetch"]
    if mode == "update":
        argv.append("--update")
    elif mode == "refresh":
        argv.append("--refresh")
    return _verbosity(argv, verbose, quiet)


def clean_argv(char_ids="", stories="", all_characters=False, dry_run=True,
               keep_state=False, verbose=False, quiet=False) -> list:
    """组装 `story-tr clean` 的 argv（非预览时加 `--yes`，确认交给 GUI 对话框）。"""
    argv = ["clean"]
    if char_ids.strip():
        argv.append(char_ids.strip())
    if all_characters:
        argv.append("--all-characters")
    if stories.strip():
        argv += ["--stories", stories.strip()]
    argv.append("--dry-run" if dry_run else "--yes")
    if keep_state:
        argv.append("--keep-state")
    return _verbosity(argv, verbose, quiet)


def parse(argv) -> argparse.Namespace:
    """用真实 CLI 解析器解析 argv，并补上已解析的工作目录（与 `cli.main` 一致）。"""
    ns = cli.build_parser().parse_args(list(argv))
    ns.data_dir = paths.home()
    return ns
