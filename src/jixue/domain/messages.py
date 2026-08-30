"""对话内部使用的消息与用量模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal
from uuid import uuid4

Role = Literal["user", "assistant"]


class MessageStatus(StrEnum):
    """消息在内部生命周期中的状态。"""

    STREAMING = "streaming"
    COMPLETE = "complete"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class Usage:
    """一次调用或一次会话的 Token 用量。"""

    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True, slots=True)
class Message:
    """包含 UI 与持久化元数据的内部消息。"""

    role: Role
    content: str
    status: MessageStatus = MessageStatus.COMPLETE
    id: str = field(default_factory=lambda: f"msg_{uuid4().hex}")
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    usage: Usage = field(default_factory=Usage)

