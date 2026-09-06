"""Agent 事件合同与事件构造，供循环、模型和工具组件共同使用。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from time import perf_counter

from jixue.domain.conversation import Usage
from jixue.llm.base import LLMStreamEvent


class AgentMode(StrEnum):
    """Agent 的工作方式：Do 可以执行全部工具，Plan 只能调查。"""

    DO = "do"
    PLAN = "plan"


class AgentEventType(StrEnum):
    """Agent 对外发出的事件种类；UI 只消费事件，不需要知道内部步骤。"""

    STREAM_TEXT = "stream_text"
    TOOL_USE = "tool_use"
    PERMISSION_REQUEST = "permission_request"
    TOOL_RESULT = "tool_result"
    USAGE = "usage"
    TURN_COMPLETE = "turn_complete"
    LOOP_COMPLETE = "loop_complete"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class AgentEvent:
    """Agent 事件只描述发生了什么，不包含 Electron 的请求编号和序号。"""

    type: AgentEventType
    payload: Mapping[str, object] = field(default_factory=dict)


def event(event_type: AgentEventType, **payload: object) -> AgentEvent:
    return AgentEvent(event_type, payload)


def error_event(code: str, message: str, *, retryable: bool = False) -> AgentEvent:
    """所有请求错误都使用同一份结构，前端收到后会结束等待状态。"""

    return event(
        AgentEventType.ERROR,
        code=code,
        message=message,
        retryable=retryable,
        scope="request",
    )


def cancelled_tool_event(call: LLMStreamEvent) -> AgentEvent:
    """关闭尚未执行的工具卡片，避免取消后一直显示“执行中”。"""

    return event(
        AgentEventType.TOOL_RESULT,
        id=call.tool_use_id,
        name=call.tool_name,
        content="用户已停止任务，工具已中断或不再等待结果",
        is_error=True,
        duration_ms=0,
        metadata={},
    )


def usage_payload(usage: Usage) -> dict[str, int]:
    return {
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
    }


def add_usage(left: Usage, right: Usage) -> Usage:
    return Usage(
        left.input_tokens + right.input_tokens,
        left.output_tokens + right.output_tokens,
    )


def subtract_usage(current: Usage, previous: Usage) -> Usage:
    """计算后台请求新增的用量；累计值理论上只增不减。"""

    return Usage(
        max(0, current.input_tokens - previous.input_tokens),
        max(0, current.output_tokens - previous.output_tokens),
    )


def loop_complete(
    started_at: float,
    message_id: str,
    iterations: int,
    stop_reason: str,
    *,
    model: str,
    mode: AgentMode,
    turn_index: int,
    is_error: bool = False,
    cancelled: bool = False,
) -> AgentEvent:
    """统一任务结束事件，普通任务和压缩命令都能让 UI 正常解锁。"""

    return event(
        AgentEventType.LOOP_COMPLETE,
        turn_index=turn_index,
        iterations=iterations,
        stop_reason=stop_reason,
        duration_ms=round((perf_counter() - started_at) * 1000),
        model=model,
        message_id=message_id,
        is_error=is_error,
        cancelled=cancelled,
        mode=mode.value,
    )
