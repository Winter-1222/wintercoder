"""普通任务主循环：请求模型、执行工具、成对写回消息，然后决定是否继续。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator, Sequence
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
from jixue.domain.conversation import (
    APIToolResultBlock,
    ConversationError,
    ConversationManager,
    MessageStatus,
    Usage,
)
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
        reminder_override: str | None = None,
        tool_definitions: Sequence[ToolDefinition] | None = None,
    ) -> None:
        self._reminder_override = reminder_override
        self._tool_definitions = tool_definitions
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
        task_usage = Usage()
        attempts = RequestAttempts()
        response = ModelResponse()
        response_saved = False
        failure: AgentEvent | None = None
        iterations = 0
        stop_reason = "end_turn"
        consecutive_invalid = 0
        self._conversation.add_user(text)
        try:
            reminder = self._reminder_override
            if reminder is None:
                reminder = await asyncio.to_thread(
                    build_system_reminder,
                    self._project_root,
                    self._control.mode.value,
                    self._control.permission_mode.value,
                )
            if reminder:
                # 提醒随工作历史保存；相邻 user 在协议转换时合并，界面仍使用原始输入事件。
                self._conversation.add_user(reminder)
            tools = (
                self._tool_definitions
                if self._tool_definitions is not None
                else self._tools.to_api_format(read_only_only=self._control.mode is AgentMode.PLAN)
            )
            for iteration in range(1, self._max_iterations + 1):
                if self._control.cancel_event.is_set():
                    stop_reason = "cancelled"
                    break
                iterations = iteration
                response = ModelResponse()
                response_saved = False
                try:
                    async for item in self._respond(
                        tools, response, attempts, task_usage, message_id,
                    ):
                        yield item
                finally:
                    # 流中途失败也可能已经产生费用，不能等到成功结束才记账。
                    task_usage = add_usage(task_usage, response.usage)
                if self._control.cancel_event.is_set():
                    stop_reason = "cancelled"
                    break
                stop_reason = response.stop_reason
                if response.calls and stop_reason == "end_turn":
                    stop_reason = "unexpected_tool_stop"
                elif not response.calls and stop_reason == "tool_use":
                    stop_reason = "missing_tool_call"
                elif not response.calls and stop_reason == "end_turn" and not response.text.strip():
                    stop_reason = "empty_response"
                yield event(
                    AgentEventType.TURN_COMPLETE,
                    iteration=iteration,
                    stop_reason=stop_reason,
                    tool_calls=len(response.calls),
                )
                # 只有完整的 tool_use 响应才允许执行；截断流里的工具块只登记为未执行。
                if stop_reason != "tool_use" or not response.calls:
                    break
                if iteration == self._max_iterations:
                    stop_reason = "max_iterations"
                    break
                result = ToolRound(consecutive_invalid=consecutive_invalid)
                async for item in self._executor.execute(response.calls, result):
                    yield item
                # 先保存真实结果再处理停止信号，避免最后一批已完成工具随取消一起消失。
                self._conversation.add_tool_round(response.blocks, result.results)
                response_saved = True
                consecutive_invalid = result.consecutive_invalid
                if self._control.cancel_event.is_set():
                    stop_reason = "cancelled"
                    break
                if result.stop_reason:
                    stop_reason = result.stop_reason
                    break
        except ConversationError as error:
            failure = error_event("conversation_invalid", str(error))
        except LLMClientError as error:
            failure = error_event(error.code, str(error), retryable=error.retryable)
        except Exception:
            failure = error_event("llm_internal_error", "模型客户端发生内部错误")

        if self._control.cancel_event.is_set():
            stop_reason, failure = "cancelled", None
        # 最后一次决策已确定，关闭停止入口，防止收尾时出现“接受取消却报告成功”。
        self._control.close_cancellation()
        if failure is not None:
            stop_reason = "error"
        if response.calls and not response_saved:
            for item in self._record_unexecuted_response(response, stop_reason):
                yield item
            response_saved = True
        # 随工具轮保存过的过程文字不再重复追加；仅保存本次未提交的部分回复。
        content = "" if response_saved else response.text
        warning = self._limit_warning(stop_reason)
        if warning:
            content += warning
            yield event(AgentEventType.STREAM_TEXT, text=warning, message_id=message_id)
        status = (
            MessageStatus.CANCELLED if stop_reason == "cancelled"
            else MessageStatus.FAILED if stop_reason in {"error", "invalid_tool_limit", "refusal"}
            else MessageStatus.COMPLETE if stop_reason == "end_turn"
            else MessageStatus.INCOMPLETE
        )
        turn_index = self._conversation.completed_turns + 1
        self._conversation.add_assistant(content, usage=task_usage, status=status)
        if failure is not None:
            yield failure
            return
        yield loop_complete(
            started_at, message_id, iterations, stop_reason,
            model=self._model.model_name,
            mode=self._control.mode,
            turn_index=turn_index,
            is_error=status in {MessageStatus.FAILED, MessageStatus.INCOMPLETE},
            cancelled=status is MessageStatus.CANCELLED,
            incomplete=status is MessageStatus.INCOMPLETE,
        )

    def _record_unexecuted_response(
        self, response: ModelResponse, stop_reason: str,
    ) -> Iterator[AgentEvent]:
        """关闭已展示但尚未执行的调用，并保存配对结果，防止下一次误以为已经执行。"""

        reason = (
            "用户已停止任务，工具未执行。" if stop_reason == "cancelled"
            else "已达到 Agent 最大循环轮数，工具未执行。" if stop_reason == "max_iterations"
            else "模型响应未完整结束，工具未执行。"
        )
        events = [unexecuted_tool_event(call, reason) for call in response.calls]
        results = [APIToolResultBlock(call.tool_use_id, reason, True) for call in response.calls]
        self._conversation.add_tool_round(response.blocks, results)
        yield from events

    async def _respond(
        self,
        tools: Sequence[ToolDefinition],
        response: ModelResponse,
        attempts: RequestAttempts,
        task_usage: Usage,
        message_id: str,
    ) -> AsyncIterator[AgentEvent]:
        """请求前检查预算；供应商拒绝超长时，摘要后只重试一次。"""

        messages = self._compactor.request_messages()
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
                messages = self._compactor.request_messages()
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
                messages = self._compactor.request_messages()

    def _compaction_usage_event(self, usage: Usage, task_usage: Usage) -> AgentEvent:
        """摘要已计入会话账单，当前任务尚未入账的模型费用也要包含在累计值中。"""

        return event(
            AgentEventType.USAGE,
            turn=usage_payload(usage),
            cumulative=usage_payload(add_usage(self._conversation.total_usage, task_usage)),
        )

    def _limit_warning(self, stop_reason: str) -> str:
        if stop_reason == "max_iterations":
            return (
                f"\n\n> Agent 已执行 {self._max_iterations} 轮但仍未完成，已自动停止。"
                "发送“继续”可根据已有结果接着处理。"
            )
        if stop_reason == "invalid_tool_limit":
            return (
                f"\n\n> 模型连续 {INVALID_TOOL_LIMIT} 次请求不存在或已禁用的工具，"
                "Agent 已自动停止。"
            )
        reasons = {
            "max_tokens": "回复达到模型输出上限",
            "stream_incomplete": "模型流提前结束，未收到完整结束事件",
            "empty_response": "模型未返回有效答复",
            "unexpected_tool_stop": "工具调用与模型结束原因不一致",
            "missing_tool_call": "模型请求使用工具，但未返回工具调用",
            "refusal": "模型拒绝了本次请求",
        }
        if stop_reason in {"end_turn", "cancelled", "error"}:
            return ""
        reason = reasons.get(stop_reason, "模型返回了暂不支持的结束原因")
        if stop_reason == "refusal":
            return f"\n\n> {reason}，任务未完成。"
        return f"\n\n> {reason}，任务未完成。发送“继续”可根据已有结果接着处理。"
