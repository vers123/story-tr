# -*- coding: utf-8 -*-
"""GUI 后台执行：把包内核心放到 `QThread` 里跑，并把日志转发到界面。"""
from __future__ import annotations

import logging
import threading

from PySide6.QtCore import QObject, QThread, Signal

LOG_NAME = "translate_chain"


class QtLogHandler(QObject, logging.Handler):
    """把 `translate_chain` 日志转成 Qt 信号（跨线程自动队列投递）。"""

    message = Signal(str)

    def __init__(self, parent=None):
        QObject.__init__(self, parent)
        logging.Handler.__init__(self)
        self.setFormatter(logging.Formatter("%(message)s"))

    def emit(self, record):  # noqa: A003 - logging.Handler 约定
        try:
            self.message.emit(self.format(record))
        except Exception:  # noqa: BLE001
            self.handleError(record)


def attach_log_handler(handler: QtLogHandler) -> None:
    """挂到与 CLI 相同的 logger；`report.setup_console` 只清理自己管理的 handler，不会移除它。"""
    logging.getLogger(LOG_NAME).addHandler(handler)


class TaskRunner(QThread):
    """执行 `fn(args, on_progress=…, cancel=…)`；`hooks=False` 时按 `fn(args)` 调用。

    `stop()` 只置位取消标志 —— 核心会在下一个检查点**保存进度**后返回退出码 `6`，
    不做强制杀线程，避免留下半个结果文件。
    """

    progress = Signal(dict)
    finished_with = Signal(int)

    def __init__(self, fn, args, hooks=True, parent=None):
        super().__init__(parent)
        self._fn = fn
        self._args = args
        self._hooks = hooks
        self.cancel = threading.Event()

    def run(self):
        if self._hooks:
            rc = self._fn(self._args, on_progress=self.progress.emit, cancel=self.cancel)
        else:
            rc = self._fn(self._args)
        self.finished_with.emit(rc)

    def stop(self):
        self.cancel.set()
