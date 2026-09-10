"""工具批次执行：校验与权限确认、并发、结果落盘和异常次数保护。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field, replace
from time import perf_counter

from jixue.agent_runtime.control import RunControl, cancel_in_background
from jixue.agent_runtime.events import (
    AgentEvent,
    AgentEventType,
    AgentMode,
    event,
)
from jixue.context import ToolResultStore
from jixue.domain.conversation import APIContentBlock, APIToolResultBlock
from jixue.llm.base import LLMStreamEvent
from jixue.permission import (
    PermissionCheck,
    PermissionDecision,
    PermissionMode,
    evaluate_permission,
)
from jixue.tools import ToolContext, ToolRegistry, ToolResult

INVALID_TOOL_LIMIT = 3


@dataclass(slots=True)
class ToolRound:
    """一轮工具执行结果；异常次数从上一轮接续，不另存消息历史。"""

    results: list[APIContentBlock] = field(default_factory=list)
    consecutive_invalid: int = 0
    stop_reason: str | None = None


class ToolExecutor:
    """只执行通过检查的调用，结果按模型给出的顺序返回。"""

    def __init__(
        self,
        tools: ToolRegistry,
        context: ToolContext,
        control: RunControl,
        blocked_tools: frozenset[str] = frozenset(),
    ) -> None:
        self._blocked_tools = blocked_tools
        self._tools = tools
        self._tool_context = context
        self._control = control
        self._tool_result_store = ToolResultStore(context.project_root)

    def _check_tool_call(
        self,
        call: LLMStreamEvent,
        mode: AgentMode,
        permission_mode: PermissionMode,
    ) -> PermissionCheck | ToolResult:
        """在产生副作用前完成格式、工具存在性、参数、模式和权限检查。"""

        if call.tool_name in self._blocked_tools:
            return ToolResult("子 Agent 不允许再次委派或写入记忆", is_error=True)
        if call.tool_error:
            return ToolResult(call.tool_error, is_error=True)
        tool = self._tools.get(call.tool_name)
        if tool is None:
            return ToolResult(f"工具不存在或未启用：{call.tool_name}", is_error=True)
        input_error = tool.validate_input(call.tool_input)
        if input_error:
            return ToolResult(f"工具参数错误：{input_error}", is_error=True)
        if mode is AgentMode.PLAN and not tool.is_read_only():
            # 即使模型猜出了未展示的写工具名，也会在真正执行前被第二层保护拦住。
            return ToolResult("Plan 模式只允许使用只读工具", is_error=True)
        return evaluate_permission(
            self._tool_context.project_root,
            tool,
            call.tool_input,
            permission_mode,
        )

    async def _execute_tool_call(
        self,
        call: LLMStreamEvent,
    ) -> tuple[ToolResult, int]:
        """执行已经通过权限检查的工具；确认等待时间不计入工具耗时。"""

        started_at = perf_counter()
        try:
            result = await self._tools.execute(
                call.tool_name,
                replace(self._tool_context, tool_use_id=call.tool_use_id),
                call.tool_input,
            )
            result = await self._tool_result_store.prepare(
                call.tool_use_id, call.tool_name, result,
            )
        except Exception as error:
            # 工具内部异常也要成为配对结果；异常前可能已有副作用，不能宣称未执行。
            result = ToolResult(
                f"工具执行异常（{type(error).__name__}），结果未知；请先核对实际状态。",
                is_error=True,
                metadata={"execution_state": "unknown"},
            )
        duration_ms = round((perf_counter() - started_at) * 1000)
        return result, duration_ms

    async def _execute_tool_batch(
        self,
        calls: Sequence[LLMStreamEvent],
    ) -> list[tuple[ToolResult, int] | None]:
        """停止只中断未完成的等待，已经拿到的并发结果仍按原顺序返回。"""

        if not calls:
            return []
        tasks = [asyncio.create_task(self._execute_tool_call(call)) for call in calls]

        async def execute_all() -> None:
            await asyncio.gather(*tasks)

        execution = asyncio.create_task(execute_all())
        cancel_wait = asyncio.create_task(self._control.cancel_event.wait())
        try:
            await asyncio.wait((execution, cancel_wait), return_when=asyncio.FIRST_COMPLETED)
            # 先读取已完成项，再取消剩余任务；两者同时就绪时不能覆盖真实成功结果。
            completed = [task.result() if task.done() and not task.cancelled() else None
                         for task in tasks]
        except asyncio.CancelledError:
            cancel_in_background(execution)
            raise
        finally:
            cancel_wait.cancel()
            await asyncio.gather(cancel_wait, return_exceptions=True)
        # 远程调用或线程可能延迟响应取消，后续只能把它们标为结果未知。
        cancel_in_background(execution)
        return completed

    async def execute(
        self,
        calls: Sequence[LLMStreamEvent],
        result: ToolRound,
    ) -> AsyncIterator[AgentEvent]:
        """每个调用都得到一个结果，包括已完成、未启动和停止后结果未知的调用。"""

        for batch in partition_tool_calls(calls, self._tools):
            executions: list[tuple[ToolResult, int] | None] = [None] * len(batch)
            ready: list[tuple[int, LLMStreamEvent]] = []
            started: set[int] = set()
            for index, call in enumerate(batch):
                if self._control.cancel_event.is_set():
                    result.stop_reason = "cancelled"
                if result.stop_reason:
                    break
                try:
                    check = self._check_tool_call(
                        call, self._control.mode, self._control.permission_mode
                    )
                except Exception:
                    check = ToolResult("工具检查异常，工具未执行。", is_error=True)
                if isinstance(check, ToolResult):
                    executions[index] = (check, 0)
                    continue
                if check.decision is PermissionDecision.DENY:
                    executions[index] = (ToolResult(f"权限拒绝：{check.reason}", is_error=True), 0)
                    continue
                if check.decision is PermissionDecision.ASK:
                    waiter = self._control.begin_permission(call.tool_use_id)
                    tool = self._tools.get(call.tool_name)
                    assert tool is not None
                    yield event(
                        AgentEventType.PERMISSION_REQUEST,
                        id=call.tool_use_id,
                        name=call.tool_name,
                        input=dict(call.tool_input),
                        reason=check.reason,
                        is_destructive=tool.is_destructive(),
                    )
                    try:
                        allowed = await waiter
                    finally:
                        self._control.clear_permission()
                    if self._control.cancel_event.is_set():
                        result.stop_reason = "cancelled"
                        break
                    if not allowed:
                        executions[index] = (ToolResult("用户拒绝了本次工具调用", is_error=True), 0)
                        continue
                ready.append((index, call))
            if not result.stop_reason and not self._control.cancel_event.is_set():
                started = {index for index, _ in ready}
                completed = await self._execute_tool_batch([call for _, call in ready])
                for (index, _), completed_execution in zip(ready, completed, strict=True):
                    executions[index] = completed_execution
            if self._control.cancel_event.is_set():
                result.stop_reason = "cancelled"
            for index, (call, execution) in enumerate(zip(batch, executions, strict=True)):
                if execution is None:
                    if index in started:
                        content = "工具已启动，但停止等待时未取得结果；结果未知，请先核对实际状态。"
                        execution_state = "unknown"
                    else:
                        content = (
                            "用户已停止任务，工具未执行。"
                            if result.stop_reason == "cancelled"
                            else "连续异常工具请求过多，工具未执行。"
                        )
                        execution_state = "not_started"
                    tool_result = ToolResult(
                        content, is_error=True, metadata={"execution_state": execution_state}
                    )
                    duration_ms = 0
                else:
                    tool_result, duration_ms = execution
                    invalid = not call.tool_error and self._tools.get(call.tool_name) is None
                    result.consecutive_invalid = result.consecutive_invalid + 1 if invalid else 0
                    if result.consecutive_invalid >= INVALID_TOOL_LIMIT:
                        result.stop_reason = "invalid_tool_limit"
                result.results.append(
                    APIToolResultBlock(call.tool_use_id, tool_result.content, tool_result.is_error)
                )
                yield event(
                    AgentEventType.TOOL_RESULT,
                    id=call.tool_use_id,
                    name=call.tool_name,
                    content=tool_result.content,
                    is_error=tool_result.is_error,
                    duration_ms=duration_ms,
                    metadata=dict(tool_result.metadata),
                )


def partition_tool_calls(
    calls: Sequence[LLMStreamEvent],
    tools: ToolRegistry,
) -> list[list[LLMStreamEvent]]:
    """把连续安全调用放在一起，不安全调用各自成为一个串行批次。"""

    batches: list[list[LLMStreamEvent]] = []
    safe_batch: list[LLMStreamEvent] = []
    for call in calls:
        tool = tools.get(call.tool_name)
        is_safe = (
            not call.tool_error and tool is not None and tool.is_concurrency_safe(call.tool_input)
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


def unexecuted_tool_event(call: LLMStreamEvent, reason: str) -> AgentEvent:
    """未执行的工具也要结束卡片，不能留在执行中。"""

    return event(
        AgentEventType.TOOL_RESULT,
        id=call.tool_use_id,
        name=call.tool_name,
        content=reason,
        is_error=True,
        duration_ms=0,
        metadata={"execution_state": "not_started"},
    )
