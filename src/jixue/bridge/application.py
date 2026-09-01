"""一轮聊天的核心：历史 → LLM 流 → UI 事件。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from jixue import __version__
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
from jixue.domain.events import Envelope
from jixue.llm.base import LLMClient, LLMClientError, LLMEventType, LLMStreamEvent
from jixue.tools import ToolContext, ToolRegistry, ToolResult


class BridgeApplication:
    """不处理 stdin/stdout，只处理握手和聊天业务。"""

    def __init__(
        self,
        llm: LLMClient,
        conversation: ConversationManager | None = None,
        tools: ToolRegistry | None = None,
        tool_context: ToolContext | None = None,
    ) -> None:
        self._llm = llm
        self._conversation = conversation or ConversationManager()
        self._tools = tools or ToolRegistry()
        self._tool_context = tool_context or ToolContext(Path.cwd().resolve())
        self._chat_lock = asyncio.Lock()

    @property
    def messages(self) -> tuple[Message, ...]:
        return self._conversation.messages

    async def handle(self, command: Envelope) -> AsyncIterator[Envelope]:
        if command.type == "bridge.hello":
            yield Envelope.create(
                "bridge.ready",
                command.request_id,
                0,
                {
                    "protocol_version": command.version,
                    "backend_version": __version__,
                    "model": self._llm.model_name,
                    "capabilities": ["stream_text", "tool_use", "tool_result", "usage"],
                },
            )
        elif command.type == "chat.send":
            async for event in self._chat(command):
                yield event
        else:
            yield self._error(
                command.request_id,
                "unknown_command",
                f"未知命令：{command.type}",
                scope="command",
            )

    async def _chat(self, command: Envelope) -> AsyncIterator[Envelope]:
        text = command.payload.get("text")
        if not isinstance(text, str) or not text.strip():
            yield self._error(
                command.request_id,
                "invalid_input",
                "消息文本不能为空",
                scope="request",
            )
            return

        async with self._chat_lock:
            sequence = 0
            message_id = f"msg_{uuid4().hex}"
            started_at = perf_counter()
            chunks: list[str] = []
            turn_usage = Usage()

            self._conversation.add_user(text.strip())
            try:
                history = self._conversation.to_api_format()
            except ConversationError as error:
                yield self._error(
                    command.request_id,
                    "conversation_invalid",
                    str(error),
                    scope="request",
                )
                return

            try:
                tool_definitions = self._tools.to_api_format()
                stop_reason = "end_turn"

                # 第二章固定最多两次 LLM 请求：请求工具一次，返回结果后收尾一次。
                for api_pass in range(2):
                    response_blocks: list[APIContentBlock] = []
                    tool_calls: list[LLMStreamEvent] = []

                    async for llm_event in self._llm.stream(history, tool_definitions):
                        if llm_event.type == LLMEventType.TEXT:
                            chunks.append(llm_event.text)
                            self._append_text(response_blocks, llm_event.text)
                            yield Envelope.create(
                                "stream_text",
                                command.request_id,
                                sequence,
                                {"text": llm_event.text, "message_id": message_id},
                            )
                            sequence += 1
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
                            yield Envelope.create(
                                "tool_use", command.request_id, sequence, payload
                            )
                            sequence += 1
                        elif llm_event.type == LLMEventType.USAGE:
                            turn_usage = self._add_usage(turn_usage, llm_event.usage)
                            total = self._conversation.total_usage
                            cumulative = self._add_usage(total, turn_usage)
                            yield Envelope.create(
                                "usage",
                                command.request_id,
                                sequence,
                                {
                                    "turn": self._usage(turn_usage),
                                    "cumulative": self._usage(cumulative),
                                },
                            )
                            sequence += 1
                        elif llm_event.type == LLMEventType.COMPLETE:
                            stop_reason = llm_event.stop_reason or "end_turn"

                    if not tool_calls:
                        break

                    result_blocks: list[APIContentBlock] = []
                    for call in tool_calls:
                        tool_started = perf_counter()
                        if api_pass == 1:
                            result = ToolResult(
                                "本章只支持一次工具往返，请等待 Agent Loop",
                                is_error=True,
                            )
                        elif call.tool_error:
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
                        yield Envelope.create(
                            "tool_result",
                            command.request_id,
                            sequence,
                            {
                                "id": call.tool_use_id,
                                "name": call.tool_name,
                                "content": result.content,
                                "is_error": result.is_error,
                                "duration_ms": round(
                                    (perf_counter() - tool_started) * 1000
                                ),
                                "metadata": dict(result.metadata),
                            },
                        )
                        sequence += 1

                    if api_pass == 1:
                        break
                    history = [
                        *history,
                        APIMessage("assistant", tuple(response_blocks)),
                        APIMessage("user", tuple(result_blocks)),
                    ]

                turn_index = 1 + sum(
                    message.role == "assistant"
                    and message.status is MessageStatus.COMPLETE
                    for message in self._conversation.messages
                )
                answer = "".join(chunks)
                if answer:
                    self._conversation.add_assistant(answer, usage=turn_usage)
                yield Envelope.create(
                    "turn_complete",
                    command.request_id,
                    sequence,
                    {
                        "turn_index": turn_index,
                        "stop_reason": stop_reason,
                        "duration_ms": round((perf_counter() - started_at) * 1000),
                        "model": self._llm.model_name,
                        "message_id": message_id,
                    },
                )
            except LLMClientError as error:
                self._remember_failed(chunks)
                yield self._error(
                    command.request_id,
                    error.code,
                    str(error),
                    scope="request",
                    retryable=error.retryable,
                    sequence=sequence,
                )
            except Exception:
                self._remember_failed(chunks)
                yield self._error(
                    command.request_id,
                    "llm_internal_error",
                    "模型客户端发生内部错误",
                    scope="request",
                    sequence=sequence,
                )

    def _remember_failed(self, chunks: list[str]) -> None:
        if chunks:
            self._conversation.add_assistant(
                "".join(chunks),
                status=MessageStatus.FAILED,
            )

    @staticmethod
    def _append_text(blocks: list[APIContentBlock], text: str) -> None:
        """合并相邻文本，但保留文本与工具请求的先后顺序。"""

        if blocks and isinstance(blocks[-1], APITextBlock):
            blocks[-1] = APITextBlock(blocks[-1].text + text)
        else:
            blocks.append(APITextBlock(text))

    @staticmethod
    def _add_usage(left: Usage, right: Usage) -> Usage:
        return Usage(
            left.input_tokens + right.input_tokens,
            left.output_tokens + right.output_tokens,
        )

    @staticmethod
    def _usage(usage: Usage) -> dict[str, int]:
        return {
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
        }

    @staticmethod
    def _error(
        request_id: str,
        code: str,
        message: str,
        *,
        scope: str,
        retryable: bool = False,
        sequence: int = 0,
    ) -> Envelope:
        return Envelope.create(
            "error",
            request_id,
            sequence,
            {
                "code": code,
                "message": message,
                "retryable": retryable,
                "scope": scope,
            },
        )
