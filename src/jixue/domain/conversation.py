"""第一章全部消息类型与多轮历史管理。"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal
from uuid import uuid4

type Role = Literal["user", "assistant"]


class MessageStatus(StrEnum):
    """内部消息状态；只有 complete 会发送给 LLM。"""

    STREAMING = "streaming"
    COMPLETE = "complete"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class Usage:
    """输入、输出 Token。"""

    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True, slots=True)
class Message:
    """UI 使用的完整消息。"""

    role: Role
    content: str
    status: MessageStatus = MessageStatus.COMPLETE
    id: str = field(default_factory=lambda: f"msg_{uuid4().hex}")
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    usage: Usage = field(default_factory=Usage)


@dataclass(frozen=True, slots=True)
class APIMessage:
    """LLM API 只需要 role 和 content。"""

    role: Role
    content: str


class ConversationError(ValueError):
    """历史为空或顺序不合法。"""


class ConversationManager:
    """保存内部消息，并生成干净的 API 历史。"""

    def __init__(self, messages: Sequence[Message] | None = None) -> None:
        self._messages = list(messages or [])

    @property
    def messages(self) -> tuple[Message, ...]:
        return tuple(self._messages)

    @property
    def total_usage(self) -> Usage:
        return Usage(
            sum(message.usage.input_tokens for message in self._messages),
            sum(message.usage.output_tokens for message in self._messages),
        )

    def add_user(self, content: str) -> None:
        self._messages.append(Message(role="user", content=content))

    def add_assistant(
        self,
        content: str,
        *,
        status: MessageStatus = MessageStatus.COMPLETE,
        usage: Usage | None = None,
    ) -> None:
        self._messages.append(
            Message(
                role="assistant",
                content=content,
                status=status,
                usage=usage or Usage(),
            )
        )

    def to_api_format(self) -> list[APIMessage]:
        """过滤未完成消息，并合并相邻的相同角色。"""

        result: list[APIMessage] = []
        for message in self._messages:
            content = message.content.strip()
            if message.status is not MessageStatus.COMPLETE or not content:
                continue
            if result and result[-1].role == message.role:
                previous = result[-1]
                result[-1] = APIMessage(previous.role, f"{previous.content}\n\n{content}")
            else:
                result.append(APIMessage(message.role, content))

        if not result:
            raise ConversationError("对话中没有可发送的消息")
        if result[0].role != "user":
            raise ConversationError("对话必须从 user 开始")
        return result
