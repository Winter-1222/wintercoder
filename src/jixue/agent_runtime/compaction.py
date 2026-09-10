"""上下文请求整理、手动摘要和自动摘要事务，不执行用户工具。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from time import perf_counter
from uuid import uuid4

from jixue.agent_runtime.control import RunControl
from jixue.agent_runtime.events import (
    AgentEvent,
    AgentEventType,
    add_usage,
    error_event,
    event,
    loop_complete,
    subtract_usage,
    usage_payload,
)
from jixue.agent_runtime.model import ModelStream
from jixue.context import (
    COMPACTION_SYSTEM_PROMPT,
    KEEP_RECENT_CONVERSATION_TURNS,
    clear_old_tool_results,
    extract_compaction_summary,
    with_compaction_request,
)
from jixue.domain.conversation import APIMessage, ConversationError, ConversationManager, Usage
from jixue.llm.base import LLMClientError, LLMEventType

# 自动摘要连续失败时暂停，手动成功后恢复。
AUTO_COMPACTION_FAILURE_LIMIT = 3


class CompactionCancelled(Exception):
    """摘要请求被用户停止；调用方据此结束当前任务但不提交摘要。"""


class ContextCompactor:
    """持有会话与模型入口，所有压缩入口共用一次校验后提交的事务。"""

    def __init__(
        self, conversation: ConversationManager, model: ModelStream, control: RunControl,
        *, preserve_message_boundary: bool = False
    ) -> None:
        self._preserve_message_boundary = preserve_message_boundary
        self._conversation = conversation
        self._model = model
        self._control = control
        self._consecutive_failures = 0

    @property
    def paused(self) -> bool:
        return self._consecutive_failures >= AUTO_COMPACTION_FAILURE_LIMIT

    def request_messages(self) -> list[APIMessage]:
        """唯一请求入口：清理会话并转换协议，复用历史里已经保存的提醒。"""

        clear_old_tool_results(self._conversation)
        return self._conversation.to_api_format(
            merge_text=not self._preserve_message_boundary,
        )

    async def run_manual(self) -> AsyncIterator[AgentEvent]:
        """处理手动命令；真正的摘要事务也供自动压缩复用。"""

        started_at = perf_counter()
        message_id = f"msg_{uuid4().hex}"
        usage_before = self._conversation.total_usage
        compacted_messages: int | None = None
        failure: Exception | None = None
        try:
            compacted_messages = await self.apply()
        except Exception as error:
            failure = error

        compact_usage = subtract_usage(self._conversation.total_usage, usage_before)
        if compact_usage != Usage():
            yield event(
                AgentEventType.USAGE,
                turn=usage_payload(compact_usage),
                cumulative=usage_payload(self._conversation.total_usage),
            )

        # 摘要请求结束后关闭取消入口，避免收尾事件之间出现“已停止”的假回执。
        self._control.close_cancellation()
        if isinstance(failure, CompactionCancelled):
            yield self._complete(
                started_at,
                message_id,
                1,
                "cancelled",
                cancelled=True,
            )
            return
        if isinstance(failure, LLMClientError):
            yield error_event(
                failure.code,
                str(failure),
                retryable=failure.retryable,
            )
            return
        if isinstance(failure, (ConversationError, ValueError)):
            yield error_event("compact_failed", str(failure))
            return
        if failure is not None:
            yield error_event(
                "compact_internal_error",
                "上下文压缩发生内部错误，原历史没有改变",
            )
            return
        if compacted_messages is None:
            text = "当前可压缩的历史不足；至少需要 3 个完整对话轮，最近 2 轮会保留原文。"
            yield event(
                AgentEventType.STREAM_TEXT,
                text=text,
                message_id=message_id,
            )
            yield self._complete(
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
        yield event(
            AgentEventType.STREAM_TEXT,
            text=text,
            message_id=message_id,
        )
        yield event(
            AgentEventType.TURN_COMPLETE,
            iteration=1,
            stop_reason="compacted",
            tool_calls=0,
        )
        yield self._complete(
            started_at,
            message_id,
            1,
            "compacted",
        )

    async def apply(self) -> int | None:
        """生成并提交一次摘要；所有调用方共享同一条校验与回滚链。"""

        compact_usage = Usage()
        try:
            prepared = self._conversation.prepare_compaction(KEEP_RECENT_CONVERSATION_TURNS)
            if prepared is None:
                return None

            source, cutoff, compacted_messages = prepared
            raw_response: list[str] = []
            requested_tool = False
            complete_received = False
            summary_stop_reason: str | None = None
            async for llm_event in self._model.stream(
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
                    compact_usage = add_usage(compact_usage, llm_event.usage)
                elif llm_event.type is LLMEventType.COMPLETE:
                    complete_received = True
                    summary_stop_reason = llm_event.stop_reason

            if self._control.cancel_event.is_set():
                raise CompactionCancelled
            if requested_tool:
                raise ValueError("摘要模型错误地请求了工具，本次压缩未应用")
            if not complete_received or summary_stop_reason != "end_turn":
                reason = summary_stop_reason or "未收到完成事件"
                raise ValueError(f"摘要生成没有正常结束（{reason}），本次压缩未应用")

            summary = extract_compaction_summary("".join(raw_response))

            # 提交前还会检查含标签的实际大小；任何校验失败都保留旧消息。
            self._conversation.apply_compaction(summary, cutoff)
            # 手动压缩成功也可解除自动暂停，让后续任务重新获得预算保护。
            self._consecutive_failures = 0
            return compacted_messages
        finally:
            # 摘要请求同样产生费用；无论成功与否，都计入状态栏的会话累计值。
            self._conversation.record_usage(compact_usage)

    async def try_auto(self) -> tuple[bool, bool, Usage]:
        """尝试一次自动摘要，并返回“成功、取消、用量”。"""

        if self.paused:
            return False, False, Usage()

        usage_before = self._conversation.total_usage
        compacted = False
        cancelled = False
        try:
            compacted = await self.apply() is not None
        except CompactionCancelled:
            cancelled = True
        except Exception:
            self._consecutive_failures += 1

        compact_usage = subtract_usage(
            self._conversation.total_usage,
            usage_before,
        )
        return compacted, cancelled, compact_usage

    def _complete(
        self,
        started_at: float,
        message_id: str,
        iterations: int,
        stop_reason: str,
        *,
        cancelled: bool = False,
    ) -> AgentEvent:
        return loop_complete(
            started_at,
            message_id,
            iterations,
            stop_reason,
            model=self._model.model_name,
            mode=self._control.mode,
            turn_index=self._conversation.completed_turns + 1,
            cancelled=cancelled,
        )
