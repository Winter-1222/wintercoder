"""对话内部使用的消息与用量模型。

这些类型属于霁雪自己的领域层，不对应某一家 LLM SDK。将来调用 Anthropic API 时，
适配器会把 Message 转成供应商格式；UI、会话管理和持久化仍使用这里的类型。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal
from uuid import uuid4

# Literal 把字符串范围限制为两种角色，写错成 "ai" 时类型检查会提前报错。
Role = Literal["user", "assistant"]


class MessageStatus(StrEnum):
    """消息在内部生命周期中的状态，而不是 API 的 stop_reason。"""

    # assistant 仍在接收文本增量，不能作为完整历史发送给下一轮模型。
    STREAMING = "streaming"
    # 内容已经完整，可以持久化并参与后续对话。
    COMPLETE = "complete"
    # 模型或网络失败；可能保留一部分仅供 UI 展示的文本。
    FAILED = "failed"
    # 用户主动取消本轮；后续会由 Agent Loop 使用。
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class Usage:
    """一次调用或一次会话的 Token 用量；默认 0 便于逐步累加。"""

    # 发送给模型的输入 Token 数。
    input_tokens: int = 0
    # 模型生成的输出 Token 数。
    output_tokens: int = 0


@dataclass(frozen=True, slots=True)
class Message:
    """包含 UI、会话管理和未来持久化元数据的内部消息。"""

    # 消息说话方：user 或 assistant。
    role: Role
    # 当前文本正文；第二章开始会扩展为多种内容块。
    content: str
    # user 消息通常立即 complete，assistant 流式生成时先为 streaming。
    status: MessageStatus = MessageStatus.COMPLETE
    # 默认生成全局足够唯一的 ID，避免使用数组下标标识消息。
    id: str = field(default_factory=lambda: f"msg_{uuid4().hex}")
    # 使用 UTC ISO 字符串，跨 Python、JavaScript 和 JSON 都容易传输。
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    # 消息级用量；没有用量信息时保持默认 0。
    usage: Usage = field(default_factory=Usage)
