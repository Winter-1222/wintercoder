"""供应商无关的 LLM 接口与流事件。"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from jixue.domain.conversation import APIMessage, Usage


class LLMClientError(RuntimeError):
    """适配器对外只暴露安全错误码、消息和重试标记。"""

    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class LLMEventType(StrEnum):
    TEXT = "text"
    USAGE = "usage"
    COMPLETE = "complete"


@dataclass(frozen=True, slots=True)
class LLMStreamEvent:
    type: LLMEventType
    text: str = ""
    usage: Usage = field(default_factory=Usage)
    stop_reason: str | None = None


class LLMClient(Protocol):
    """FakeLLM 和真实适配器共同遵守的最小合同。"""

    @property
    def model_name(self) -> str: ...

    def stream(self, messages: Sequence[APIMessage]) -> AsyncIterator[LLMStreamEvent]: ...
