# -*- coding: utf-8 -*-
"""story-tr 主窗口：翻译 / 抓取 / 清理 / 结果浏览 四个页签 + 实时日志面板。

设计要点：
- **复用包内核心**：直接调用 `stories.main` / `sync.main` / `clean.main`，
  参数由 `gui.argv` 用真实 CLI 解析器构造，因此与命令行语义完全一致；
- 进度来自核心的 `on_progress` 回调、取消来自 `cancel`（`threading.Event`）；
- 日志直接挂 `translate_chain` logger（`report.setup_console` 不会清掉外部 handler）。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .. import __version__, clean, paths, stories, sync
from ..mcp import tools as mtools
from . import argv as gargv
from .runner import StreamRelay, TaskRunner, capture_streams

EXIT_LABELS = {
    -1: "异常（见日志）",
    0: "完成",
    2: "语言不受支持",
    3: "批量状态与上次不一致",
    4: "已暂停（限流或业务错误）",
    5: "后端连接失败",
    6: "已取消（进度已保存）",
}
PROVIDERS = ["（自动 google→bing→baidu）", "google", "bing", "baidu"]
MODES = ["fixed", "random", "custom"]


def render_summary(data: dict) -> str:
    """把 `segments.json` 渲染成可读文本（标题 / 全文 / 分段）。"""
    out = ["角色 %s · 故事 %s" % (data.get("id", ""), data.get("story", "")),
           "后端 %s · 模式 %s · 生成于 %s" % (data.get("provider", ""), data.get("mode", ""),
                                             data.get("generated_at", ""))]
    title = data.get("title") or {}
    if title:
        out.append("\n【标题】\n%s\n→ %s" % (title.get("source", ""), title.get("final", "")))
    full = data.get("full") or {}
    if full:
        out.append("\n【全文】\n%s" % full.get("final", ""))
    segs = data.get("segments") or []
    if segs:
        out.append("\n【分段 %d 段】" % len(segs))
        for s in segs:
            out.append("\n[%s] %s\n→ %s" % (s.get("index", ""), s.get("source", ""),
                                            s.get("final", "")))
    if len(out) <= 2:  # 结构未知时退回原始 JSON
        out.append("\n" + json.dumps(data, ensure_ascii=False, indent=2)[:6000])
    return "\n".join(out)


class _TaskTab(QWidget):
    """带「开始 / 停止 + 进度条 + 状态」的页签基类。"""

    run_requested = Signal(object, list, bool)
    stop_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.run_btn = QPushButton("开始")
        self.stop_btn = QPushButton("停止")
        self.stop_btn.setEnabled(False)
        self.bar = QProgressBar()
        self.status = QLabel("就绪")
        self.run_btn.clicked.connect(self._emit_run)
        self.stop_btn.clicked.connect(self._emit_stop)

    # 子类实现：返回 (fn, argv)
    def build_job(self):
        raise NotImplementedError

    def _emit_run(self):
        fn, argv_list, hooks = self.build_job()
        self.run_requested.emit(fn, argv_list, hooks)

    def _emit_stop(self):
        self.stop_requested.emit()

    def on_progress(self, info: dict):
        """默认实现：显示 `done/total`（子类可覆盖为更具体的文案）。"""
        done = info.get("done")
        total = info.get("total") or info.get("tasks")
        if done is not None and total:
            self.status.setText("%s/%s" % (done, total))

    def set_busy(self, busy: bool):
        self.run_btn.setEnabled(not busy)
        self.stop_btn.setEnabled(busy)
        if not busy:
            self.bar.setValue(0)


class TranslateTab(_TaskTab):
    """批量翻译。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        form = QFormLayout()
        self.char_ids = QLineEdit()
        self.char_ids.setPlaceholderText("如 10000002-10000030 或 10000021,10000025；留空＝全部")
        self.all_chars = QCheckBox("全部角色（--all-characters）")
        self.all_chars.setChecked(True)
        self.story_filter = QLineEdit()
        self.story_filter.setPlaceholderText("留空＝该角色全部含内容故事")
        self.mode = QComboBox()
        self.mode.addItems(MODES)
        self.mode.setToolTip("推荐 fixed：同一批结果可复现、可断点续跑；random 每份独立随机且自动关批量。")
        self.chain = QComboBox()
        self.chain.addItems(["asia", "eu", "amer", "sea"])
        self.chain.setToolTip("推荐 asia（默认）。仅 fixed 模式生效。")
        self.custom_langs = QLineEdit()
        self.custom_langs.setPlaceholderText("custom 模式：en,ja,ko,…")
        self.provider = QComboBox()
        self.provider.addItems(PROVIDERS)
        self.provider.setToolTip("推荐「自动」：google → bing → baidu 依次回退，最稳；"
                                 "本机长期连不上 google 时可固定 bing/baidu，省去超时等待。")
        self.workers = QSpinBox()
        self.workers.setRange(1, 8)
        self.workers.setValue(1)
        self.workers.setToolTip("并发线程数。推荐 1（最稳）；想快可 3~4，"
                                "但更易触发限流 —— 限流时会退避等待，不一定更快。")
        self.min_interval = QDoubleSpinBox()
        self.min_interval.setRange(0.0, 20.0)
        self.min_interval.setSingleStep(0.1)
        self.min_interval.setDecimals(1)
        self.min_interval.setValue(gargv.DEFAULT_MIN_INTERVAL)
        self.min_interval.setSuffix(" 秒")
        self.min_interval.setToolTip("请求最小间隔。推荐 %.1f（与 CLI 默认一致）；0＝关闭节流（易被限流）。"
                                     "百度官方通道另有 1.1 秒下限，无法通过此项调低。"
                                     % gargv.DEFAULT_MIN_INTERVAL)
        form.addRow("角色 id / 范围", self.char_ids)
        form.addRow("", self.all_chars)
        form.addRow("故事（--stories）", self.story_filter)
        form.addRow("模式 / 链", self._row(self.mode, self.chain))
        form.addRow("自定义语言", self.custom_langs)
        form.addRow("后端", self.provider)
        form.addRow("并发线程", self.workers)
        form.addRow("最小间隔", self.min_interval)

        self.full_only = QCheckBox("只出全文（--full-only）")
        self.full_only.setToolTip("批量开启时只省约 20% 请求，主要价值是减少产物文件与磁盘占用。")
        self.segments_only = QCheckBox("只出段落（--segments-only）")
        self.overwrite = QCheckBox("已有结果也重跑（--overwrite）")
        self.overwrite.setToolTip("默认跳过已有结果（可断点续跑）；仅在需要重跑时才勾选。")
        self.no_tts = QCheckBox("不生成语音（--no-tts）")
        self.no_tts.setToolTip("推荐首次跑全量时勾选：省去每份结果的 gTTS 请求与 mp3 体积，之后可单独补跑。")
        self.verbose = QCheckBox("控制台/日志显示每步明细（--verbose）")
        self.verbose.setToolTip("默认只显示分组行 + 每份结果一行；"
                                "勾选后追加每次翻译步骤的中间结果（量很大）。")
        opts = QGroupBox("选项")
        o = QVBoxLayout(opts)
        for w in (self.full_only, self.segments_only, self.overwrite, self.no_tts, self.verbose):
            o.addWidget(w)

        box = QGroupBox("翻译参数")
        b = QVBoxLayout(box)
        b.addLayout(form)
        b.addWidget(opts)
        b.addWidget(self._run_row())
        b.addWidget(self.bar)
        b.addWidget(self.status)
        lay = QVBoxLayout(self)
        lay.addWidget(box)
        lay.addStretch(1)

    @staticmethod
    def _row(*widgets):
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        for x in widgets:
            h.addWidget(x)
        return w

    def _run_row(self):
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(self.run_btn)
        h.addWidget(self.stop_btn)
        h.addStretch(1)
        return w

    def build_job(self):
        provider = "" if self.provider.currentIndex() == 0 else self.provider.currentText()
        argv_list = gargv.translate_argv(
            char_ids=self.char_ids.text(), stories=self.story_filter.text(),
            all_characters=self.all_chars.isChecked() and not self.char_ids.text().strip(),
            mode=self.mode.currentText(), chain=self.chain.currentText(),
            languages=self.custom_langs.text(), provider=provider,
            workers=self.workers.value(), min_interval=float(self.min_interval.value()),
            full_only=self.full_only.isChecked(), segments_only=self.segments_only.isChecked(),
            overwrite=self.overwrite.isChecked(), no_tts=self.no_tts.isChecked(),
            verbose=self.verbose.isChecked())
        return stories.main, argv_list, True

    def on_progress(self, info: dict):
        done, total = info.get("done", 0), info.get("tasks") or 0
        self.bar.setMaximum(max(total, 1))
        self.bar.setValue(min(done, total))
        self.status.setText("角色 %s（%d/%d）  %d/%d 份" % (
            info.get("char_id", ""), info.get("char_index", 0), info.get("chars", 0), done, total))


class FetchTab(_TaskTab):
    """数据抓取（增量 / 更新 / 强制重抓）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.mode = QComboBox()
        self.mode.addItems(["增量（只补缺失）", "更新（--update）", "强制重抓（--refresh）"])
        self.verbose = QCheckBox("显示明细（--verbose）")
        box = QGroupBox("抓取参数")
        b = QVBoxLayout(box)
        form = QFormLayout()
        form.addRow("模式", self.mode)
        b.addLayout(form)
        b.addWidget(self.verbose)
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(self.run_btn)
        h.addWidget(self.stop_btn)
        h.addStretch(1)
        b.addWidget(w)
        b.addWidget(self.bar)
        b.addWidget(self.status)
        lay = QVBoxLayout(self)
        lay.addWidget(box)
        lay.addStretch(1)

    def build_job(self):
        mode = ["incremental", "update", "refresh"][self.mode.currentIndex()]
        return sync.main, gargv.fetch_argv(mode, verbose=self.verbose.isChecked()), True

    def on_progress(self, info: dict):
        done, total = info.get("done", 0), info.get("total") or 0
        self.bar.setMaximum(max(total, 1))
        self.bar.setValue(done)
        self.status.setText("抓取 %d/%d  %s" % (done, total, info.get("char_id", "")))


class CleanTab(_TaskTab):
    """清理 result/（默认只预览）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.char_ids = QLineEdit()
        self.char_ids.setPlaceholderText("留空并勾选「全部角色」＝处理全部")
        self.all_chars = QCheckBox("全部角色（--all-characters）")
        self.all_chars.setChecked(True)
        self.story_filter = QLineEdit()
        self.story_filter.setPlaceholderText("留空＝该角色全部故事")
        self.keep_state = QCheckBox("保留进度记录（--keep-state）")
        self.verbose = QCheckBox("显示明细（--verbose）")
        box = QGroupBox("清理参数")
        b = QVBoxLayout(box)
        form = QFormLayout()
        form.addRow("角色 id / 范围", self.char_ids)
        form.addRow("", self.all_chars)
        form.addRow("故事（--stories）", self.story_filter)
        b.addLayout(form)
        for w in (self.keep_state, self.verbose):
            b.addWidget(w)
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        self.preview_btn = QPushButton("预览（--dry-run）")
        self.preview_btn.clicked.connect(lambda: self._go(dry_run=True))
        self.run_btn.setText("执行清理")
        self.run_btn.clicked.disconnect(self._emit_run)  # 改走带确认的 _go
        self.run_btn.clicked.connect(lambda: self._go(dry_run=False))
        h.addWidget(self.preview_btn)
        h.addWidget(self.run_btn)
        h.addWidget(self.stop_btn)
        h.addStretch(1)
        b.addWidget(row)
        b.addWidget(self.bar)
        b.addWidget(self.status)
        lay = QVBoxLayout(self)
        lay.addWidget(box)
        lay.addWidget(QLabel("提示：预览与执行都会把输出写入下方日志面板。"))
        lay.addStretch(1)

    def _go(self, dry_run: bool):
        if not dry_run:
            ok = QMessageBox.question(
                self, "确认清理", "将删除上述角色的 result/ 内容（result/ 目录本身保留）。是否继续？")
            if ok != QMessageBox.StandardButton.Yes:
                return
        fn, argv_list, hooks = self.build_job(dry_run=dry_run)
        self.run_requested.emit(fn, argv_list, hooks)

    def build_job(self, dry_run=True):
        argv_list = gargv.clean_argv(
            char_ids=self.char_ids.text(), stories=self.story_filter.text(),
            all_characters=self.all_chars.isChecked() and not self.char_ids.text().strip(),
            dry_run=dry_run, keep_state=self.keep_state.isChecked(),
            verbose=self.verbose.isChecked())
        return clean.main, argv_list, False

    def set_busy(self, busy: bool):
        super().set_busy(busy)
        self.preview_btn.setEnabled(not busy)


class BrowseTab(QWidget):
    """只读浏览：角色 → 故事 → 翻译结果。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.chars = QListWidget()
        self.story_list = QListWidget()
        self.detail = QTextEdit()
        self.detail.setReadOnly(True)
        self.refresh_btn = QPushButton("刷新角色列表")
        self.refresh_btn.clicked.connect(self.reload)
        self.chars.currentItemChanged.connect(self._on_char)
        self.story_list.currentItemChanged.connect(self._on_story)
        left = QGroupBox("角色")
        lv = QVBoxLayout(left)
        lv.addWidget(self.refresh_btn)
        lv.addWidget(self.chars)
        mid = QGroupBox("故事")
        mv = QVBoxLayout(mid)
        mv.addWidget(self.story_list)
        right = QGroupBox("翻译结果")
        rv = QVBoxLayout(right)
        rv.addWidget(self.detail)
        split = QSplitter(Qt.Orientation.Horizontal)
        for w in (left, mid, right):
            split.addWidget(w)
        split.setSizes([240, 240, 620])
        lay = QVBoxLayout(self)
        lay.addWidget(split)
        self.reload()

    def reload(self):
        self.chars.clear()
        try:
            for c in mtools.list_characters():
                item = QListWidgetItem("%s  %s (%s)" % (c.get("id", ""), c.get("name_zh") or "",
                                                        c.get("name_en") or ""))
                item.setData(Qt.ItemDataRole.UserRole, c.get("id"))
                self.chars.addItem(item)
        except Exception as exc:  # noqa: BLE001
            self.detail.setPlainText("读取 data/character.json 失败：%s" % exc)

    def _on_char(self, item, _prev=None):
        self.story_list.clear()
        self.detail.setPlainText("")
        if item is None:
            return
        char_id = item.data(Qt.ItemDataRole.UserRole)
        try:
            for s in mtools.list_stories(char_id):
                it = QListWidgetItem("%s  %s（%d 段）" % (s.get("story", ""),
                                                          s.get("title_zh") or "",
                                                          s.get("paragraph_count") or 0))
                it.setData(Qt.ItemDataRole.UserRole, s.get("story"))
                self.story_list.addItem(it)
        except Exception as exc:  # noqa: BLE001
            self.detail.setPlainText("读取故事列表失败：%s" % exc)

    def _on_story(self, item, _prev=None):
        if item is None:
            return
        char_item = self.chars.currentItem()
        if char_item is None:
            return
        char_id = char_item.data(Qt.ItemDataRole.UserRole)
        story = item.data(Qt.ItemDataRole.UserRole)
        try:
            self.detail.setPlainText(render_summary(mtools.get_result_summary(char_id, story)))
        except Exception as exc:  # noqa: BLE001
            self.detail.setPlainText("尚无翻译结果：%s" % exc)


class MainWindow(QMainWindow):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("story-tr %s" % __version__)
        self.resize(1040, 760)
        self.runner = None

        self.translate_tab = TranslateTab()
        self.fetch_tab = FetchTab()
        self.clean_tab = CleanTab()
        self.browse_tab = BrowseTab()
        self.tabs = QTabWidget()
        self.tabs.addTab(self.translate_tab, "翻译")
        self.tabs.addTab(self.fetch_tab, "抓取")
        self.tabs.addTab(self.clean_tab, "清理")
        self.tabs.addTab(self.browse_tab, "结果浏览")

        for tab in (self.translate_tab, self.fetch_tab, self.clean_tab):
            tab.run_requested.connect(self._on_run)
            tab.stop_requested.connect(self._on_stop)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(5000)
        clear_btn = QPushButton("清空")
        clear_btn.clicked.connect(self.log_view.clear)
        save_btn = QPushButton("保存日志…")
        save_btn.clicked.connect(self._save_log)
        log_head = QWidget()
        lh = QHBoxLayout(log_head)
        lh.setContentsMargins(0, 0, 0, 0)
        lh.addWidget(QLabel("运行日志"))
        lh.addStretch(1)
        lh.addWidget(clear_btn)
        lh.addWidget(save_btn)
        log_box = QGroupBox()
        lb = QVBoxLayout(log_box)
        lb.addWidget(log_head)
        lb.addWidget(self.log_view)

        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(self.tabs)
        split.addWidget(log_box)
        split.setSizes([520, 240])

        head = QLabel("工作目录：%s　（由 --data-dir / $STORY_TR_HOME / 向上查找决定，启动后不可更改）"
                      % paths.home())
        head.setWordWrap(True)
        central = QWidget()
        lay = QVBoxLayout(central)
        lay.addWidget(head)
        lay.addWidget(split)
        self.setCentralWidget(central)

        # 日志面板：接管 stdout/stderr（核心既有 logging 也有 print，两者都要收）
        self.relay = StreamRelay(self)
        self.relay.line.connect(self._append_log)
        self._restore_streams = None
        self._append_log("就绪。等效命令行会打印在这里；日志同时按 CLI 规则写入 logs/。")

    # ---------------------------------------------------------------- 运行
    def _on_run(self, fn, argv_list, hooks):
        if self.runner is not None and self.runner.isRunning():
            QMessageBox.warning(self, "忙碌", "已有任务在运行，请先点击「停止」。")
            return
        try:
            ns = gargv.parse(argv_list)
        except SystemExit:
            self._append_log("参数不合法：story-tr " + " ".join(argv_list))
            return
        self._append_log("$ story-tr " + " ".join(argv_list))
        self._set_busy(True)
        self._restore_streams = capture_streams(self.relay)
        self.runner = TaskRunner(fn, ns, hooks=hooks, parent=self)
        self.runner.progress.connect(self._on_progress)
        self.runner.finished_with.connect(self._on_done)
        self.runner.error.connect(lambda msg: self._append_log("任务异常：%s" % msg))
        self.runner.start()

    def _on_stop(self):
        if self.runner is not None and self.runner.isRunning():
            self._append_log("已请求停止：将在当前任务边界保存进度后退出（退出码 6）…")
            self.runner.stop()

    def _on_progress(self, info: dict):
        tab = self.tabs.currentWidget()
        if hasattr(tab, "on_progress"):
            tab.on_progress(info)

    def _restore(self):
        """还原 stdout/stderr（幂等）。"""
        if self._restore_streams is not None:
            self._restore_streams()
            self._restore_streams = None

    def _on_done(self, rc: int):
        self._restore()
        self._append_log("任务结束：退出码 %d —— %s" % (rc, EXIT_LABELS.get(rc, "未知")))
        self._set_busy(False)
        self.browse_tab.reload()

    def _set_busy(self, busy: bool):
        for tab in (self.translate_tab, self.fetch_tab, self.clean_tab):
            tab.set_busy(busy)

    # ---------------------------------------------------------------- 日志
    def _append_log(self, line: str):
        self.log_view.appendPlainText(line)
        bar = self.log_view.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _save_log(self):
        default = "story-tr-gui-%s.log" % datetime.now().strftime("%Y%m%d_%H%M%S")
        path, _ = QFileDialog.getSaveFileName(self, "保存日志", default, "日志 (*.log *.txt)")
        if not path:
            return
        with open(path, "w", encoding="utf-8") as f:
            f.write(self.log_view.toPlainText())
        self._append_log("日志已保存：%s" % path)

    def closeEvent(self, event):  # noqa: N802 - Qt 约定
        """关闭时还原 stdout/stderr（避免把接管状态留给进程内其它使用方）。"""
        self._restore()
        super().closeEvent(event)


def main(argv=None) -> int:
    app = QApplication(list(sys.argv if argv is None else argv))
    app.setApplicationName("story-tr")
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
