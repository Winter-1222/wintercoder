"""普通任务主循环：请求模型、执行工具、成对写回消息，然后决定是否继续。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from jixue.agent_runtime.compaction import ContextCompactor
from jixue.agent_runtime.control import RunControl
from jixue.agent_runtime.events import (
    AgentEvent,
    AgentEventType,
    AgentMode,
    add_usage,
    cancelled_tool_event,
    error_event,
    event,
    loop_complete,
    usage_payload,
)
from jixue.agent_runtime.execution import (
    INVALID_TOOL_LIMIT,
    ToolExecutor,
    ToolRound,
    unexecuted_tool_event,
)
from jixue.agent_runtime.model import ModelResponse, ModelStream
from jixue.context import needs_auto_compaction
from jixue.domain.conversation import ConversationError, ConversationManager, MessageStatus, Usage
from jixue.llm.base import LLMClientError, ToolDefinition
from jixue.prompt import build_system_reminder
from jixue.tools import ToolRegistry


@dataclass(slots=True)
class RequestAttempts:
    """额度属于整个用户任务，进入下一轮工具循环时不能重置。"""

    auto_compaction_attempted: bool = False
    prompt_too_long_retried: bool = False


class AgentLoop:
    """只协调已组装好的组件；具体模型协议、权限等待和摘要校验各自封装。"""

    def __init__(
        self,
        conversation: ConversationManager,
        model: ModelStream,
        executor: ToolExecutor,
        compactor: ContextCompactor,
        control: RunControl,
        tools: ToolRegistry,
        project_root: Path,
        max_iterations: int,
        auto_compaction_trigger_characters: int,
    ) -> None:
        self._conversation = conversation
        self._model = model
        self._executor = executor
        self._compactor = compactor
        self._control = control
        self._tools = tools
        self._project_root = project_root
        self._max_iterations = max_iterations
        self._auto_compaction_trigger_characters = auto_compaction_trigger_characters

    async def run(self, text: str) -> AsyncIterator[AgentEvent]:
        """完整任务只读这一条链：模型 → 工具 → 会话，直到结束或停止。"""

        started_at = perf_counter()
        message_id = f"msg_{uuid4().hex}"
        chunks: list[str] = []
        task_usage = Usage()
        attempts = RequestAttempts()
        response = ModelResponse()
        iterations = 0
        stop_reason = "end_turn"
        consecutive_invalid = 0
        self._conversation.add_user(text)
        try:
            reminder = await asyncio.to_thread(
                build_system_reminder,
                self._project_root,
                self._control.mode.value,
                self._control.permission_mode.value,
            )
            tools = self._tools.to_api_format(read_only_only=self._control.mode is AgentMode.PLAN)
            for iteration in range(1, self._max_iterations + 1):
                if self._control.cancel_event.is_set():
                    stop_reason = "cancelled"
                    break
                iterations = iteration
                response = ModelResponse()
                try:
                    async for item in self._respond(
                        reminder,
                        tools,
                        response,
                        attempts,
                        task_usage,
                        message_id,
                    ):
                        if item.type is AgentEventType.STREAM_TEXT:
                            chunks.append(str(item.payload["text"]))
                        yield item
                finally:
                    # 流中途失败也可能已经产生费用，不能等到成功结束才记账。
                    task_usage = add_usage(task_usage, response.usage)
                if self._control.cancel_event.is_set():
                    stop_reason = "cancelled"
                    for call in response.calls:
                        yield cancelled_tool_event(call)
                    break
                stop_reason = response.stop_reason
                yield event(
                    AgentEventType.TURN_COMPLETE,
                    iteration=iteration,
                    stop_reason=stop_reason,
                    tool_calls=len(response.calls),
                )
                if not response.calls:
                    break
                if iteration == self._max_iterations:
                    stop_reason = "max_iterations"
                    for call in response.calls:
                        yield unexecuted_tool_event(call, "已达到 Agent 最大循环轮数，工具未执行")
                    break
                result = ToolRound(consecutive_invalid=consecutive_invalid)
                async for item in self._executor.execute(response.calls, result):
                    yield item
                consecutive_invalid = result.consecutive_invalid
                if self._control.cancel_event.is_set():
                    stop_reason = "cancelled"
                    break
                if result.stop_reason:
                    stop_reason = result.stop_reason
                    break
                self._conversation.add_tool_round(response.blocks, result.results)

            warning = self._limit_warning(stop_reason)
            if warning:
                chunks.append(warning)
                yield event(AgentEventType.STREAM_TEXT, text=warning, message_id=message_id)
            cancelled = stop_reason == "cancelled"
            failed = stop_reason in ("max_iterations", "invalid_tool_limit")
            turn_index = self._conversation.completed_turns + 1
            if cancelled:
                self._conversation.cancel_last_user()
            # 过程文字已随工具轮写入；正常结束只保存最终回复，取消时保留已展示片段。
            self._conversation.add_assistant(
                "".join(chunks) if cancelled else response.text,
                usage=task_usage,
                status=(
                    MessageStatus.CANCELLED
                    if cancelled
                    else MessageStatus.FAILED
                    if failed
                    else MessageStatus.COMPLETE
                ),
            )
            yield loop_complete(
                started_at,
                message_id,
                iterations,
                stop_reason,
                model=self._model.model_name,
                mode=self._control.mode,
                turn_index=turn_index,
                is_error=failed,
                cancelled=cancelled,
            )
        except ConversationError as error:
            self._remember_failed(chunks, task_usage)
            yield error_event("conversation_invalid", str(error))
        except LLMClientError as error:
            self._remember_failed(chunks, task_usage)
            yield error_event(error.code, str(error), retryable=error.retryable)
        except Exception:
            self._remember_failed(chunks, task_usage)
            yield error_event("llm_internal_error", "模型客户端发生内部错误")

    async def _respond(
        self,
        reminder: str,
        tools: Sequence[ToolDefinition],
        response: ModelResponse,
        attempts: RequestAttempts,
        task_usage: Usage,
        message_id: str,
    ) -> AsyncIterator[AgentEvent]:
        """请求前检查预算；供应商拒绝超长时，摘要后只重试一次。"""

        messages = self._compactor.request_messages(reminder)
        if (
            not attempts.auto_compaction_attempted
            and not self._compactor.paused
            and needs_auto_compaction(messages, self._auto_compaction_trigger_characters)
        ):
            attempts.auto_compaction_attempted = True
            compacted, cancelled, compact_usage = await self._compactor.try_auto()
            if compact_usage != Usage():
                yield self._compaction_usage_event(compact_usage, task_usage)
            if cancelled:
                return
            if compacted:
                messages = self._compactor.request_messages(reminder)
        while True:
            try:
                async for item in self._model.respond(
                    messages,
                    tools,
                    response,
                    message_id=message_id,
                    task_usage=task_usage,
                    conversation_usage=self._conversation.total_usage,
                ):
                    yield item
                return
            except LLMClientError as error:
                if (
                    error.code != "prompt_too_long"
                    or attempts.prompt_too_long_retried
                    or response.event_received
                ):
                    raise
                attempts.prompt_too_long_retried = True
                attempts.auto_compaction_attempted = True
                compacted, cancelled, compact_usage = await self._compactor.try_auto()
                if compact_usage != Usage():
                    yield self._compaction_usage_event(compact_usage, task_usage)
                if cancelled:
                    return
                if not compacted:
                    raise
                messages = self._compactor.request_messages(reminder)

    def _compaction_usage_event(self, usage: Usage, task_usage: Usage) -> AgentEvent:
        """摘要已计入会话账单，当前任务尚未入账的模型费用也要包含在累计值中。"""

        return event(
            AgentEventType.USAGE,
            turn=usage_payload(usage),
            cumulative=usage_payload(add_usage(self._conversation.total_usage, task_usage)),
        )

    def _limit_warning(self, stop_reason: str) -> str:
        if stop_reason == "max_iterations":
            return f"\n\n> Agent 已执行 {self._max_iterations} 轮但仍未完成，已自动停止。"
        if stop_reason == "invalid_tool_limit":
            return (
                f"\n\n> 模型连续 {INVALID_TOOL_LIMIT} 次请求不存在或已禁用的工具，"
                "Agent 已自动停止。"
            )
        return ""

    def _remember_failed(self, chunks: list[str], usage: Usage) -> None:
        """保留已展示的半截回复，但下一次请求不会把它当作完整回答。"""

        if chunks:
            self._conversation.add_assistant(
                "".join(chunks), status=MessageStatus.FAILED, usage=usage
            )
        else:
            self._conversation.record_usage(usage)
