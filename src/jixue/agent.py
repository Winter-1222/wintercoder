"""霁雪真正的 Agent 核心：对话历史 → LLM → 工具 → 事件流。"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from jixue.domain.conversation import (
    APIContentBlock,
    APIMessage,
    APITextBlock,
    APIToolResultBlock,
    APIToolUseBlock,
    ConversationError,
    ConversationManager,
    Message,
    MessageStatus,
    Usage,
)
from jixue.llm.base import LLMClient, LLMClientError, LLMEventType, LLMStreamEvent
from jixue.tools import ToolContext, ToolRegistry, ToolResult


class AgentEventType(StrEnum):
    """Agent 对外发出的事件种类；UI 只消费事件，不需要知道内部步骤。"""

    STREAM_TEXT = "stream_text"
    TOOL_USE = "tool_use"
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


class Agent:
    """协调对话、模型和工具，并让模型持续工作到任务结束。"""

    def __init__(
        self,
        llm: LLMClient,
        conversation: ConversationManager | None = None,
        tools: ToolRegistry | None = None,
        tool_context: ToolContext | None = None,
        max_iterations: int = 50,
    ) -> None:
        if max_iterations < 1:
            raise ValueError("最大循环轮数必须大于 0")
        self._llm = llm
        self._conversation = conversation or ConversationManager()
        self._tools = tools or ToolRegistry()
        self._tool_context = tool_context or ToolContext(Path.cwd().resolve())
        self._max_iterations = max_iterations

    @property
    def model_name(self) -> str:
        return self._llm.model_name

    @property
    def messages(self) -> tuple[Message, ...]:
        return self._conversation.messages

    async def run(self, user_text: str) -> AsyncIterator[AgentEvent]:
        """处理一条用户消息，并在过程发生时立即向外产生事件。"""

        if not user_text.strip():
            yield _event(
                AgentEventType.ERROR,
                code="invalid_input",
                message="消息文本不能为空",
                retryable=False,
                scope="request",
            )
            return

        started_at = perf_counter()
        message_id = f"msg_{uuid4().hex}"
        chunks: list[str] = []
        turn_usage = Usage()
        self._conversation.add_user(user_text.strip())

        try:
            history = self._conversation.to_api_format()
        except ConversationError as error:
            yield _event(
                AgentEventType.ERROR,
                code="conversation_invalid",
                message=str(error),
                retryable=False,
                scope="request",
            )
            return

        try:
            tool_definitions = self._tools.to_api_format()
            stop_reason = "end_turn"
            iterations = 0
            reached_limit = False

            # 一轮就是一次 LLM 请求。模型需要工具时，执行后把结果送回下一轮；
            # 模型不再请求工具时，说明它已经给出最终答复，循环自然结束。
            for iteration in range(1, self._max_iterations + 1):
                iterations = iteration
                response_blocks: list[APIContentBlock] = []
                tool_calls: list[LLMStreamEvent] = []

                async for llm_event in self._llm.stream(history, tool_definitions):
                    if llm_event.type == LLMEventType.TEXT:
                        chunks.append(llm_event.text)
                        _append_text(response_blocks, llm_event.text)
                        yield _event(
                            AgentEventType.STREAM_TEXT,
                            text=llm_event.text,
                            message_id=message_id,
                        )
                    elif llm_event.type == LLMEventType.TOOL_USE:
                        tool_calls.append(llm_event)
                        response_blocks.append(
                            APIToolUseBlock(
                                llm_event.tool_use_id,
                                llm_event.tool_name,
                                llm_event.tool_input,
                            )
                        )
                        payload: dict[str, object] = {
                            "id": llm_event.tool_use_id,
                            "name": llm_event.tool_name,
                            "input": dict(llm_event.tool_input),
                        }
                        if llm_event.tool_error:
                            payload["error"] = llm_event.tool_error
                        yield AgentEvent(AgentEventType.TOOL_USE, payload)
                    elif llm_event.type == LLMEventType.USAGE:
                        turn_usage = _add_usage(turn_usage, llm_event.usage)
                        cumulative = _add_usage(
                            self._conversation.total_usage,
                            turn_usage,
                        )
                        yield _event(
                            AgentEventType.USAGE,
                            turn=_usage(turn_usage),
                            cumulative=_usage(cumulative),
                        )
                    elif llm_event.type == LLMEventType.COMPLETE:
                        stop_reason = llm_event.stop_reason or "end_turn"

                # turn_complete 只表示“这一轮 LLM 请求结束”，不是整个任务结束。
                yield _event(
                    AgentEventType.TURN_COMPLETE,
                    iteration=iteration,
                    stop_reason=stop_reason,
                    tool_calls=len(tool_calls),
                )

                if not tool_calls:
                    break

                # 最后一轮仍请求工具时不再真的执行，避免任务无限运行。
                # 但仍给 UI 发错误结果，把“执行中”的工具卡片正常收尾。
                if iteration == self._max_iterations:
                    reached_limit = True
                    stop_reason = "max_iterations"
                    for call in tool_calls:
                        yield _event(
                            AgentEventType.TOOL_RESULT,
                            id=call.tool_use_id,
                            name=call.tool_name,
                            content="已达到 Agent 最大循环轮数，工具未执行",
                            is_error=True,
                            duration_ms=0,
                            metadata={},
                        )
                    break

                result_blocks: list[APIContentBlock] = []
                for call in tool_calls:
                    tool_started = perf_counter()
                    if call.tool_error:
                        result = ToolResult(call.tool_error, is_error=True)
                    else:
                        result = await self._tools.execute(
                            call.tool_name,
                            self._tool_context,
                            call.tool_input,
                        )

                    result_blocks.append(
                        APIToolResultBlock(
                            call.tool_use_id,
                            result.content,
                            result.is_error,
                        )
                    )
                    yield _event(
                        AgentEventType.TOOL_RESULT,
                        id=call.tool_use_id,
                        name=call.tool_name,
                        content=result.content,
                        is_error=result.is_error,
                        duration_ms=round((perf_counter() - tool_started) * 1000),
                        metadata=dict(result.metadata),
                    )

                # 工具结果必须紧跟模型的 tool_use，并使用相同的 tool_use_id。
                history = [
                    *history,
                    APIMessage("assistant", tuple(response_blocks)),
                    APIMessage("user", tuple(result_blocks)),
                ]

            if reached_limit:
                warning = (
                    f"\n\n> Agent 已执行 {self._max_iterations} 轮但仍未完成，"
                    "已自动停止。"
                )
                chunks.append(warning)
                yield _event(
                    AgentEventType.STREAM_TEXT,
                    text=warning,
                    message_id=message_id,
                )

            turn_index = 1 + sum(
                message.role == "assistant"
                and message.status is MessageStatus.COMPLETE
                for message in self._conversation.messages
            )
            answer = "".join(chunks)
            if answer:
                self._conversation.add_assistant(
                    answer,
                    usage=turn_usage,
                    status=(
                        MessageStatus.FAILED
                        if reached_limit
                        else MessageStatus.COMPLETE
                    ),
                )
            # loop_complete 才表示这一条用户任务彻底结束，UI 应在这里解锁输入框。
            yield _event(
                AgentEventType.LOOP_COMPLETE,
                turn_index=turn_index,
                iterations=iterations,
                stop_reason=stop_reason,
                duration_ms=round((perf_counter() - started_at) * 1000),
                model=self.model_name,
                message_id=message_id,
                is_error=reached_limit,
            )
        except LLMClientError as error:
            self._remember_failed(chunks)
            yield _event(
                AgentEventType.ERROR,
                code=error.code,
                message=str(error),
                retryable=error.retryable,
                scope="request",
            )
        except Exception:
            self._remember_failed(chunks)
            yield _event(
                AgentEventType.ERROR,
                code="llm_internal_error",
                message="模型客户端发生内部错误",
                retryable=False,
                scope="request",
            )

    def _remember_failed(self, chunks: list[str]) -> None:
        """保留已经显示的半截回复，但标记失败，下一轮不会发给 LLM。"""

        if chunks:
            self._conversation.add_assistant(
                "".join(chunks),
                status=MessageStatus.FAILED,
            )


def _event(event_type: AgentEventType, **payload: object) -> AgentEvent:
    return AgentEvent(event_type, payload)


def _append_text(blocks: list[APIContentBlock], text: str) -> None:
    """合并相邻文本，但保留文本块和工具块的先后顺序。"""

    if blocks and isinstance(blocks[-1], APITextBlock):
        blocks[-1] = APITextBlock(blocks[-1].text + text)
    else:
        blocks.append(APITextBlock(text))


def _add_usage(left: Usage, right: Usage) -> Usage:
    return Usage(
        left.input_tokens + right.input_tokens,
        left.output_tokens + right.output_tokens,
    )


def _usage(usage: Usage) -> dict[str, int]:
    return {
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
    }
