"""霁雪真正的 Agent 核心：对话历史 → LLM → 工具 → 事件流。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping, Sequence
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
from jixue.llm.base import (
    LLMClient,
    LLMClientError,
    LLMEventType,
    LLMStreamEvent,
    ToolDefinition,
)
from jixue.tools import ToolContext, ToolRegistry, ToolResult

# 模型连续三次请求不存在或已禁用的工具，通常说明它已经无法自行纠正。
INVALID_TOOL_LIMIT = 3


class AgentMode(StrEnum):
    """Agent 的工作方式：Do 可以执行全部工具，Plan 只能调查。"""

    DO = "do"
    PLAN = "plan"


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
        self._cancel_event = asyncio.Event()
        self._is_running = False
        self._mode = AgentMode.DO

    @property
    def model_name(self) -> str:
        return self._llm.model_name

    @property
    def messages(self) -> tuple[Message, ...]:
        return self._conversation.messages

    @property
    def mode(self) -> AgentMode:
        return self._mode

    def set_mode(self, mode: AgentMode) -> None:
        """切换工作模式；任务运行中不允许改变本轮规则。"""

        if self._is_running:
            raise RuntimeError("任务运行中不能切换模式")
        self._mode = mode

    def cancel(self) -> bool:
        """请求停止当前任务；返回 False 表示此刻没有正在运行的任务。"""

        if not self._is_running:
            return False
        self._cancel_event.set()
        return True

    async def _stream_llm(
        self,
        history: Sequence[APIMessage],
        tool_definitions: Sequence[ToolDefinition],
    ) -> AsyncIterator[LLMStreamEvent]:
        """同时等待模型事件和取消信号，取消时关闭正在等待的流。"""

        iterator = self._llm.stream(history, tool_definitions).__aiter__()
        while not self._cancel_event.is_set():
            next_event = asyncio.ensure_future(anext(iterator))
            cancel_wait = asyncio.create_task(self._cancel_event.wait())
            done, _ = await asyncio.wait(
                (next_event, cancel_wait),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if cancel_wait in done:
                next_event.cancel()
                await asyncio.gather(next_event, return_exceptions=True)
                return

            cancel_wait.cancel()
            await asyncio.gather(cancel_wait, return_exceptions=True)
            try:
                yield next_event.result()
            except StopAsyncIteration:
                return

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

        # asyncio.Event 会绑定首次等待它的事件循环，每次任务都要新建。
        self._cancel_event = asyncio.Event()
        self._is_running = True
        started_at = perf_counter()
        message_id = f"msg_{uuid4().hex}"
        chunks: list[str] = []
        turn_usage = Usage()
        mode = self._mode
        self._conversation.add_user(user_text.strip())

        try:
            history = _history_for_mode(
                self._conversation.to_api_format(),
                mode,
                self._tool_context.project_root,
            )
        except ConversationError as error:
            yield _event(
                AgentEventType.ERROR,
                code="conversation_invalid",
                message=str(error),
                retryable=False,
                scope="request",
            )
            self._is_running = False
            return

        try:
            # Plan 模式只把只读工具告诉模型，这是第一层保护。
            tool_definitions = self._tools.to_api_format(
                read_only_only=mode is AgentMode.PLAN
            )
            stop_reason = "end_turn"
            iterations = 0
            reached_limit = False
            invalid_tool_limit_reached = False
            consecutive_invalid_tools = 0
            cancelled = False
            pending_tool_calls: list[LLMStreamEvent] = []

            # 一轮就是一次 LLM 请求。模型需要工具时，执行后把结果送回下一轮；
            # 模型不再请求工具时，说明它已经给出最终答复，循环自然结束。
            for iteration in range(1, self._max_iterations + 1):
                if self._cancel_event.is_set():
                    cancelled = True
                    stop_reason = "cancelled"
                    break
                iterations = iteration
                response_blocks: list[APIContentBlock] = []
                tool_calls: list[LLMStreamEvent] = []
                pending_tool_calls = []

                async for llm_event in self._stream_llm(history, tool_definitions):
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
                        pending_tool_calls.append(llm_event)
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

                if self._cancel_event.is_set():
                    cancelled = True
                    stop_reason = "cancelled"
                    for call in pending_tool_calls:
                        yield _cancelled_tool_event(call)
                    break
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
                for batch in _partition_tool_calls(tool_calls, self._tools):
                    if self._cancel_event.is_set():
                        cancelled = True
                        stop_reason = "cancelled"
                        for pending in pending_tool_calls:
                            yield _cancelled_tool_event(pending)
                        break

                    # 一个安全批次可以有多个调用；不安全批次永远只有一个调用。
                    executions = await asyncio.gather(
                        *(self._execute_tool_call(call, mode) for call in batch)
                    )
                    # gather 的返回顺序与输入顺序一致，API 历史不会因为并发而乱序。
                    for call, (result, duration_ms) in zip(batch, executions, strict=True):
                        # 只有“工具不存在或被禁用”才累计；合法工具会打断连续计数。
                        tool_is_invalid = (
                            not call.tool_error and self._tools.get(call.tool_name) is None
                        )
                        consecutive_invalid_tools = (
                            consecutive_invalid_tools + 1 if tool_is_invalid else 0
                        )
                        result_blocks.append(
                            APIToolResultBlock(
                                call.tool_use_id,
                                result.content,
                                result.is_error,
                            )
                        )
                        pending_tool_calls.remove(call)
                        yield _event(
                            AgentEventType.TOOL_RESULT,
                            id=call.tool_use_id,
                            name=call.tool_name,
                            content=result.content,
                            is_error=result.is_error,
                            duration_ms=duration_ms,
                            metadata=dict(result.metadata),
                        )

                        if consecutive_invalid_tools >= INVALID_TOOL_LIMIT:
                            invalid_tool_limit_reached = True
                            stop_reason = "invalid_tool_limit"
                            # 同一轮中可能还有工具卡片；虽然不再执行，也必须把它们收尾。
                            for pending in pending_tool_calls:
                                yield _event(
                                    AgentEventType.TOOL_RESULT,
                                    id=pending.tool_use_id,
                                    name=pending.tool_name,
                                    content="连续异常工具请求过多，工具未执行",
                                    is_error=True,
                                    duration_ms=0,
                                    metadata={},
                                )
                            pending_tool_calls.clear()
                            break
                    if invalid_tool_limit_reached:
                        break

                if cancelled:
                    break
                if self._cancel_event.is_set():
                    cancelled = True
                    stop_reason = "cancelled"
                    break
                if invalid_tool_limit_reached:
                    break
                # 工具结果必须紧跟模型的 tool_use，并使用相同的 tool_use_id。
                history = [
                    *history,
                    APIMessage("assistant", tuple(response_blocks)),
                    APIMessage("user", tuple(result_blocks)),
                ]

            if reached_limit:
                warning = f"\n\n> Agent 已执行 {self._max_iterations} 轮但仍未完成，已自动停止。"
                chunks.append(warning)
                yield _event(
                    AgentEventType.STREAM_TEXT,
                    text=warning,
                    message_id=message_id,
                )

            if invalid_tool_limit_reached:
                warning = (
                    f"\n\n> 模型连续 {INVALID_TOOL_LIMIT} 次请求不存在或已禁用的工具，"
                    "Agent 已自动停止。"
                )
                chunks.append(warning)
                yield _event(
                    AgentEventType.STREAM_TEXT,
                    text=warning,
                    message_id=message_id,
                )

            turn_index = 1 + sum(
                message.role == "assistant" and message.status is MessageStatus.COMPLETE
                for message in self._conversation.messages
            )
            answer = "".join(chunks)
            if cancelled:
                # 取消的本轮仍可供 UI 复盘，但 user 和 assistant 都不进入下次 API 历史。
                self._conversation.cancel_last_user()
                self._conversation.add_assistant(
                    answer,
                    usage=turn_usage,
                    status=MessageStatus.CANCELLED,
                )
            elif answer:
                self._conversation.add_assistant(
                    answer,
                    usage=turn_usage,
                    status=(
                        MessageStatus.FAILED
                        if reached_limit or invalid_tool_limit_reached
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
                is_error=reached_limit or invalid_tool_limit_reached,
                cancelled=cancelled,
                mode=mode.value,
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
        finally:
            self._is_running = False

    async def _execute_tool_call(
        self,
        call: LLMStreamEvent,
        mode: AgentMode,
    ) -> tuple[ToolResult, int]:
        """执行一个工具调用，并把耗时和普通失败一起返回。"""

        started_at = perf_counter()
        tool = self._tools.get(call.tool_name)
        if call.tool_error:
            result = ToolResult(call.tool_error, is_error=True)
        elif mode is AgentMode.PLAN and tool is not None and not tool.is_read_only():
            # 即使模型猜出了未展示的写工具名，也会在真正执行前被第二层保护拦住。
            result = ToolResult("Plan 模式只允许使用只读工具", is_error=True)
        else:
            result = await self._tools.execute(
                call.tool_name,
                self._tool_context,
                call.tool_input,
            )
        duration_ms = round((perf_counter() - started_at) * 1000)
        return result, duration_ms

    def _remember_failed(self, chunks: list[str]) -> None:
        """保留已经显示的半截回复，但标记失败，下一轮不会发给 LLM。"""

        if chunks:
            self._conversation.add_assistant(
                "".join(chunks),
                status=MessageStatus.FAILED,
            )


def _history_for_mode(
    history: Sequence[APIMessage],
    mode: AgentMode,
    project_root: Path,
) -> list[APIMessage]:
    """Plan 指令只加入本次 API 历史，不污染用户真正保存的消息。"""

    result = list(history)
    if mode is AgentMode.DO:
        return result

    reminder = (
        "<system-reminder>\n"
        "当前处于 Plan 模式。请先使用只读工具调查现状，再给出清晰的执行计划；"
        "不要写入、编辑或删除文件，也不要执行会改变环境的操作。"
        "最终只输出计划，等待用户切换到 Do 模式。\n"
        f"当前工作目录：{project_root}\n"
        "</system-reminder>"
    )
    # 当前用户问题一定是最后一条字符串 user 消息；倒序查找让函数更耐受工具块历史。
    for index in range(len(result) - 1, -1, -1):
        message = result[index]
        if message.role == "user" and isinstance(message.content, str):
            result[index] = APIMessage("user", f"{message.content}\n\n{reminder}")
            break
    return result


def _partition_tool_calls(
    calls: Sequence[LLMStreamEvent],
    tools: ToolRegistry,
) -> list[list[LLMStreamEvent]]:
    """把连续安全调用放在一起，不安全调用各自成为一个串行批次。"""

    batches: list[list[LLMStreamEvent]] = []
    safe_batch: list[LLMStreamEvent] = []
    for call in calls:
        tool = tools.get(call.tool_name)
        is_safe = (
            not call.tool_error
            and tool is not None
            and tool.is_concurrency_safe(call.tool_input)
        )
        if is_safe:
            safe_batch.append(call)
            continue
        if safe_batch:
            batches.append(safe_batch)
            safe_batch = []
        # 工具不存在、参数 JSON 损坏和声明不安全的调用都采用保守串行。
        batches.append([call])
    if safe_batch:
        batches.append(safe_batch)
    return batches


def _cancelled_tool_event(call: LLMStreamEvent) -> AgentEvent:
    """关闭尚未执行的工具卡片，避免取消后一直显示“执行中”。"""

    return _event(
        AgentEventType.TOOL_RESULT,
        id=call.tool_use_id,
        name=call.tool_name,
        content="用户已停止任务，工具未执行",
        is_error=True,
        duration_ms=0,
        metadata={},
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
