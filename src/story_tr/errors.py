"""统一退出码与错误层级（替代旧 `exitcodes.py` 与 `chain.py` 的异常类）。

所有翻译后端异常继承 `TranslationError`；控制流取消用 `Cancelled`。
错误层级约定：
    - `Cancelled`：调用方主动取消（控制流，非翻译错误）
    - `RateLimited` / `QuotaExceeded`：限流 / 配额，退避或保存进度后退出
    - `ConnectionFailed`：连接 / 超时 / DNS / 5xx（刻意避开内置 `ConnectionError`）
    - `UnsupportedLanguage` / `BadOutput` / `ModelNotLoaded`：业务类失败
"""
from enum import IntEnum


class ExitCode(IntEnum):
    OK = 0                   # 完成
    USAGE = 1                # 参数/前置条件错误
    UNSUPPORTED_LANGUAGE = 2  # 语言不受支持
    SIGNATURE = 3            # 批量签名与上次不一致
    PAUSED = 4               # 业务错误重试耗尽（含本地模型未加载）
    CONNECTION = 5           # 所有后端连接失败
    CANCELLED = 6            # 用户取消，进度已保存
    PARTIAL = 7              # 部分任务失败，失败清单已保存
    QUOTA = 8                # 当日配额耗尽


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


class StoryTrError(Exception):
    """所有 story-tr 异常的基类。"""


class Cancelled(StoryTrError):
    """被调用方主动取消（控制流）：保存进度后以退出码 6 结束。"""


class TranslationError(StoryTrError):
    """翻译后端异常的基类。"""


class RateLimited(TranslationError):
    """被限流（HTTP 429 / TooManyRequests）：退避等待后重试同一后端。"""


class QuotaExceeded(TranslationError):
    """当日配额耗尽：重试无意义，保存进度后以退出码 8 结束。"""


class ConnectionFailed(TranslationError):
    """连接 / 超时 / DNS / 5xx 类失败：立即失败，可切换后端。"""


class UnsupportedLanguage(TranslationError):
    """后端不支持该语言：直接终止（重试无意义）。"""


class BadOutput(TranslationError):
    """后端返回空 / 结构异常 / 清洗后为空等业务失败。"""


class ModelNotLoaded(TranslationError):
    """本地 RWKV 模型未加载（服务可达）：批量应立即中止。"""
