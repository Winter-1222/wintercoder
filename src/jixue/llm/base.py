"""LLM 客户端的领域接口。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from jixue.domain.messages import Usage


class LLMEventType(StrEnum):
    """第一章需要的最小 LLM 流事件。"""

    TEXT = "text"
    USAGE = "usage"
    COMPLETE = "complete"


@dataclass(frozen=True, slots=True)
class LLMStreamEvent:
    """供应商事件转换后的领域事件。"""

    type: LLMEventType
    text: str = ""
    usage: Usage = field(default_factory=Usage)
    stop_reason: str | None = None


class LLMClient(Protocol):
    """上层唯一允许依赖的 LLM 客户端接口。"""

    @property
    def model_name(self) -> str:
        """返回用于 UI 展示的模型名。"""

    def stream(self, prompt: str) -> AsyncIterator[LLMStreamEvent]:
        """按领域事件流式生成回复。"""
