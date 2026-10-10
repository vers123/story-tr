# -*- coding: utf-8 -*-
"""GUI 后台执行：把包内核心放到 `QThread` 里跑，并把控制台输出转发到界面。

- `StreamRelay`：`sys.stdout` / `sys.stderr` 的替身，把文本按行转成 Qt 信号。
  它同时让 `report.interactive()` 变为 False（`isatty()` 恒为假），于是**进度条自动禁用**、
  日志走普通行输出 —— 面板里不会出现 `\\r` 重绘，且 `print` 与日志**都能收到**。
- `TaskRunner`：执行 `fn(args, on_progress=…, cancel=…)`，把进度、异常与退出码发回 GUI。
"""
from __future__ import annotations

import sys
import threading

from PySide6.QtCore import QObject, QThread, Signal


class StreamRelay(QObject):
    """把写入 stdout/stderr 的文本按行转发为 Qt 信号。"""

    line = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._buf = ""

    def write(self, text):
        if text is None:
            return 0
        text = str(text)
        self._buf += text
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            line = line.rstrip("\r")
            if line:
                self.line.emit(line)
        return len(text)

    def flush(self):
        if self._buf:
            self.line.emit(self._buf.rstrip("\r"))
            self._buf = ""

    def isatty(self):  # 关键：让 report.interactive() 为 False → 禁用进度条
        return False


def capture_streams(relay: StreamRelay):
    """把 stdout / stderr 指向 relay，返回**还原函数**。"""
    saved = (sys.stdout, sys.stderr)
    sys.stdout = sys.stderr = relay

    def restore():
        sys.stdout, sys.stderr = saved
        relay.flush()

    return restore


class TaskRunner(QThread):
    """执行核心函数；`hooks=False` 时按 `fn(args)` 调用。

    `stop()` 只置位取消标志 —— 核心会在下一个检查点**保存进度**后返回退出码 `6`，
    不强制杀线程，避免留下半个结果文件。
    """

    progress = Signal(dict)
    finished_with = Signal(int)
    error = Signal(str)

    def __init__(self, fn, args, hooks=True, parent=None):
        super().__init__(parent)
        self._fn = fn
        self._args = args
        self._hooks = hooks
        self.cancel = threading.Event()

    def run(self):
        try:
            if self._hooks:
                rc = self._fn(self._args, on_progress=self.progress.emit, cancel=self.cancel)
            else:
                rc = self._fn(self._args)
        except BaseException as exc:  # noqa: BLE001 - SystemExit 等也不能让 GUI 卡在「忙」
            self.error.emit("%s: %s" % (type(exc).__name__, exc))
            rc = -1
        self.finished_with.emit(rc)

    def stop(self):
        self.cancel.set()
