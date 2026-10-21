# -*- coding: utf-8 -*-
"""统一退出码：CLI / GUI / MCP 子进程共用，避免各处魔法数字漂移。"""
from __future__ import annotations

from enum import IntEnum


class ExitCode(IntEnum):
    OK = 0
    USAGE = 1                 # 参数/前置条件错误
    UNSUPPORTED_LANGUAGE = 2  # 语言不受支持
    SIGNATURE = 3             # 批量签名与上次不一致
    PAUSED = 4                # 业务错误重试耗尽
    CONNECTION = 5            # 所有后端连接失败
    CANCELLED = 6             # 用户取消，进度已保存
    PARTIAL = 7               # 部分任务失败，失败清单已保存
    QUOTA = 8                 # 当日配额耗尽


LABELS = {
    -1: "异常（见日志）",
    ExitCode.OK: "完成",
    ExitCode.USAGE: "参数或前置条件错误",
    ExitCode.UNSUPPORTED_LANGUAGE: "语言不受支持",
    ExitCode.SIGNATURE: "批量状态与上次不一致",
    ExitCode.PAUSED: "已暂停（限流或业务错误）",
    ExitCode.CONNECTION: "后端连接失败",
    ExitCode.CANCELLED: "已取消（进度已保存）",
    ExitCode.PARTIAL: "部分任务失败（失败清单已保存）",
    ExitCode.QUOTA: "今日配额已耗尽（进度已保存）",
}


def label(rc: int) -> str:
    """退出码 → 人类可读文案。"""
    return LABELS.get(rc, "未知")
