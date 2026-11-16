"""失败登记模型：工作区 `.translate_failures_state.json`。"""

from typing import Any, Literal

from pydantic import BaseModel


class FailureRecord(BaseModel):
    """单个失败任务（翻译失败或 TTS 失败）。"""

    char_id: str
    story: str
    key: str
    stage: Literal["translate", "tts"]
    error: str = ""
    attempts: int = 1
    first_at: str
    last_at: str


class FailureRegistry(BaseModel):
    """一次运行留下的未解决失败清单（不跨运行自动重试）。"""

    signature: dict[str, Any]
    result_tag: str | None = None
    started_at: str
    updated_at: str | None = None
    rounds: int = 0
    failures: list[FailureRecord] = []
