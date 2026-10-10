# -*- coding: utf-8 -*-
"""GUI 界面（gui.app / gui.runner）：离屏构造窗口、检查页签与任务参数。

未安装 PySide6 时整组跳过（`pip install -e ".[gui]"` 后可跑）。
"""
import logging
import os

import pytest

pytest.importorskip("PySide6")

# 必须在创建 QApplication 之前设置：无显示器环境也能构造控件
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from story_tr import stories, sync  # noqa: E402
from story_tr.gui import app as gapp  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


def test_main_window_has_four_tabs(qapp):
    win = gapp.MainWindow()
    try:
        titles = [win.tabs.tabText(i) for i in range(win.tabs.count())]
        assert titles == ["翻译", "抓取", "清理", "结果浏览"]
        assert win.windowTitle().startswith("story-tr")
    finally:
        win.close()


def test_translate_tab_builds_core_job(qapp):
    tab = gapp.TranslateTab()
    tab.char_ids.setText("10000021")
    tab.all_chars.setChecked(False)
    fn, argv_list, hooks = tab.build_job()
    assert fn is stories.main
    assert argv_list[0] == "translate" and "10000021" in argv_list
    assert hooks is True  # 需要 on_progress / cancel


def test_fetch_tab_maps_mode(qapp):
    tab = gapp.FetchTab()
    tab.mode.setCurrentIndex(1)
    fn, argv_list, hooks = tab.build_job()
    assert fn is sync.main
    assert "--update" in argv_list and hooks is True


def test_clean_tab_preview_then_execute(qapp):
    tab = gapp.CleanTab()
    _, preview, hooks = tab.build_job(dry_run=True)
    assert "--dry-run" in preview and "--yes" not in preview
    _, run, hooks2 = tab.build_job(dry_run=False)
    assert "--yes" in run and "--dry-run" not in run
    assert hooks is False and hooks2 is False  # clean 核心没有进度回调


def test_progress_widgets_update(qapp):
    tab = gapp.TranslateTab()
    tab.on_progress({"event": "progress", "char_id": "10000021", "char_index": 2,
                     "chars": 124, "tasks": 93, "done": 12})
    assert tab.bar.maximum() == 93 and tab.bar.value() == 12
    assert "10000021" in tab.status.text()

    fetch = gapp.FetchTab()
    fetch.on_progress({"event": "progress", "done": 3, "total": 10, "char_id": "10000002"})
    assert fetch.bar.value() == 3 and "3/10" in fetch.status.text()


def test_busy_toggles_buttons(qapp):
    tab = gapp.TranslateTab()
    tab.set_busy(True)
    assert tab.run_btn.isEnabled() is False and tab.stop_btn.isEnabled() is True
    tab.set_busy(False)
    assert tab.run_btn.isEnabled() is True and tab.stop_btn.isEnabled() is False


def test_render_summary_sections():
    text = gapp.render_summary({"id": "10000021", "story": "character_details",
                                "provider": "google", "mode": "fixed/asia+批量",
                                "generated_at": "2026-10-11T03:13:05",
                                "title": {"source": "角色详细", "final": "角色详情"},
                                "full": {"final": "全文"},
                                "segments": [{"index": 1, "source": "原", "final": "译"}]})
    for section in ("【标题】", "【全文】", "【分段 1 段】"):
        assert section in text
    assert "角色详细" in text and "全文" in text


def test_render_summary_falls_back_to_json():
    text = gapp.render_summary({"unexpected": 1})
    assert "unexpected" in text


def test_log_handler_emits_signal(qapp):
    handler = gapp.QtLogHandler()
    seen = []
    handler.message.connect(seen.append)
    handler.emit(logging.LogRecord("x", logging.INFO, __file__, 1, "你好", None, None))
    assert seen == ["你好"]


def test_runner_calls_hooks_and_reports_exit_code(qapp):
    from story_tr.gui.runner import TaskRunner

    calls = {}

    def fake(args, on_progress=None, cancel=None):
        calls["args"] = args
        calls["has_hooks"] = on_progress is not None and cancel is not None
        return 7

    runner = TaskRunner(fake, "ARGS", hooks=True)
    codes = []
    runner.finished_with.connect(codes.append)
    runner.start()
    assert runner.wait(5000) is True
    qapp.processEvents()
    assert calls == {"args": "ARGS", "has_hooks": True}
    assert codes == [7]


def test_runner_without_hooks(qapp):
    from story_tr.gui.runner import TaskRunner

    seen = []

    def fake(args):
        seen.append(args)
        return 0

    runner = TaskRunner(fake, "ARGS", hooks=False)
    runner.start()
    assert runner.wait(5000) is True
    assert seen == ["ARGS"]
