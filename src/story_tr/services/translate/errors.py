"""批量翻译的控制流异常：把致命错误 / 取消转成退出码。"""

from __future__ import annotations

from story_tr.errors import ExitCode


class Abort(Exception):
    """携带退出码的控制流异常（不面向最终用户展示 traceback）。"""

    def __init__(self, code: ExitCode, reason: str = "") -> None:
        super().__init__(reason)
        self.code = code
        self.reason = reason
