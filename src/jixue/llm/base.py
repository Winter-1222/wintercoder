"""LLM 客户端的领域接口。

上层只认识本文件的 Protocol 和事件，不认识 Anthropic SDK。FakeLLM 与未来真实适配器
都实现相同接口，因此更换供应商时 BridgeApplication 不需要改变。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from jixue.domain.messages import Usage


class LLMClientError(RuntimeError):
    """所有具体模型适配器向上层报告错误时使用的领域异常。

    Bridge 只能看到稳定的 `code/message/retryable`，不能看到 Anthropic 的异常类。
    `message` 必须适合直接展示给用户，不能包含 API Key、完整请求或响应正文。
    """

    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        """保存机器可判断的错误码、人类可读消息和是否建议重试。"""

        super().__init__(message)
        self.code = code
        self.retryable = retryable


class LLMEventType(StrEnum):
    """第一章需要的最小 LLM 流事件。"""

    # 一小段新增文本，可以连续出现很多次。
    TEXT = "text"
    # 本轮输入/输出 Token 统计。
    USAGE = "usage"
    # 本轮模型调用已经结束，携带停止原因。
    COMPLETE = "complete"


@dataclass(frozen=True, slots=True)
class LLMStreamEvent:
    """供应商事件转换后的统一领域事件；未使用的字段保持默认值。"""

    # type 决定消费方应该读取 text、usage 还是 stop_reason。
    type: LLMEventType
    # 只有 TEXT 事件使用；其他类型保持空字符串。
    text: str = ""
    # USAGE/COMPLETE 可以携带用量，default_factory 避免共享同一个实例。
    usage: Usage = field(default_factory=Usage)
    # COMPLETE 使用，例如 end_turn；生成过程中为 None。
    stop_reason: str | None = None


class LLMClient(Protocol):
    """上层唯一允许依赖的 LLM 客户端接口，类似一份实现者必须遵守的合同。"""

    @property
    def model_name(self) -> str:
        """返回用于 UI 展示的模型名。"""

    def stream(self, prompt: str) -> AsyncIterator[LLMStreamEvent]:
        """接收当前提示词，按产生顺序异步返回零到多个领域事件。"""
