"""霁雪真正的 Agent 核心：对话历史 → LLM → 工具 → 事件流。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import uuid4

from jixue.context import (
    AUTO_COMPACTION_TRIGGER_CHARACTERS,
    COMPACTION_SYSTEM_PROMPT,
    KEEP_RECENT_CONVERSATION_TURNS,
    ActiveContext,
    ToolResultStore,
    api_text_characters,
    extract_compaction_summary,
    needs_auto_compaction,
    with_compaction_request,
)
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
from jixue.permission import (
    PermissionCheck,
    PermissionDecision,
    PermissionMode,
    evaluate_permission,
)
from jixue.prompt import build_system_prompt, build_system_reminder
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


class _CompactionCancelled(Exception):
    """摘要请求被用户停止；调用方据此结束当前任务但不提交摘要。"""


class Agent:
    """协调对话、模型和工具，并让模型持续工作到任务结束。"""

    def __init__(
        self,
        llm: LLMClient,
        conversation: ConversationManager | None = None,
        tools: ToolRegistry | None = None,
        tool_context: ToolContext | None = None,
        max_iterations: int = 50,
        *,
        auto_compaction_trigger_characters: int = AUTO_COMPACTION_TRIGGER_CHARACTERS,
    ) -> None:
        if max_iterations < 1:
            raise ValueError("最大循环轮数必须大于 0")
        if auto_compaction_trigger_characters < 1:
            raise ValueError("自动压缩触发线必须大于 0")
        self._llm = llm
        self._conversation = conversation or ConversationManager()
        self._tools = tools or ToolRegistry()
        self._tool_context = tool_context or ToolContext(Path.cwd().resolve())
        # 所有内置工具和 MCP 工具都从这里经过同一套大结果保护。
        self._tool_result_store = ToolResultStore(self._tool_context.project_root)
        # 固定提示词只生成一次；每轮不变，供应商才有机会复用 Prompt Cache。
        self._system_prompt = build_system_prompt(self._tool_context.project_root)
        self._max_iterations = max_iterations
        self._auto_compaction_trigger_characters = auto_compaction_trigger_characters
        self._cancel_event = asyncio.Event()
        self._is_running = False
        self._mode = AgentMode.DO
        self._permission_mode = PermissionMode.CONFIRM_EDITS
        # Agent 等待用户确认时，Bridge 会通过这个 Future 把“允许/拒绝”送回来。
        self._permission_tool_use_id: str | None = None
        self._permission_future: asyncio.Future[bool] | None = None

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

    @property
    def permission_mode(self) -> PermissionMode:
        return self._permission_mode

    def set_permission_mode(self, mode: PermissionMode) -> None:
        """切换权限策略；任务运行中继续沿用本轮开始时的策略。"""

        if self._is_running:
            raise RuntimeError("任务运行中不能切换权限模式")
        self._permission_mode = mode

    def cancel(self) -> bool:
        """请求停止当前任务；返回 False 表示此刻没有正在运行的任务。"""

        if not self._is_running:
            return False
        self._cancel_event.set()
        # 如果 Agent 正停在权限确认处，只设置取消标记还不够：还要唤醒等待中的 Future。
        if self._permission_future is not None and not self._permission_future.done():
            self._permission_future.set_result(False)
        return True

    def respond_permission(self, tool_use_id: str, allow: bool) -> bool:
        """接收 UI 的权限决定；ID 不匹配表示这个确认已经过期。"""

        future = self._permission_future
        if (
            future is None
            or future.done()
            or tool_use_id != self._permission_tool_use_id
        ):
            return False
        future.set_result(allow)
        return True

    async def _stream_llm(
        self,
        history: Sequence[APIMessage],
        tool_definitions: Sequence[ToolDefinition],
        *,
        system_prompt: str | None = None,
    ) -> AsyncIterator[LLMStreamEvent]:
        """同时等待模型事件和取消信号，取消时关闭正在等待的流。"""

        iterator = self._llm.stream(
            history,
            tool_definitions,
            system=self._system_prompt if system_prompt is None else system_prompt,
        ).__aiter__()
        while not self._cancel_event.is_set():
            next_event = asyncio.ensure_future(anext(iterator))
            cancel_wait = asyncio.create_task(self._cancel_event.wait())
            done, _ = await asyncio.wait(
                (next_event, cancel_wait),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if cancel_wait in done:
                # 大多数网络库会立刻响应 cancel，但少数底层连接可能要等超时才退出。
                # 这里不能继续 await 它，否则页面会显示“正在停止”却迟迟无法解锁。
                _cancel_in_background(next_event)
                return

            cancel_wait.cancel()
            await asyncio.gather(cancel_wait, return_exceptions=True)
            try:
                yield next_event.result()
            except StopAsyncIteration:
                return

    async def run(self, user_text: str) -> AsyncIterator[AgentEvent]:
        """处理一条用户消息，并在过程发生时立即向外产生事件。"""

        clean_text = user_text.strip()
        if not clean_text:
            yield _error_event("invalid_input", "消息文本不能为空")
            return

        # /compact 是客户端命令，不是用户交给模型的问题，因此不能写进普通历史。
        if clean_text == "/compact":
            async for event in self._compact_context():
                yield event
            return

        # asyncio.Event 会绑定首次等待它的事件循环，每次任务都要新建。
        self._cancel_event = asyncio.Event()
        self._is_running = True
        started_at = perf_counter()
        message_id = f"msg_{uuid4().hex}"
        chunks: list[str] = []
        turn_usage = Usage()
        mode = self._mode
        permission_mode = self._permission_mode
        self._conversation.add_user(clean_text)

        try:
            # 时间和 Git 状态会变化，因此放进临时消息，而不是固定 System Prompt。
            reminder = await asyncio.to_thread(
                build_system_reminder,
                self._tool_context.project_root,
                mode.value,
                permission_mode.value,
            )
            full_history = _history_with_reminder(
                self._conversation.to_api_format(),
                reminder,
            )
        except ConversationError as error:
            yield _error_event("conversation_invalid", str(error))
            self._is_running = False
            return

        try:
            # Plan 模式只把只读工具告诉模型，这是第一层保护。
            tool_definitions = self._tools.to_api_format(read_only_only=mode is AgentMode.PLAN)
            stop_reason = "end_turn"
            iterations = 0
            reached_limit = False
            invalid_tool_limit_reached = False
            consecutive_invalid_tools = 0
            cancelled = False
            pending_tool_calls: list[LLMStreamEvent] = []
            # 当前任务的工具轮不进入 ConversationManager，重建压缩后的历史时要单独接回。
            task_history: list[APIMessage] = []
            auto_compaction_attempted = False
            # 这个对象只服务当前任务：保存哪些旧结果已经退出模型视图。
            active_context = ActiveContext()

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

                # full_history 保存原文；active_history 只是本轮发给模型的临时副本。
                active_history = active_context.build(full_history)
                if (
                    not auto_compaction_attempted
                    and needs_auto_compaction(
                        active_history,
                        self._auto_compaction_trigger_characters,
                    )
                ):
                    auto_compaction_attempted = True
                    usage_before = self._conversation.total_usage
                    compacted_messages: int | None = None
                    try:
                        compacted_messages = (
                            await self._apply_compaction_transaction()
                        )
                    except _CompactionCancelled:
                        cancelled = True
                        stop_reason = "cancelled"
                    except Exception:
                        # 自动摘要失败不能挡住原任务；保留旧视图继续发送。
                        # prompt-too-long 重试和连续失败暂停会在后续小步补上。
                        pass

                    compact_usage = _subtract_usage(
                        self._conversation.total_usage,
                        usage_before,
                    )
                    if compact_usage != Usage():
                        yield _event(
                            AgentEventType.USAGE,
                            turn=_usage(compact_usage),
                            cumulative=_usage(self._conversation.total_usage),
                        )
                    if cancelled:
                        break
                    if compacted_messages is not None:
                        # 不能继续使用压缩前已经生成的 full_history；重新取摘要视图，
                        # 再接回本任务已经完成的工具轮，最后重建 active_history。
                        full_history = [
                            *_history_with_reminder(
                                self._conversation.to_api_format(),
                                reminder,
                            ),
                            *task_history,
                        ]
                        active_history = active_context.build(full_history)

                async for llm_event in self._stream_llm(active_history, tool_definitions):
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

                    # 先完成校验和权限判断，只有通过的调用才会进入真正的执行函数。
                    executions: list[tuple[ToolResult, int] | None] = [None] * len(batch)
                    ready_calls: list[tuple[int, LLMStreamEvent]] = []
                    for index, call in enumerate(batch):
                        preflight = self._check_tool_call(call, mode, permission_mode)
                        if isinstance(preflight, ToolResult):
                            executions[index] = (preflight, 0)
                            continue
                        if preflight.decision is PermissionDecision.DENY:
                            executions[index] = (
                                ToolResult(f"权限拒绝：{preflight.reason}", is_error=True),
                                0,
                            )
                            continue
                        if preflight.decision is PermissionDecision.ASK:
                            waiter = self._begin_permission(call.tool_use_id)
                            tool = self._tools.get(call.tool_name)
                            assert tool is not None
                            yield _event(
                                AgentEventType.PERMISSION_REQUEST,
                                id=call.tool_use_id,
                                name=call.tool_name,
                                input=dict(call.tool_input),
                                reason=preflight.reason,
                                is_destructive=tool.is_destructive(),
                            )
                            try:
                                allowed = await waiter
                            finally:
                                self._clear_permission()
                            if self._cancel_event.is_set():
                                cancelled = True
                                stop_reason = "cancelled"
                                for pending in pending_tool_calls:
                                    yield _cancelled_tool_event(pending)
                                break
                            if not allowed:
                                executions[index] = (
                                    ToolResult("用户拒绝了本次工具调用", is_error=True),
                                    0,
                                )
                                continue
                        ready_calls.append((index, call))

                    if cancelled:
                        break
                    completed = await self._execute_tool_batch(
                        [call for _, call in ready_calls]
                    )
                    if completed is None:
                        cancelled = True
                        stop_reason = "cancelled"
                        for pending in pending_tool_calls:
                            yield _cancelled_tool_event(pending)
                        break
                    for (index, _), execution in zip(
                        ready_calls,
                        completed,
                        strict=True,
                    ):
                        executions[index] = execution
                    if any(execution is None for execution in executions):
                        raise RuntimeError("工具批次存在未处理的调用")
                    resolved_executions = [
                        execution for execution in executions if execution is not None
                    ]

                    # 即使安全工具并发完成，也按模型原来的调用顺序写回对话历史。
                    for call, (result, duration_ms) in zip(
                        batch,
                        resolved_executions,
                        strict=True,
                    ):
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
                completed_tool_round = [
                    APIMessage("assistant", tuple(response_blocks)),
                    APIMessage("user", tuple(result_blocks)),
                ]
                task_history.extend(completed_tool_round)
                full_history = [*full_history, *completed_tool_round]

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

            turn_index = _next_turn_index(self._conversation)
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
            yield self._loop_complete_event(
                started_at,
                message_id,
                iterations,
                stop_reason,
                turn_index=turn_index,
                is_error=reached_limit or invalid_tool_limit_reached,
                cancelled=cancelled,
            )
        except LLMClientError as error:
            self._remember_failed(chunks)
            yield _error_event(error.code, str(error), retryable=error.retryable)
        except Exception:
            self._remember_failed(chunks)
            yield _error_event("llm_internal_error", "模型客户端发生内部错误")
        finally:
            self._clear_permission()
            self._is_running = False

    async def _compact_context(self) -> AsyncIterator[AgentEvent]:
        """处理手动命令；真正的摘要事务也供自动压缩复用。"""

        self._cancel_event = asyncio.Event()
        self._is_running = True
        started_at = perf_counter()
        message_id = f"msg_{uuid4().hex}"
        usage_before = self._conversation.total_usage
        compacted_messages: int | None = None
        failure: Exception | None = None
        try:
            compacted_messages = await self._apply_compaction_transaction()
        except Exception as error:
            failure = error

        compact_usage = _subtract_usage(self._conversation.total_usage, usage_before)
        if compact_usage != Usage():
            yield _event(
                AgentEventType.USAGE,
                turn=_usage(compact_usage),
                cumulative=_usage(self._conversation.total_usage),
            )

        # 摘要请求结束后关闭取消入口，避免收尾事件之间出现“已停止”的假回执。
        self._is_running = False
        if isinstance(failure, _CompactionCancelled):
            yield self._loop_complete_event(
                started_at,
                message_id,
                1,
                "cancelled",
                cancelled=True,
            )
            return
        if isinstance(failure, LLMClientError):
            yield _error_event(
                failure.code,
                str(failure),
                retryable=failure.retryable,
            )
            return
        if isinstance(failure, (ConversationError, ValueError)):
            yield _error_event("compact_failed", str(failure))
            return
        if failure is not None:
            yield _error_event(
                "compact_internal_error",
                "上下文压缩发生内部错误，原历史没有改变",
            )
            return
        if compacted_messages is None:
            text = "当前可压缩的历史不足；至少需要 3 个完整对话轮，最近 2 轮会保留原文。"
            yield _event(
                AgentEventType.STREAM_TEXT,
                text=text,
                message_id=message_id,
            )
            yield self._loop_complete_event(
                started_at,
                message_id,
                0,
                "nothing_to_compact",
            )
            return

        text = (
            f"上下文压缩完成：已将 {compacted_messages} 条较早消息整理为摘要，"
            f"最近 {KEEP_RECENT_CONVERSATION_TURNS} 个对话轮保留原文。"
        )
        yield _event(
            AgentEventType.STREAM_TEXT,
            text=text,
            message_id=message_id,
        )
        yield _event(
            AgentEventType.TURN_COMPLETE,
            iteration=1,
            stop_reason="compacted",
            tool_calls=0,
        )
        yield self._loop_complete_event(
            started_at,
            message_id,
            1,
            "compacted",
        )

    async def _apply_compaction_transaction(self) -> int | None:
        """生成并提交一次摘要；所有调用方共享同一条校验与回滚链。"""

        compact_usage = Usage()
        try:
            prepared = self._conversation.prepare_compaction(
                KEEP_RECENT_CONVERSATION_TURNS
            )
            if prepared is None:
                return None

            source, cutoff, compacted_messages = prepared
            raw_response: list[str] = []
            requested_tool = False
            complete_received = False
            summary_stop_reason: str | None = None
            async for llm_event in self._stream_llm(
                with_compaction_request(source),
                (),
                system_prompt=COMPACTION_SYSTEM_PROMPT,
            ):
                # 摘要正文是内部数据，不边生成边展示，避免失败时留下“半份摘要”。
                if llm_event.type is LLMEventType.TEXT:
                    raw_response.append(llm_event.text)
                elif llm_event.type is LLMEventType.TOOL_USE:
                    requested_tool = True
                elif llm_event.type is LLMEventType.USAGE:
                    compact_usage = _add_usage(compact_usage, llm_event.usage)
                elif llm_event.type is LLMEventType.COMPLETE:
                    complete_received = True
                    summary_stop_reason = llm_event.stop_reason

            if self._cancel_event.is_set():
                raise _CompactionCancelled
            if requested_tool:
                raise ValueError("摘要模型错误地请求了工具，本次压缩未应用")
            if not complete_received or summary_stop_reason != "end_turn":
                reason = summary_stop_reason or "未收到完成事件"
                raise ValueError(f"摘要生成没有正常结束（{reason}），本次压缩未应用")

            summary = extract_compaction_summary("".join(raw_response))
            if len(summary) >= api_text_characters(source):
                raise ValueError("摘要没有比原历史更短，本次压缩未应用")

            # 上面任何一步失败都不会到这里；这里是压缩状态唯一的提交点。
            self._conversation.apply_compaction(summary, cutoff)
            return compacted_messages
        finally:
            # 摘要请求同样产生费用；无论成功与否，都计入状态栏的会话累计值。
            self._conversation.record_system_usage(compact_usage)

    def _loop_complete_event(
        self,
        started_at: float,
        message_id: str,
        iterations: int,
        stop_reason: str,
        *,
        turn_index: int | None = None,
        is_error: bool = False,
        cancelled: bool = False,
    ) -> AgentEvent:
        """统一生成任务结束事件，保证普通聊天和管理命令都会解锁 UI。"""

        return _event(
            AgentEventType.LOOP_COMPLETE,
            turn_index=(
                _next_turn_index(self._conversation)
                if turn_index is None
                else turn_index
            ),
            iterations=iterations,
            stop_reason=stop_reason,
            duration_ms=round((perf_counter() - started_at) * 1000),
            model=self.model_name,
            message_id=message_id,
            is_error=is_error,
            cancelled=cancelled,
            mode=self._mode.value,
        )

    def _check_tool_call(
        self,
        call: LLMStreamEvent,
        mode: AgentMode,
        permission_mode: PermissionMode,
    ) -> PermissionCheck | ToolResult:
        """在产生副作用前完成格式、工具存在性、参数、模式和权限检查。"""

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

    def _begin_permission(self, tool_use_id: str) -> asyncio.Future[bool]:
        """先建立等待对象再发事件，避免 UI 很快回复时丢失决定。"""

        if self._permission_future is not None:
            raise RuntimeError("已有工具正在等待权限确认")
        self._permission_tool_use_id = tool_use_id
        self._permission_future = asyncio.get_running_loop().create_future()
        return self._permission_future

    def _clear_permission(self) -> None:
        """清掉一次性确认状态，旧按钮再次点击时就会被拒绝。"""

        self._permission_tool_use_id = None
        self._permission_future = None

    async def _execute_tool_call(
        self,
        call: LLMStreamEvent,
    ) -> tuple[ToolResult, int]:
        """执行已经通过权限检查的工具；确认等待时间不计入工具耗时。"""

        started_at = perf_counter()
        result = await self._tools.execute(
            call.tool_name,
            self._tool_context,
            call.tool_input,
        )
        result = await self._tool_result_store.prepare(
            call.tool_use_id,
            call.tool_name,
            result,
        )
        duration_ms = round((perf_counter() - started_at) * 1000)
        return result, duration_ms

    async def _execute_tool_batch(
        self,
        calls: Sequence[LLMStreamEvent],
    ) -> list[tuple[ToolResult, int]] | None:
        """等待一批工具；用户取消时立即放弃等待并让 Agent 收尾。"""

        if not calls:
            return []

        async def execute_all() -> list[tuple[ToolResult, int]]:
            return list(await asyncio.gather(*(self._execute_tool_call(call) for call in calls)))

        execution = asyncio.create_task(execute_all())
        cancel_wait = asyncio.create_task(self._cancel_event.wait())
        done, _ = await asyncio.wait(
            (execution, cancel_wait),
            return_when=asyncio.FIRST_COMPLETED,
        )
        if cancel_wait in done:
            # 工具可能正在等待远程 HTTP；不再让这次等待占住后续聊天。
            _cancel_in_background(execution)
            return None

        cancel_wait.cancel()
        await asyncio.gather(cancel_wait, return_exceptions=True)
        return execution.result()

    def _remember_failed(self, chunks: list[str]) -> None:
        """保留已经显示的半截回复，但标记失败，下一轮不会发给 LLM。"""

        if chunks:
            self._conversation.add_assistant(
                "".join(chunks),
                status=MessageStatus.FAILED,
            )


def _history_with_reminder(
    history: Sequence[APIMessage],
    reminder: str,
) -> list[APIMessage]:
    """把客户端提醒附到当前问题副本，不污染真正保存的用户消息。"""

    result = list(history)
    # 当前用户问题一定是最后一条字符串 user 消息；倒序查找可避开工具结果块。
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


def _cancel_in_background(task: asyncio.Task[Any]) -> None:
    """请求取消但不阻塞 Agent；任务稍后结束时取走异常，避免控制台警告。"""

    task.cancel()

    def consume_result(done: asyncio.Task[Any]) -> None:
        # 这里处理的是已被我们主动取消的后台任务，异常不能再影响新一轮聊天。
        with suppress(BaseException):
            done.result()

    task.add_done_callback(consume_result)


def _cancelled_tool_event(call: LLMStreamEvent) -> AgentEvent:
    """关闭尚未执行的工具卡片，避免取消后一直显示“执行中”。"""

    return _event(
        AgentEventType.TOOL_RESULT,
        id=call.tool_use_id,
        name=call.tool_name,
        content="用户已停止任务，工具已中断或不再等待结果",
        is_error=True,
        duration_ms=0,
        metadata={},
    )


def _event(event_type: AgentEventType, **payload: object) -> AgentEvent:
    return AgentEvent(event_type, payload)


def _error_event(code: str, message: str, *, retryable: bool = False) -> AgentEvent:
    """所有请求错误都使用同一份结构，前端收到后会结束等待状态。"""

    return _event(
        AgentEventType.ERROR,
        code=code,
        message=message,
        retryable=retryable,
        scope="request",
    )


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


def _subtract_usage(current: Usage, previous: Usage) -> Usage:
    """计算后台请求新增的用量；累计值理论上只增不减。"""

    return Usage(
        max(0, current.input_tokens - previous.input_tokens),
        max(0, current.output_tokens - previous.output_tokens),
    )


def _next_turn_index(conversation: ConversationManager) -> int:
    """管理命令不写入历史，但结束事件仍需要一个稳定的 UI 轮次编号。"""

    return 1 + sum(
        message.role == "assistant" and message.status is MessageStatus.COMPLETE
        for message in conversation.messages
    )


def _usage(usage: Usage) -> dict[str, int]:
    return {
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
    }
