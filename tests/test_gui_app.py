# -*- coding: utf-8 -*-
"""GUI 界面（gui.app / gui.runner）：离屏构造窗口、检查页签与任务参数。

未安装 PySide6 时整组跳过（`pip install -e ".[gui]"` 后可跑）。
"""
import os

import pytest

pytest.importorskip("PySide6")

# 必须在创建 QApplication 之前设置：无显示器环境也能构造控件
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from story_tr import stories, sync, video  # noqa: E402
from story_tr.gui import app as gapp  # noqa: E402


@pytest.fixture(scope="module")
def qapp(tmp_path_factory):
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication

    # 把 QSettings 重定向到临时 ini，避免测试读写真实注册表 / 用户配置
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope,
                      str(tmp_path_factory.mktemp("settings")))
    app = QApplication.instance() or QApplication([])
    yield app


def test_main_window_has_five_tabs(qapp):
    win = gapp.MainWindow()
    try:
        titles = [win.tabs.tabText(i) for i in range(win.tabs.count())]
        assert titles == ["翻译", "视频", "抓取", "清理", "结果浏览"]
        assert win.open_btn.text() == "打开结果目录"
        assert win.windowTitle().startswith("story-tr")
    finally:
        win.close()


def test_video_tab_builds_core_job(qapp):
    tab = gapp.VideoTab()
    tab.char_ids.setText("10000021")
    tab.all_chars.setChecked(False)
    fn, argv_list, hooks = tab.build_job()
    assert fn is video.main
    assert argv_list[0] == "video" and "10000021" in argv_list
    assert "--crf" not in argv_list and "--bitrate" in argv_list
    assert hooks is False  # video 没有 on_progress / cancel


def test_translate_tab_builds_core_job(qapp):
    tab = gapp.TranslateTab()
    tab.char_ids.setText("10000021")
    tab.all_chars.setChecked(False)
    fn, argv_list, hooks = tab.build_job()
    assert fn is stories.main
    assert argv_list[0] == "translate" and "10000021" in argv_list
    assert hooks is True  # 需要 on_progress / cancel


def test_translate_tab_defaults_match_cli(qapp):
    """默认必须与 CLI 对齐：曾经是整数框且默认 0，会静默关掉节流（CLI 默认 0.3）。"""
    from story_tr.gui import argv as gargv

    tab = gapp.TranslateTab()
    assert tab.min_interval.value() == pytest.approx(gargv.DEFAULT_MIN_INTERVAL)
    assert tab.workers.value() == 1
    assert tab.all_chars.isChecked() is True
    _, argv_list, _ = tab.build_job()
    assert argv_list[argv_list.index("--min-interval") + 1] == str(gargv.DEFAULT_MIN_INTERVAL)
    assert argv_list[argv_list.index("--mode") + 1] == "fixed"
    assert "--full-only" not in argv_list and "--overwrite" not in argv_list


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


def test_busy_toggles_window_toolbar(qapp):
    """「开始 / 停止」现在常驻窗口级工具条，切换页签不丢状态。"""
    win = gapp.MainWindow()
    try:
        win._set_busy(True)
        assert win.run_btn.isEnabled() is False and win.stop_btn.isEnabled() is True
        win._set_busy(False)
        assert win.stop_btn.isEnabled() is False
    finally:
        win.close()


def test_toolbar_run_disabled_on_clean_and_browse(qapp):
    """清理页要在页内用「预览 / 执行」；结果浏览页无任务 —— 工具条「开始」都禁用。"""
    win = gapp.MainWindow()
    try:
        win.tabs.setCurrentWidget(win.translate_tab)
        assert win.run_btn.isEnabled() is True
        win.tabs.setCurrentWidget(win.clean_tab)
        assert win.run_btn.isEnabled() is False
        win.tabs.setCurrentWidget(win.browse_tab)
        assert win.run_btn.isEnabled() is False
    finally:
        win.close()


def test_parts_radios_are_mutually_exclusive(qapp):
    """回归：这两个选项曾是 QCheckBox，可同时勾选 → 生成互斥参数被解析器拒绝。"""
    from story_tr.gui import argv as gargv

    tab = gapp.TranslateTab()
    tab.parts_btns["full"].setChecked(True)
    assert tab.current_parts() == "full"
    _, argv_list, _ = tab.build_job()
    assert "--full-only" in argv_list and "--segments-only" not in argv_list
    gargv.parse(argv_list)  # 真实解析器必须接受

    tab.parts_btns["segments"].setChecked(True)
    assert tab.current_parts() == "segments"
    assert tab.parts_btns["full"].isChecked() is False  # 单选：旧的自动取消
    _, argv_list, _ = tab.build_job()
    assert "--segments-only" in argv_list and "--full-only" not in argv_list
    gargv.parse(argv_list)


def test_mode_switch_greys_dependent_fields(qapp):
    tab = gapp.TranslateTab()
    tab.mode_btns["fixed"].setChecked(True)
    assert tab.chain.isEnabled() and not tab.custom_langs.isEnabled() \
        and not tab.steps.isEnabled()
    tab.mode_btns["random"].setChecked(True)
    assert tab.steps.isEnabled() and not tab.chain.isEnabled()
    tab.mode_btns["custom"].setChecked(True)
    assert tab.custom_langs.isEnabled() and not tab.steps.isEnabled()


def test_reset_rerun_adds_reset_flag(qapp):
    """退出码 3 的出路：`build_job(reset=True)` 必须带上 `--reset`。"""
    from story_tr.gui import argv as gargv

    tab = gapp.TranslateTab()
    tab.char_ids.setText("10000021")
    _, argv_list, _ = tab.build_job(reset=True)
    assert "--reset" in argv_list
    ns = gargv.parse(argv_list)
    assert ns.reset is True
    _, plain, _ = tab.build_job()
    assert "--reset" not in plain


def test_task_tabs_are_scrollable(qapp):
    """回归：高缩放 / 小屏下窗口会被内容顶破（Windows 报 Unable to set geometry）。

    三个任务页签自身是 QScrollArea，窗口因此可以缩到很小，内容超出时改为滚动。
    """
    from PySide6.QtWidgets import QScrollArea

    win = gapp.MainWindow()
    try:
        for tab in (win.translate_tab, win.video_tab, win.fetch_tab, win.clean_tab):
            assert isinstance(tab, QScrollArea)
            assert tab.widgetResizable() is True
            assert tab.widget() is not None
            assert tab.minimumSizeHint().height() <= 120  # 不再随表单行数增长
        assert win.minimumSizeHint().height() < win.sizeHint().height()
    finally:
        win.close()


def test_done_keeps_progress_and_offers_reset(qapp):
    """完成后进度条不清零；退出码 3 时出现「重置批次并重跑」。"""
    win = gapp.MainWindow()
    try:
        tab = win.translate_tab
        win._last_tab = tab
        win._started_at = gapp.time.monotonic()
        tab.on_progress({"event": "progress", "char_id": "10000021", "char_index": 1,
                         "chars": 1, "tasks": 5, "done": 5})
        win._on_done(0)
        assert tab.bar.value() == 5  # 保留，而不是归零
        assert win.reset_btn.isHidden() is True

        win._on_done(3)
        assert win.reset_btn.isHidden() is False
    finally:
        win.close()


def test_log_panel_toggle_and_search(qapp):
    win = gapp.MainWindow()
    try:
        win._append_log("INFO 可检索的标记")
        win.log_toggle.setChecked(False)
        assert win.log_view.isHidden() is True
        assert win.log_search.isHidden() is True
        win.log_toggle.setChecked(True)
        assert win.log_view.isHidden() is False
        win.log_search.setText("可检索")
        win._find_next()
        assert win.log_view.textCursor().selectedText() == "可检索"
    finally:
        win.close()


def test_settings_roundtrip(qapp, tmp_path):
    """表单配置（不含本次目标 id）经 QSettings 存/取一致。"""
    from PySide6.QtCore import QSettings

    # 用独立的 ini 文件，避免依赖注册表（不同环境可能不可写/有残留值）
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    settings.clear()
    tab = gapp.TranslateTab()
    tab.mode_btns["random"].setChecked(True)
    tab.workers.setValue(4)
    tab.no_tts.setChecked(True)
    tab.save_settings(settings)
    settings.sync()

    restored = gapp.TranslateTab()
    restored.load_settings(settings)
    assert restored.current_mode() == "random"
    assert restored.workers.value() == 4
    assert restored.no_tts.isChecked() is True


def test_as_bool_accepts_qt_variants():
    assert gapp._as_bool(True) is True
    assert gapp._as_bool("true") is True
    assert gapp._as_bool("False") is False
    assert gapp._as_bool("") is False


def test_fmt_elapsed():
    assert gapp.fmt_elapsed(45) == "45s"
    assert gapp.fmt_elapsed(201) == "3m21s"
    assert gapp.fmt_elapsed(-1) == "0s"


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


def test_stream_relay_splits_lines(qapp):
    from story_tr.gui.runner import StreamRelay

    relay = StreamRelay()
    seen = []
    relay.line.connect(seen.append)
    relay.write("a\nb\n")
    relay.write("c")  # 未换行 → 缓冲，不立即发
    assert seen == ["a", "b"]
    relay.flush()
    assert seen == ["a", "b", "c"]
    assert relay.isatty() is False  # 让 report.interactive() 为 False → 禁用进度条


def test_capture_streams_roundtrip(qapp):
    """接管 stdout 后：print 进面板、进度条自动禁用、还原后恢复原流。"""
    import sys

    from story_tr import report
    from story_tr.gui.runner import StreamRelay, capture_streams

    relay = StreamRelay()
    seen = []
    relay.line.connect(seen.append)
    before = sys.stdout
    restore = capture_streams(relay)
    try:
        assert report.interactive() is False
        print("喂给面板")
    finally:
        restore()
    assert sys.stdout is before
    assert "喂给面板" in seen


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
