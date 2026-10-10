# -*- coding: utf-8 -*-
"""输出层（report）：级别归一、TqdmHandler 路由、进度条参数、`-q/--verbose` 开关。"""
import logging

import pytest

from story_tr import cli, report


# --------------------------------------------------------------------------- #
# 级别归一
# --------------------------------------------------------------------------- #
class _Args:
    def __init__(self, **kw):
        self.quiet = kw.get("quiet", False)
        self.verbose = kw.get("verbose", False)


def test_console_level_defaults_to_info():
    assert report.console_level(_Args()) == logging.INFO


def test_console_level_verbose_shows_steps():
    assert report.console_level(_Args(verbose=True)) == report.STEP


def test_console_level_quiet_only_errors():
    assert report.console_level(_Args(quiet=True)) == logging.ERROR


def test_console_level_show_steps_default():
    """chain：默认就显示每次步骤明细。"""
    assert report.console_level(_Args(), show_steps=True) == report.STEP
    assert report.console_level(_Args(verbose=True), show_steps=True) == report.STEP


def test_step_between_debug_and_info():
    assert logging.DEBUG < report.STEP < logging.INFO


# --------------------------------------------------------------------------- #
# 进度条参数
# --------------------------------------------------------------------------- #
def test_bar_kwargs_disabled_when_not_interactive(monkeypatch):
    monkeypatch.setattr(report, "interactive", lambda: False)
    assert report.bar_kwargs()["disable"] is True
    assert report.bar_kwargs(False)["dynamic_ncols"] is True


def test_bar_kwargs_enabled_when_interactive(monkeypatch):
    monkeypatch.setattr(report, "interactive", lambda: True)
    assert report.bar_kwargs()["disable"] is False
    assert report.bar_kwargs(True)["disable"] is True


def test_interactive_false_when_streams_redirected(monkeypatch):
    """任一路被重定向就不算可交互（否则条与日志会错位）。"""
    class _S:
        def __init__(self, tty):
            self._tty = tty

        def isatty(self):
            return self._tty

    monkeypatch.setattr(report.sys, "stdout", _S(True))
    monkeypatch.setattr(report.sys, "stderr", _S(False))
    assert report.interactive() is False
    monkeypatch.setattr(report.sys, "stderr", _S(True))
    assert report.interactive() is True


# --------------------------------------------------------------------------- #
# handler / emit 路由
# --------------------------------------------------------------------------- #
def test_handler_writes_plain_when_not_interactive(monkeypatch, capsys):
    monkeypatch.setattr(report, "interactive", lambda: False)
    handler = report.TqdmHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    handler.emit(logging.LogRecord("x", logging.INFO, __file__, 1, "你好", None, None))
    assert capsys.readouterr().out == "你好\n"


def test_handler_routes_through_tqdm_write(monkeypatch):
    monkeypatch.setattr(report, "interactive", lambda: True)
    seen = []
    monkeypatch.setattr(report.tqdm, "write", lambda s, **kw: seen.append(s))
    handler = report.TqdmHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    handler.emit(logging.LogRecord("x", logging.INFO, __file__, 1, "上滚", None, None))
    assert seen == ["上滚"]


def test_emit_prints_when_not_interactive(monkeypatch, capsys):
    monkeypatch.setattr(report, "interactive", lambda: False)
    report.emit("一行")
    assert capsys.readouterr().out == "一行\n"


def test_setup_console_replaces_handler_and_sets_level():
    log = report.setup_console(logging.INFO)
    assert logging.getLogger("translate_chain") is log
    consoles = [h for h in log.handlers if isinstance(h, report.TqdmHandler)]
    assert len(consoles) == 1
    assert consoles[0].level == logging.INFO
    report.setup_console(logging.ERROR)
    consoles = [h for h in log.handlers if isinstance(h, report.TqdmHandler)]
    assert len(consoles) == 1
    assert consoles[0].level == logging.ERROR


def test_setup_console_keeps_external_handler():
    """外部 handler（如 GUI 日志面板）不被清掉。"""
    log = logging.getLogger("translate_chain")
    ext = logging.NullHandler()
    log.addHandler(ext)
    try:
        report.setup_console(logging.INFO)
        assert ext in log.handlers
    finally:
        log.removeHandler(ext)


# --------------------------------------------------------------------------- #
# CLI 开关
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name,extra", [
    ("translate", ["10000021"]),
    ("chain", ["hello"]),
    ("fetch", []),
    ("folders", []),
])
def test_verbosity_flags_accepted(name, extra):
    args = cli.build_parser().parse_args([name] + extra + ["-q"])
    assert args.quiet is True and args.verbose is False
    args = cli.build_parser().parse_args([name] + extra + ["--verbose"])
    assert args.verbose is True and args.quiet is False
    args = cli.build_parser().parse_args([name] + extra)
    assert args.quiet is False and args.verbose is False


def test_verbosity_flags_mutually_exclusive():
    with pytest.raises(SystemExit) as e:
        cli.build_parser().parse_args(["translate", "10000021", "-q", "--verbose"])
    assert e.value.code == 2


# --------------------------------------------------------------------------- #
# 结果行里的后端标注
# --------------------------------------------------------------------------- #
def test_providers_of_dedupes_and_truncates():
    from story_tr import stories

    assert stories.providers_of({}) == "-"
    assert stories.providers_of({"steps": [{"provider": "google"}]}) == "google"
    assert stories.providers_of({"steps": [{"provider": "google"},
                                           {"provider": "google"}]}) == "google"
    assert stories.providers_of({"steps": [{"provider": "google"},
                                           {"provider": "bing"}]}) == "google/bing"
    assert stories.providers_of({"steps": [{"provider": "google"}, {"provider": "bing"},
                                           {"provider": "baidu"}]}) == "google/…"
