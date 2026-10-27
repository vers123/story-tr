"""状态文件模型：批量任务与单条链路任务的续跑状态。"""
from typing import Any

from pydantic import BaseModel


class BatchState(BaseModel):
    """批量翻译任务的续跑状态。"""

    signature: dict[str, Any]
    completed: list[str] = []
    started_at: str
    updated_at: str | None = None


class ChainState(BaseModel):
    """单文本「来回翻译 N 次」链路的续跑状态。"""

    signature: dict[str, Any]
    source: str
    detected: str
    languages: list[str]
    text: str
    next_index: int
    steps: list[dict[str, Any]]
    started_at: str
    updated_at: str | None = None
