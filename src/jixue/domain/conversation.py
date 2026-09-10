"""统一的会话消息：文字、工具轮和摘要使用同一条序列。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal
from uuid import uuid4

type Role = Literal["user", "assistant"]


class MessageStatus(StrEnum):
    """消息结束状态；流式草稿不发送，中断内容附上状态说明后供后续续接。"""

    STREAMING = "streaming"
    COMPLETE = "complete"
    INCOMPLETE = "incomplete"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class Usage:
    """输入、输出 Token。"""

    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True, slots=True)
class Message:
    """会话中的一条消息；UI 通过事件流独立维护显示记录。"""

    role: Role
    content: APIContent
    status: MessageStatus = MessageStatus.COMPLETE
    id: str = field(default_factory=lambda: f"msg_{uuid4().hex}")
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    usage: Usage = field(default_factory=Usage)
    is_summary: bool = False


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
    """只保存一份工作消息；清理和摘要直接更新它，不维护平行历史。"""

    def __init__(
        self,
        messages: Sequence[Message] | None = None,
        *,
        total_usage: Usage | None = None,
        completed_turns: int | None = None,
    ) -> None:
        self._messages = list(messages or [])
        self._usage = total_usage or Usage(
            sum(message.usage.input_tokens for message in self._messages),
            sum(message.usage.output_tokens for message in self._messages),
        )
        self.completed_turns = (
            completed_turns
            if completed_turns is not None
            else len(_complete_turn_starts(self._messages))
        )

    @property
    def messages(self) -> tuple[Message, ...]:
        return tuple(self._messages)

    @property
    def total_usage(self) -> Usage:
        return self._usage

    def record_usage(self, usage: Usage) -> None:
        """账单独立于消息长度，删除旧消息后累计用量也不会倒退。"""

        self._usage = Usage(
            self._usage.input_tokens + usage.input_tokens,
            self._usage.output_tokens + usage.output_tokens,
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
        self._messages.append(Message("assistant", content, status, usage=usage or Usage()))
        self.record_usage(usage or Usage())
        # 累计已结束的用户轮；停止或失败也占一轮，不能复用界面轮号。
        if status is not MessageStatus.STREAMING:
            self.completed_turns += 1

    def add_tool_round(
        self,
        response: Sequence[APIContentBlock],
        results: Sequence[APIContentBlock],
    ) -> None:
        """工具调用与结果一起写入，避免下一次请求出现孤立的工具块。"""

        uses = [block.id for block in response if isinstance(block, APIToolUseBlock)]
        result_ids = [
            block.tool_use_id for block in results if isinstance(block, APIToolResultBlock)
        ]
        if not uses or uses != result_ids or len(result_ids) != len(results):
            raise ConversationError("工具调用与结果必须按 ID 完整配对")
        self._messages.extend(
            (
                Message("assistant", tuple(response)),
                Message("user", tuple(results)),
            )
        )

    def clear_tool_results(self, contents: Mapping[str, str]) -> None:
        """只替换工具结果正文，调用 ID、错误标记和消息位置保持不变。"""

        for index, message in enumerate(self._messages):
            if not isinstance(message.content, tuple):
                continue
            blocks = tuple(
                replace(block, content=contents[block.tool_use_id])
                if isinstance(block, APIToolResultBlock) and block.tool_use_id in contents
                else block
                for block in message.content
            )
            if blocks != message.content:
                self._messages[index] = replace(message, content=blocks)

    def to_api_format(self, *, merge_text: bool = True) -> list[APIMessage]:
        """转换协议并标明中断状态，不删除已经发生的用户输入和工具事实。"""

        return _to_api_messages(self._messages, merge_text=merge_text)

    def prepare_compaction(
        self,
        keep_recent_turns: int = 2,
    ) -> tuple[list[APIMessage], int, int] | None:
        """摘要只覆盖已结束的旧用户轮；工具调用和结果不能被边界拆开。"""

        if keep_recent_turns < 1:
            raise ValueError("至少保留 1 个最近对话轮")
        starts = _complete_turn_starts(self._messages)
        if len(starts) <= keep_recent_turns:
            return None
        cutoff = starts[-keep_recent_turns]
        source = _to_api_messages(self._messages[:cutoff])
        return source, cutoff, len(source)

    def apply_compaction(self, summary: str, cutoff: int) -> None:
        """完整摘要通过校验后一次替换旧前缀；失败前不修改消息。"""

        if not summary.strip():
            raise ValueError("压缩摘要不能为空")
        if not 0 < cutoff < len(self._messages):
            raise ValueError("压缩边界已经过期")
        if cutoff not in _complete_turn_starts(self._messages):
            raise ValueError("压缩边界必须位于用户消息之前")
        replacement = [
            Message("user", _summary_reminder(summary.strip()), is_summary=True),
            Message("assistant", "我已了解以上压缩摘要，将从这里继续。", is_summary=True),
        ]
        # 标签和确认消息也占空间；只比较摘要正文会放过“越压越长”的替换。
        if message_characters(_to_api_messages(replacement)) >= message_characters(
            _to_api_messages(self._messages[:cutoff])
        ):
            raise ValueError("摘要没有比原历史更短，本次压缩未应用")
        self._messages[:cutoff] = replacement


def _to_api_messages(messages: Sequence[Message], *, merge_text: bool = True) -> list[APIMessage]:
    """相邻同角色文字可合并，工具消息保持原来的结构。"""

    result: list[APIMessage] = []
    for message in messages:
        if message.status is MessageStatus.STREAMING:
            continue
        content = message.content.strip() if isinstance(message.content, str) else message.content
        if message.role == "assistant" and isinstance(content, str):
            content = _with_interruption_notice(content, message.status)
        if not content:
            continue
        if (
            merge_text
            and result
            and result[-1].role == message.role
            and isinstance(result[-1].content, str)
            and isinstance(content, str)
        ):
            result[-1] = APIMessage(message.role, f"{result[-1].content}\n\n{content}")
        else:
            result.append(APIMessage(message.role, content))
    if not result:
        raise ConversationError("对话中没有可发送的消息")
    if result[0].role != "user":
        raise ConversationError("对话必须从 user 开始")
    return result


def _complete_turn_starts(messages: Sequence[Message]) -> list[int]:
    """普通 user 开始一轮，终态 assistant 结束一轮；停止不等于任务成功。"""

    starts: list[int] = []
    start: int | None = None
    for index, message in enumerate(messages):
        if (
            message.is_summary
            or message.status is MessageStatus.STREAMING
            or not isinstance(message.content, str)
        ):
            continue
        if message.role == "user":
            if start is None and message.content.strip():
                start = index
        elif start is not None and (
            message.content.strip() or message.status is not MessageStatus.COMPLETE
        ):
            starts.append(start)
            start = None
    return starts


def _with_interruption_notice(content: str, status: MessageStatus) -> str:
    """保留部分输出，但明确它不是完整答复；说明只在协议转换时生成。"""

    reasons = {
        MessageStatus.CANCELLED: "用户已停止本次任务",
        MessageStatus.INCOMPLETE: "本次响应未完整结束",
        MessageStatus.FAILED: "本次响应因错误中断",
    }
    reason = reasons.get(status)
    if reason is None:
        return content
    notice = (
        f"<system-reminder>\n{reason}，不能据此认定任务已完成。"
        "已记录的工具结果仍然有效；对结果未知的操作先核对实际状态，"
        "不要盲目重复已经成功的操作。根据用户下一条消息继续或调整任务。\n"
        "</system-reminder>"
    )
    return f"{content}\n\n{notice}" if content else notice


def _summary_reminder(summary: str) -> str:
    """摘要属于系统补充上下文，不应被模型误认为新的用户要求。"""

    return (
        "<system-reminder>\n"
        "这里之前的对话已经被压缩。摘要用于继续任务，不是新的用户消息。"
        "若需要文件的精确内容，请重新调用读取工具，不要仅凭摘要猜测。\n"
        f"<conversation-summary>\n{summary}\n</conversation-summary>\n"
        "</system-reminder>"
    )


def message_characters(messages: Sequence[APIMessage]) -> int:
    """估算即将发送的消息大小，包含文本、工具参数和工具结果。"""

    total = 0
    for message in messages:
        total += len(message.role)
        if isinstance(message.content, str):
            total += len(message.content)
            continue
        for block in message.content:
            if isinstance(block, APITextBlock):
                total += len(block.text)
            elif isinstance(block, APIToolUseBlock):
                total += len(block.id) + len(block.name) + len(str(block.input))
            elif isinstance(block, APIToolResultBlock):
                total += len(block.tool_use_id) + len(block.content)
    return total
