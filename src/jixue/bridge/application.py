"""一轮聊天的核心：历史 → LLM 流 → UI 事件。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from time import perf_counter
from uuid import uuid4

from jixue import __version__
from jixue.domain.conversation import (
    ConversationError,
    ConversationManager,
    Message,
    MessageStatus,
    Usage,
)
from jixue.domain.events import Envelope
from jixue.llm.base import LLMClient, LLMClientError, LLMEventType
from jixue.tools import ToolRegistry


class BridgeApplication:
    """不处理 stdin/stdout，只处理握手和聊天业务。"""

    def __init__(
        self,
        llm: LLMClient,
        conversation: ConversationManager | None = None,
        tools: ToolRegistry | None = None,
    ) -> None:
        self._llm = llm
        self._conversation = conversation or ConversationManager()
        self._tools = tools or ToolRegistry()
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
                    "capabilities": ["stream_text", "tool_use", "usage"],
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
                async for llm_event in self._llm.stream(
                    history,
                    self._tools.to_api_format(),
                ):
                    if llm_event.type == LLMEventType.TEXT:
                        chunks.append(llm_event.text)
                        yield Envelope.create(
                            "stream_text",
                            command.request_id,
                            sequence,
                            {"text": llm_event.text, "message_id": message_id},
                        )
                        sequence += 1
                    elif llm_event.type == LLMEventType.TOOL_USE:
                        payload: dict[str, object] = {
                            "id": llm_event.tool_use_id,
                            "name": llm_event.tool_name,
                            "input": dict(llm_event.tool_input),
                        }
                        if llm_event.tool_error:
                            payload["error"] = llm_event.tool_error
                        yield Envelope.create(
                            "tool_use",
                            command.request_id,
                            sequence,
                            payload,
                        )
                        sequence += 1
                    elif llm_event.type == LLMEventType.USAGE:
                        turn_usage = llm_event.usage
                        total = self._conversation.total_usage
                        cumulative = Usage(
                            total.input_tokens + turn_usage.input_tokens,
                            total.output_tokens + turn_usage.output_tokens,
                        )
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
                        turn_usage = llm_event.usage
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
                                "stop_reason": llm_event.stop_reason or "end_turn",
                                "duration_ms": round(
                                    (perf_counter() - started_at) * 1000
                                ),
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
