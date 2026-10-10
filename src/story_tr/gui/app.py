# -*- coding: utf-8 -*-
"""story-tr 主窗口：翻译 / 抓取 / 清理 / 结果浏览 四个页签 + 可折叠日志面板。

设计要点：
- **复用包内核心**：直接调用 `stories.main` / `sync.main` / `clean.main`，
  参数由 `gui.argv` 用真实 CLI 解析器构造，因此与命令行语义完全一致；
- **窗口级工具条**：开始 / 停止 / 当前任务状态常驻窗口顶部，切换页签不丢状态；
- **进度来自核心**的 `on_progress` 回调、**取消来自** `cancel`（`threading.Event`）；
- 核心既有 logging 也有 `print`，二者都经 `StreamRelay` 接管 stdout/stderr 转发到日志面板
  （`report.setup_console` 只清理自己管理的 handler，不会破坏这次接管）。
"""
from __future__ import annotations

import html
import sys
import time
from datetime import datetime

from PySide6.QtCore import QSettings, Qt, Signal
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
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
    QRadioButton,
    QScrollArea,
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
PART_LABELS = [("both", "两者都要（默认）"),
               ("full", "只出全文（--full-only）"),
               ("segments", "只出段落（--segments-only）")]
# 日志级别着色（None = 跟随默认前景色）
LEVEL_COLORS = {"ERROR": "#c0392b", "WARNING": "#b7791f", "WARN": "#b7791f",
                "STEP": "#2f6fed", "DEBUG": "#7a8194"}


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
        import json
        out.append("\n" + json.dumps(data, ensure_ascii=False, indent=2)[:6000])
    return "\n".join(out)


def fmt_elapsed(seconds: float) -> str:
    """秒 → `45s` / `3m21s`。"""
    total = int(max(seconds, 0))
    if total < 60:
        return "%ds" % total
    return "%dm%02ds" % (total // 60, total % 60)


def _as_bool(value) -> bool:
    """QSettings 在不同平台可能回 bool 或 `"true"` 字符串，统一成 bool。"""
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes")


# --------------------------------------------------------------------------- #
# 页签基类
# --------------------------------------------------------------------------- #
class _TaskTab(QScrollArea):
    """页签基类：负责「构造任务」与「展示进度」，内容**可滚动**。

    自身即为 `QScrollArea` —— 屏幕缩放较大或窗口被压小时，内容超出就出现滚动条，
    而不是把窗口最小高度顶到屏幕之外（Windows 会因此报 `Unable to set geometry`）。

    「开始 / 停止」按钮由窗口级工具条统一承载，页签只实现 `build_job()` 与进度展示。
    子类把控件加进 `self.body_layout` 即可。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        body = QWidget()
        self.body_layout = QVBoxLayout(body)
        self.setWidget(body)
        self.bar = QProgressBar()
        self.bar.setTextVisible(True)
        self.status = QLabel("就绪")
        self.status.setWordWrap(True)

    # 子类实现：返回 (fn, argv, hooks)
    def build_job(self):
        raise NotImplementedError

    def on_progress(self, info: dict):
        """默认实现：显示 `done/total`（子类可覆盖为更具体的文案）。"""
        done = info.get("done")
        total = info.get("total") or info.get("tasks")
        if done is not None and total:
            self.status.setText("%s/%s" % (done, total))

    def begin(self):
        """任务开始：清零进度条与状态行。"""
        self.bar.setMaximum(1)
        self.bar.setValue(0)
        self.status.setText("运行中…")

    def finish(self, rc: int, label: str):
        """任务结束：**保留**进度条数值，仅追加结果到状态行。"""
        self.status.setText("%s · 退出码 %d（%s）" % (self.status.text(), rc, label))

    def set_running(self, running: bool):
        """任务运行中：子类可禁用运行期不应操作的控件。"""

    def save_settings(self, settings: QSettings):
        """把「表单配置」写入 QSettings（不含本次目标，如角色 id）。"""

    def load_settings(self, settings: QSettings):
        """从 QSettings 恢复表单配置。"""


class TranslateTab(_TaskTab):
    """批量翻译：参数分「基本 / 输出内容 / 速率与后端」三组，且随模式联动。"""

    def __init__(self, parent=None):
        super().__init__(parent)

        # ---- 基本 ----
        self.char_ids = QLineEdit()
        self.char_ids.setPlaceholderText("如 10000002-10000030 或 10000021,10000025；留空＝全部")
        self.all_chars = QCheckBox("全部角色（--all-characters）")
        self.all_chars.setChecked(True)
        self.story_filter = QLineEdit()
        self.story_filter.setPlaceholderText("留空＝该角色全部含内容故事")

        self.mode_group = QButtonGroup(self)
        self.mode_btns = {}
        mode_row = QHBoxLayout()
        mode_row.setContentsMargins(0, 0, 0, 0)
        for i, name in enumerate(MODES):
            btn = QRadioButton(name)
            self.mode_group.addButton(btn, i)
            self.mode_btns[name] = btn
            mode_row.addWidget(btn)
        self.mode_btns["fixed"].setChecked(True)
        mode_wrap = QWidget()
        mode_wrap.setLayout(mode_row)

        self.chain = QComboBox()
        self.chain.addItems(["asia", "eu", "amer", "sea"])
        self.chain.setToolTip("推荐 asia（默认）。仅 fixed 模式生效。")
        self.custom_langs = QLineEdit()
        self.custom_langs.setPlaceholderText("custom 模式：en,ja,ko,…")
        self.steps = QSpinBox()
        self.steps.setRange(1, 200)
        self.steps.setValue(20)
        self.steps.setToolTip("random 模式抽取的语言数量（CLI 默认 20）。")

        base_form = QFormLayout()
        base_form.addRow("角色 id / 范围", self.char_ids)
        base_form.addRow("", self.all_chars)
        base_form.addRow("故事（--stories）", self.story_filter)
        base_form.addRow("模式", mode_wrap)
        base_form.addRow("链（fixed）", self.chain)
        base_form.addRow("自定义语言（custom）", self.custom_langs)
        base_form.addRow("随机步数（random）", self.steps)

        # ---- 输出内容 ----
        self.parts_group = QButtonGroup(self)
        self.parts_btns = {}
        parts_box = QVBoxLayout()
        parts_box.setContentsMargins(0, 0, 0, 0)
        for key, label in PART_LABELS:
            btn = QRadioButton(label)
            self.parts_group.addButton(btn)
            self.parts_btns[key] = btn
            parts_box.addWidget(btn)
        self.parts_btns["both"].setChecked(True)
        self.overwrite = QCheckBox("已有结果也重跑（--overwrite）")
        self.overwrite.setToolTip("默认跳过已有结果（可断点续跑）；仅在需要重跑时才勾选。")
        self.no_tts = QCheckBox("不生成语音（--no-tts）")
        self.no_tts.setToolTip("推荐首次跑全量时勾选：省去每份结果的 gTTS 请求与 mp3 体积。")
        self.verbose = QCheckBox("日志显示每步明细（--verbose）")
        self.verbose.setToolTip("默认只显示分组行 + 每份结果一行；"
                                "勾选后追加每次翻译步骤的中间结果（量很大）。")
        out_box = QVBoxLayout()
        out_box.setContentsMargins(0, 0, 0, 0)
        out_box.addLayout(parts_box)
        for w in (self.overwrite, self.no_tts, self.verbose):
            out_box.addWidget(w)

        # ---- 速率与后端 ----
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
        self.min_interval.setToolTip("请求最小间隔。推荐 %.1f（与 CLI 默认一致）；0＝关闭节流。"
                                     "百度官方通道另有 1.1 秒下限，无法通过此项调低。"
                                     % gargv.DEFAULT_MIN_INTERVAL)
        rate_form = QFormLayout()
        rate_form.addRow("后端", self.provider)
        rate_form.addRow("并发线程", self.workers)
        rate_form.addRow("最小间隔", self.min_interval)

        box = QGroupBox("翻译参数")
        b = QVBoxLayout(box)
        b.addWidget(self._group("基本", base_form))
        b.addWidget(self._group("输出内容", out_box))
        b.addWidget(self._group("速率与后端", rate_form))
        b.addWidget(self.bar)
        b.addWidget(self.status)
        lay = self.body_layout
        lay.addWidget(box)
        lay.addStretch(1)

        self.mode_group.buttonToggled.connect(self._sync_mode)
        self._sync_mode()

    @staticmethod
    def _group(title, inner):
        """把一个布局包进带标题的分组框。"""
        g = QGroupBox(title)
        v = QVBoxLayout(g)
        v.addLayout(inner)
        return g

    # ------------------------------------------------------------------ 联动
    def current_mode(self) -> str:
        btn = self.mode_group.checkedButton()
        return btn.text() if btn is not None else "fixed"

    def current_parts(self) -> str:
        for key, btn in self.parts_btns.items():
            if btn.isChecked():
                return key
        return "both"

    def _sync_mode(self, *_args, **_kwargs):
        """按当前模式启用/置灰与之相关的参数。"""
        mode = self.current_mode()
        self.chain.setEnabled(mode == "fixed")
        self.custom_langs.setEnabled(mode == "custom")
        self.steps.setEnabled(mode == "random")

    # ------------------------------------------------------------------ 任务
    def build_job(self, reset=False):
        provider = "" if self.provider.currentIndex() == 0 else self.provider.currentText()
        parts = self.current_parts()
        argv_list = gargv.translate_argv(
            char_ids=self.char_ids.text(), stories=self.story_filter.text(),
            all_characters=self.all_chars.isChecked() and not self.char_ids.text().strip(),
            mode=self.current_mode(), chain=self.chain.currentText(),
            languages=self.custom_langs.text(), steps=self.steps.value(), provider=provider,
            workers=self.workers.value(), min_interval=float(self.min_interval.value()),
            full_only=(parts == "full"), segments_only=(parts == "segments"),
            overwrite=self.overwrite.isChecked(), no_tts=self.no_tts.isChecked(),
            reset=reset, verbose=self.verbose.isChecked())
        return stories.main, argv_list, True

    def on_progress(self, info: dict):
        done, total = info.get("done", 0), info.get("tasks") or 0
        self.bar.setMaximum(max(total, 1))
        self.bar.setValue(min(done, total))
        self.status.setText("角色 %s（%d/%d）  %d/%d 份" % (
            info.get("char_id", ""), info.get("char_index", 0), info.get("chars", 0), done, total))

    # -------------------------------------------------------------- 持久化
    def save_settings(self, settings: QSettings):
        settings.setValue("translate/mode", self.current_mode())
        settings.setValue("translate/parts", self.current_parts())
        settings.setValue("translate/chain", self.chain.currentText())
        settings.setValue("translate/languages", self.custom_langs.text())
        settings.setValue("translate/steps", self.steps.value())
        settings.setValue("translate/provider", self.provider.currentIndex())
        settings.setValue("translate/workers", self.workers.value())
        settings.setValue("translate/min_interval", float(self.min_interval.value()))
        settings.setValue("translate/overwrite", self.overwrite.isChecked())
        settings.setValue("translate/no_tts", self.no_tts.isChecked())
        settings.setValue("translate/verbose", self.verbose.isChecked())

    def load_settings(self, settings: QSettings):
        mode = str(settings.value("translate/mode", "fixed"))
        if mode in self.mode_btns:
            self.mode_btns[mode].setChecked(True)
        parts = str(settings.value("translate/parts", "both"))
        if parts in self.parts_btns:
            self.parts_btns[parts].setChecked(True)
        chain = str(settings.value("translate/chain", "asia"))
        if self.chain.findText(chain) >= 0:
            self.chain.setCurrentText(chain)
        self.custom_langs.setText(str(settings.value("translate/languages", "")))
        self.steps.setValue(int(settings.value("translate/steps", 20)))
        self.provider.setCurrentIndex(int(settings.value("translate/provider", 0)))
        self.workers.setValue(int(settings.value("translate/workers", 1)))
        self.min_interval.setValue(float(settings.value(
            "translate/min_interval", gargv.DEFAULT_MIN_INTERVAL)))
        self.overwrite.setChecked(_as_bool(settings.value("translate/overwrite", False)))
        self.no_tts.setChecked(_as_bool(settings.value("translate/no_tts", False)))
        self.verbose.setChecked(_as_bool(settings.value("translate/verbose", False)))
        self._sync_mode()


class FetchTab(_TaskTab):
    """数据抓取（增量 / 更新 / 强制重抓）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.mode = QComboBox()
        self.mode.addItems(["增量（只补缺失）", "更新（--update）", "强制重抓（--refresh）"])
        self.verbose = QCheckBox("显示明细（--verbose）")
        form = QFormLayout()
        form.addRow("模式", self.mode)
        box = QGroupBox("抓取参数")
        b = QVBoxLayout(box)
        b.addLayout(form)
        b.addWidget(self.verbose)
        b.addWidget(self.bar)
        b.addWidget(self.status)
        lay = self.body_layout
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

    def save_settings(self, settings: QSettings):
        settings.setValue("fetch/mode", self.mode.currentIndex())
        settings.setValue("fetch/verbose", self.verbose.isChecked())

    def load_settings(self, settings: QSettings):
        self.mode.setCurrentIndex(int(settings.value("fetch/mode", 0)))
        self.verbose.setChecked(_as_bool(settings.value("fetch/verbose", False)))


class CleanTab(_TaskTab):
    """清理 result/（默认只预览）。"""

    run_requested = Signal(object, list, bool)

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
        form = QFormLayout()
        form.addRow("角色 id / 范围", self.char_ids)
        form.addRow("", self.all_chars)
        form.addRow("故事（--stories）", self.story_filter)

        self.preview_btn = QPushButton("预览（--dry-run）")
        self.preview_btn.clicked.connect(lambda: self._go(dry_run=True))
        self.exec_btn = QPushButton("执行清理")
        self.exec_btn.clicked.connect(lambda: self._go(dry_run=False))
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(self.preview_btn)
        h.addWidget(self.exec_btn)
        h.addStretch(1)

        box = QGroupBox("清理参数")
        b = QVBoxLayout(box)
        b.addLayout(form)
        for w in (self.keep_state, self.verbose):
            b.addWidget(w)
        b.addWidget(row)
        b.addWidget(self.bar)
        b.addWidget(self.status)
        lay = self.body_layout
        lay.addWidget(box)
        lay.addWidget(QLabel("提示：预览与执行都会把输出写入下方日志面板。"
                             "「开始」按钮在本页不启用，请用上面的预览 / 执行。"))
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

    def set_running(self, running: bool):
        self.preview_btn.setEnabled(not running)
        self.exec_btn.setEnabled(not running)

    def save_settings(self, settings: QSettings):
        settings.setValue("clean/keep_state", self.keep_state.isChecked())
        settings.setValue("clean/verbose", self.verbose.isChecked())

    def load_settings(self, settings: QSettings):
        self.keep_state.setChecked(_as_bool(settings.value("clean/keep_state", False)))
        self.verbose.setChecked(_as_bool(settings.value("clean/verbose", False)))


class BrowseTab(QWidget):
    """只读浏览：角色 → 故事 → 翻译结果（角色列表带搜索）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._all_chars = []
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索角色（id / 中文名 / 英文名）")
        self.search.textChanged.connect(self._filter_chars)
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
        lv.addWidget(self.search)
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
        split.setSizes([260, 240, 620])
        lay = QVBoxLayout(self)
        lay.addWidget(split)
        self.reload()

    def reload(self):
        try:
            self._all_chars = list(mtools.list_characters())
        except Exception as exc:  # noqa: BLE001
            self._all_chars = []
            self.detail.setPlainText("读取 data/character.json 失败：%s" % exc)
        self._filter_chars()

    def _filter_chars(self):
        needle = self.search.text().strip().lower()
        self.chars.clear()
        for c in self._all_chars:
            blob = "%s %s %s" % (c.get("id", ""), c.get("name_zh") or "", c.get("name_en") or "")
            if needle and needle not in blob.lower():
                continue
            item = QListWidgetItem("%s  %s (%s)" % (c.get("id", ""), c.get("name_zh") or "",
                                                    c.get("name_en") or ""))
            item.setData(Qt.ItemDataRole.UserRole, c.get("id"))
            self.chars.addItem(item)

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
        self.resize(1100, 800)
        self.runner = None
        self._last_tab = None
        self._started_at = None

        self.translate_tab = TranslateTab()
        self.fetch_tab = FetchTab()
        self.clean_tab = CleanTab()
        self.browse_tab = BrowseTab()
        self.tabs = QTabWidget()
        self.tabs.addTab(self.translate_tab, "翻译")
        self.tabs.addTab(self.fetch_tab, "抓取")
        self.tabs.addTab(self.clean_tab, "清理")
        self.tabs.addTab(self.browse_tab, "结果浏览")
        self.tabs.currentChanged.connect(self._sync_toolbar)
        self.clean_tab.run_requested.connect(
            lambda fn, argv_list, hooks: self._start(fn, argv_list, hooks, self.clean_tab))

        # ------------------------------------------------ 窗口级工具条 + 状态栏
        self.run_btn = QPushButton("开始")
        self.run_btn.clicked.connect(self._on_run_current)
        self.stop_btn = QPushButton("停止")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._on_stop)
        self.reset_btn = QPushButton("重置批次并重跑")
        self.reset_btn.setVisible(False)
        self.reset_btn.clicked.connect(self._on_reset_rerun)
        self.task_label = QLabel("就绪")
        self.task_label.setWordWrap(True)
        toolbar = QWidget()
        th = QHBoxLayout(toolbar)
        th.setContentsMargins(0, 0, 0, 0)
        th.addWidget(self.run_btn)
        th.addWidget(self.stop_btn)
        th.addWidget(self.reset_btn)
        th.addSpacing(8)
        th.addWidget(self.task_label, 1)
        self.statusBar().showMessage("就绪")

        # ---------------------------------------------------------- 日志面板
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(5000)
        self.log_toggle = QPushButton("运行日志 ▾")
        self.log_toggle.setCheckable(True)
        self.log_toggle.setChecked(True)
        self.log_toggle.toggled.connect(self._toggle_log)
        self.log_search = QLineEdit()
        self.log_search.setPlaceholderText("在日志中查找…")
        self.log_search.returnPressed.connect(self._find_next)
        find_btn = QPushButton("查找")
        find_btn.clicked.connect(self._find_next)
        self.find_btn = find_btn
        self.clear_btn = QPushButton("清空")
        self.clear_btn.clicked.connect(self.log_view.clear)
        self.save_btn = QPushButton("保存日志…")
        self.save_btn.clicked.connect(self._save_log)
        log_head = QWidget()
        lh = QHBoxLayout(log_head)
        lh.setContentsMargins(0, 0, 0, 0)
        lh.addWidget(self.log_toggle)
        lh.addStretch(1)
        lh.addWidget(self.log_search)
        lh.addWidget(self.find_btn)
        lh.addWidget(self.clear_btn)
        lh.addWidget(self.save_btn)
        log_box = QGroupBox()
        lb = QVBoxLayout(log_box)
        lb.addWidget(log_head)
        lb.addWidget(self.log_view)

        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(self.tabs)
        split.addWidget(log_box)
        split.setSizes([560, 220])

        head = QLabel("工作目录：%s　（由 --data-dir / $STORY_TR_HOME / 向上查找决定，启动后不可更改）"
                      % paths.home())
        head.setWordWrap(True)
        central = QWidget()
        lay = QVBoxLayout(central)
        lay.addWidget(head)
        lay.addWidget(toolbar)
        lay.addWidget(split)
        self.setCentralWidget(central)

        # 日志面板：接管 stdout/stderr（核心既有 logging 也有 print，两者都要收）
        self.relay = StreamRelay(self)
        self.relay.line.connect(self._append_log)
        self._restore_streams = None
        self._append_log("就绪。等效命令行会打印在这里；日志同时按 CLI 规则写入 logs/。")

        self._settings = QSettings("story-tr", "story-tr-gui")
        self._load_settings()
        self._sync_toolbar()

    # ---------------------------------------------------------------- 运行
    def _sync_toolbar(self, *_args):
        """按当前页签决定「开始」是否可用（运行中不改，交给 `_set_busy`）。"""
        if self.runner is not None and self.runner.isRunning():
            return
        tab = self.tabs.currentWidget()
        can = hasattr(tab, "build_job") and tab is not self.clean_tab
        self.run_btn.setEnabled(can)
        if tab is self.clean_tab:
            self.run_btn.setToolTip("「清理」页请用页内的「预览 / 执行清理」按钮。")
        elif not hasattr(tab, "build_job"):
            self.run_btn.setToolTip("「结果浏览」页没有可执行的任务。")
        else:
            self.run_btn.setToolTip("")

    def _on_run_current(self):
        tab = self.tabs.currentWidget()
        if not hasattr(tab, "build_job") or tab is self.clean_tab:
            return
        self._start(*tab.build_job(), tab)

    def _on_reset_rerun(self):
        """退出码 3 的出路：按当前参数加 `--reset` 重新开始。"""
        self.reset_btn.setVisible(False)
        self._start(*self.translate_tab.build_job(reset=True), self.translate_tab)

    def _start(self, fn, argv_list, hooks, tab):
        if self.runner is not None and self.runner.isRunning():
            QMessageBox.warning(self, "忙碌", "已有任务在运行，请先点击「停止」。")
            return
        try:
            ns = gargv.parse(argv_list)
        except SystemExit:
            self._append_log("参数不合法：story-tr " + " ".join(argv_list))
            return
        self.reset_btn.setVisible(False)
        self._append_log("$ story-tr " + " ".join(argv_list))
        self._last_tab = tab
        self._started_at = time.monotonic()
        tab.begin()
        self.task_label.setText(tab.status.text())
        self.statusBar().showMessage("运行中…")
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
        tab = self._last_tab or self.tabs.currentWidget()
        if hasattr(tab, "on_progress"):
            tab.on_progress(info)
            self.task_label.setText(tab.status.text())

    def _restore(self):
        """还原 stdout/stderr（幂等）。"""
        if self._restore_streams is not None:
            self._restore_streams()
            self._restore_streams = None

    def _on_done(self, rc: int):
        self._restore()
        label = EXIT_LABELS.get(rc, "未知")
        self._append_log("任务结束：退出码 %d —— %s" % (rc, label))
        elapsed = fmt_elapsed(time.monotonic() - self._started_at) if self._started_at else "—"
        tab = self._last_tab
        if tab is not None:
            tab.finish(rc, label)
            self.task_label.setText(tab.status.text())
        self.statusBar().showMessage("退出码 %d（%s） · 用时 %s" % (rc, label, elapsed))
        self._set_busy(False)
        if rc == 3 and tab is self.translate_tab:
            self.reset_btn.setVisible(True)
            self._append_log("提示：参数与上次批量任务不一致。"
                             "可点工具条上的「重置批次并重跑」按当前参数重新开始。")
        self.browse_tab.reload()

    def _set_busy(self, busy: bool):
        for tab in (self.translate_tab, self.fetch_tab, self.clean_tab):
            tab.set_running(busy)
        if busy:
            self.run_btn.setEnabled(False)
            self.stop_btn.setEnabled(True)
        else:
            self.stop_btn.setEnabled(False)
            self._sync_toolbar()

    # ---------------------------------------------------------------- 日志
    def _append_log(self, line: str):
        level = line.split(None, 1)[0].upper() if line else ""
        color = LEVEL_COLORS.get(level)
        text = html.escape(line) if line else "&nbsp;"
        if color:
            self.log_view.appendHtml('<span style="color:%s">%s</span>' % (color, text))
        else:
            self.log_view.appendHtml(text)
        bar = self.log_view.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _toggle_log(self, shown: bool):
        self.log_view.setVisible(shown)
        for w in (self.log_search, self.find_btn, self.clear_btn, self.save_btn):
            w.setVisible(shown)
        self.log_toggle.setText("运行日志 %s" % ("▾" if shown else "▸"))

    def _find_next(self):
        needle = self.log_search.text().strip()
        if not needle:
            return
        if self.log_view.find(needle):
            return
        cursor = self.log_view.textCursor()  # 从顶再找一遍（等价于循环）
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        self.log_view.setTextCursor(cursor)
        if not self.log_view.find(needle):
            self.statusBar().showMessage("日志中未找到：%s" % needle)

    def _save_log(self):
        default = "story-tr-gui-%s.log" % datetime.now().strftime("%Y%m%d_%H%M%S")
        path, _ = QFileDialog.getSaveFileName(self, "保存日志", default, "日志 (*.log *.txt)")
        if not path:
            return
        with open(path, "w", encoding="utf-8") as f:
            f.write(self.log_view.toPlainText())
        self._append_log("日志已保存：%s" % path)

    # -------------------------------------------------------------- 持久化
    def _save_settings(self):
        for tab in (self.translate_tab, self.fetch_tab, self.clean_tab):
            tab.save_settings(self._settings)
        self._settings.setValue("ui/tab", self.tabs.currentIndex())
        self._settings.setValue("ui/log_visible", self.log_toggle.isChecked())

    def _load_settings(self):
        for tab in (self.translate_tab, self.fetch_tab, self.clean_tab):
            try:
                tab.load_settings(self._settings)
            except (TypeError, ValueError):  # 旧版本残留的坏值 → 回退默认
                pass
        index = int(self._settings.value("ui/tab", 0))
        if 0 <= index < self.tabs.count():
            self.tabs.setCurrentIndex(index)
        visible = _as_bool(self._settings.value("ui/log_visible", True))
        self.log_toggle.setChecked(visible)
        self._toggle_log(visible)

    def closeEvent(self, event):  # noqa: N802 - Qt 约定
        """关闭时还原 stdout/stderr 并保存界面配置。"""
        self._restore()
        self._save_settings()
        super().closeEvent(event)


def main(argv=None) -> int:
    app = QApplication(list(sys.argv if argv is None else argv))
    app.setApplicationName("story-tr")
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
