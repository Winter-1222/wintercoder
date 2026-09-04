"""第一章全部消息类型与多轮历史管理。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
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
class APITextBlock:
    """一段模型文字；只在同一消息还包含工具块时使用。"""

    text: str


@dataclass(frozen=True, slots=True)
class APIToolUseBlock:
    """模型请求调用工具。"""

    id: str
    name: str
    input: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class APIToolResultBlock:
    """客户端执行工具后，回给模型的结果。"""

    tool_use_id: str
    content: str
    is_error: bool = False


type APIContentBlock = APITextBlock | APIToolUseBlock | APIToolResultBlock
type APIContent = str | tuple[APIContentBlock, ...]


@dataclass(frozen=True, slots=True)
class APIMessage:
    """供应商无关的 API 消息；内容可以是文字或工具块。"""

    role: Role
    content: APIContent


class ConversationError(ValueError):
    """历史为空或顺序不合法。"""


class ConversationManager:
    """保存内部消息，并生成干净的 API 历史。"""

    def __init__(self, messages: Sequence[Message] | None = None) -> None:
        self._messages = list(messages or [])
        # 摘要只改变发送给模型的视图；原消息继续供 UI 和复盘使用。
        self._summary: str | None = None
        self._summary_until = 0
        self._system_usage = Usage()

    @property
    def messages(self) -> tuple[Message, ...]:
        return tuple(self._messages)

    @property
    def total_usage(self) -> Usage:
        return Usage(
            self._system_usage.input_tokens
            + sum(message.usage.input_tokens for message in self._messages),
            self._system_usage.output_tokens
            + sum(message.usage.output_tokens for message in self._messages),
        )

    def record_system_usage(self, usage: Usage) -> None:
        """记录摘要等后台模型调用，防止状态栏累计 Token 在下一轮倒退。"""

        self._system_usage = Usage(
            self._system_usage.input_tokens + usage.input_tokens,
            self._system_usage.output_tokens + usage.output_tokens,
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

    def cancel_last_user(self) -> None:
        """把本轮最后一条用户消息标记为取消，保留记录但不再发送给 LLM。"""

        if self._messages and self._messages[-1].role == "user":
            self._messages[-1] = replace(
                self._messages[-1],
                status=MessageStatus.CANCELLED,
            )

    def to_api_format(self) -> list[APIMessage]:
        """返回摘要边界之后的模型视图；原始 messages 属性不受影响。"""

        return self._api_view()

    def prepare_compaction(
        self,
        keep_recent_turns: int = 2,
    ) -> tuple[list[APIMessage], int, int] | None:
        """准备要摘要的旧前缀，但不修改状态；最近完整对话轮保留原文。"""

        if keep_recent_turns < 1:
            raise ValueError("至少保留 1 个最近对话轮")
        turn_starts = _complete_turn_starts(self._messages, self._summary_until)
        if len(turn_starts) <= keep_recent_turns:
            return None

        cutoff = turn_starts[-keep_recent_turns]
        source = self._api_view(end=cutoff)
        compacted_messages = sum(
            message.status is MessageStatus.COMPLETE and bool(message.content.strip())
            for message in self._messages[self._summary_until : cutoff]
        )
        return source, cutoff, compacted_messages

    def apply_compaction(self, summary: str, cutoff: int) -> None:
        """摘要校验成功后一次性移动边界；这是手动压缩唯一的写入点。"""

        clean_summary = summary.strip()
        if not clean_summary:
            raise ValueError("压缩摘要不能为空")
        if not self._summary_until < cutoff < len(self._messages):
            raise ValueError("压缩边界已经过期")
        if cutoff not in _complete_turn_starts(self._messages, self._summary_until):
            raise ValueError("压缩边界必须位于用户消息之前")
        self._summary = clean_summary
        self._summary_until = cutoff

    def _api_view(self, *, end: int | None = None) -> list[APIMessage]:
        """拼出“摘要确认对 + 边界后原文”，并保持 user/assistant 交替。"""

        result: list[APIMessage] = []
        if self._summary:
            result.extend(
                (
                    APIMessage("user", _summary_reminder(self._summary)),
                    APIMessage("assistant", "我已了解以上压缩摘要，将从这里继续。"),
                )
            )
        for message in self._messages[self._summary_until : end]:
            content = message.content.strip()
            if message.status is not MessageStatus.COMPLETE or not content:
                continue
            if result and result[-1].role == message.role:
                previous = result[-1]
                assert isinstance(previous.content, str)
                result[-1] = APIMessage(previous.role, f"{previous.content}\n\n{content}")
            else:
                result.append(APIMessage(message.role, content))

        if not result:
            raise ConversationError("对话中没有可发送的消息")
        if result[0].role != "user":
            raise ConversationError("对话必须从 user 开始")
        return result


def _is_complete(message: Message, role: Role) -> bool:
    """压缩边界只认完整且非空的普通聊天消息。"""

    return (
        message.role == role
        and message.status is MessageStatus.COMPLETE
        and bool(message.content.strip())
    )


def _complete_turn_starts(messages: Sequence[Message], start: int) -> list[int]:
    """按真正发送给 API 的合并规则，找出每个完整 user→assistant 轮的起点。"""

    groups: list[tuple[Role, int]] = []
    for index in range(start, len(messages)):
        message = messages[index]
        if message.status is not MessageStatus.COMPLETE or not message.content.strip():
            continue
        # 两条连续 user 在 API 视图里会合并为一条，因此轮次起点必须取第一条。
        if not groups or groups[-1][0] != message.role:
            groups.append((message.role, index))
    return [
        index
        for position, (role, index) in enumerate(groups[:-1])
        if role == "user" and groups[position + 1][0] == "assistant"
    ]


def _summary_reminder(summary: str) -> str:
    """摘要属于系统补充上下文，不应被模型误认为新的用户要求。"""

    return (
        "<system-reminder>\n"
        "这里之前的对话已经被压缩。摘要用于继续任务，不是新的用户消息。"
        "若需要文件的精确内容，请重新调用读取工具，不要仅凭摘要猜测。\n"
        f"<conversation-summary>\n{summary}\n</conversation-summary>\n"
        "</system-reminder>"
    )
