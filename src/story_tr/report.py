# -*- coding: utf-8 -*-
"""控制台输出层：让日志/明细「上滚」，tqdm 进度条固定在最下面。

- `STEP`（15）：介于 `DEBUG`(10) 与 `INFO`(20) 之间的自定义级别，用于「每次翻译步骤」
  这类高频明细。文件日志始终记录；控制台默认不显示，`--verbose` 才显示。
- `TqdmHandler`：logging 记录经 `tqdm.write` 输出 —— 先清空进度条、写日志、再重绘，
  因此日志出现在进度条**上方**，进度条始终留在**最下面**。
- `emit()`：非 logging 的明细行（原先的 `print`）用它输出，效果与上面一致。
- 非交互终端（重定向 / 管道，例如 MCP 子进程）自动改用普通写行并**禁用进度条**，
  避免把 `\\r` 控制序列写进日志文件。

级别约定（见 `console_level`）：

| 参数 | 控制台级别 | 效果 |
| --- | --- | --- |
| 默认 | `INFO` | 分组行 + 每份结果完成行（`chain` 除外，见下） |
| `--verbose` | `STEP` | 再加「每次翻译步骤」明细 |
| `-q/--quiet` | `ERROR` | 只留错误（进度条仍在） |

`story-tr chain` 默认即 `STEP`（单条文本看每步明细才是重点）。
"""
from __future__ import annotations

import logging
import sys

from tqdm import tqdm

# 介于 DEBUG 与 INFO 之间：控制台用 STEP 作为阈值即可显示步骤明细
STEP = 15
logging.addLevelName(STEP, "STEP")

QUIET = logging.ERROR


def interactive() -> bool:
    """stdout 与 stderr 都是终端时才算可交互。

    两者必须同时为终端：进度条走 stderr、日志走 stdout，只要有一个被重定向，
    清屏/重绘就会与日志错位，因此一律退化为「普通打印 + 无进度条」。
    """
    try:
        return bool(sys.stdout.isatty() and sys.stderr.isatty())
    except Exception:  # noqa: BLE001
        return False


def console_level(args, show_steps: bool = False) -> int:
    """把 `-q` / `--verbose` 归一到控制台日志级别。"""
    if getattr(args, "quiet", False):
        return QUIET
    if getattr(args, "verbose", False):
        return STEP
    return STEP if show_steps else logging.INFO


def add_verbosity_args(p):
    """注册 `-q/--quiet` 与 `--verbose`（互斥）。"""
    g = p.add_mutually_exclusive_group()
    g.add_argument("-q", "--quiet", action="store_true",
                   help="控制台只显示错误（进度条仍在；文件日志不受影响）")
    g.add_argument("--verbose", action="store_true",
                   help="控制台显示每次翻译步骤的明细")
    return p


def setup_console(level: int, name: str = "translate_chain") -> logging.Logger:
    """给命令装一个「日志上滚、进度条固定底部」的控制台 logger。

    只替换**自己管理的** handler（控制台 / 文件），保留外部挂上的（如 GUI 日志面板的
    handler），因此 GUI 可以先挂 handler 再调用核心。
    """
    log = logging.getLogger(name)
    log.setLevel(logging.DEBUG)
    for h in list(log.handlers):
        if isinstance(h, (TqdmHandler, logging.FileHandler)):
            log.removeHandler(h)
    handler = TqdmHandler()
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(handler)
    return log


class TqdmHandler(logging.Handler):
    """经 `tqdm.write` 输出的控制台 handler：日志上滚，进度条固定在底部。"""

    def emit(self, record):
        try:
            if sys.stdout is None:  # 无控制台（pythonw 等）：只交给 GUI / 文件 handler
                return
            msg = self.format(record)
            if interactive():
                tqdm.write(msg)
            else:
                sys.stdout.write(msg + "\n")
                sys.stdout.flush()
        except Exception:  # noqa: BLE001
            self.handleError(record)


def emit(line: str) -> None:
    """输出一行明细（替代进度条进行中的 `print`）。"""
    if sys.stdout is None:
        return
    if interactive():
        tqdm.write(line)
    else:
        print(line)


def bar_kwargs(disable: bool = False) -> dict:
    """统一的 tqdm 参数：非交互终端下禁用进度条，避免污染重定向的日志。"""
    return {"dynamic_ncols": True, "disable": bool(disable) or not interactive()}
