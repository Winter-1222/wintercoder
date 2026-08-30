"""把协议命令转换成领域调用与业务事件。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from time import perf_counter
from typing import Any
from uuid import uuid4

from jixue import __version__
from jixue.domain.events import Envelope
from jixue.llm.base import LLMClient, LLMEventType


class BridgeApplication:
    """不依赖标准输入输出的 Bridge 应用核心，便于单元测试。"""

    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm

    async def handle(self, command: Envelope) -> AsyncIterator[Envelope]:
        """处理一条命令并产生零到多个事件。"""

        if command.type == "bridge.hello":
            yield Envelope.create(
                "bridge.ready",
                command.request_id,
                0,
                {
                    "protocol_version": command.version,
                    "backend_version": __version__,
                    "capabilities": ["fake_llm", "stream_text", "usage"],
                },
            )
            return

        if command.type == "chat.send":
            async for event in self._handle_chat(command):
                yield event
            return

        yield self._error(
            command.request_id,
            "unknown_command",
            f"未知命令：{command.type}",
            scope="command",
        )

    async def _handle_chat(self, command: Envelope) -> AsyncIterator[Envelope]:
        text = command.payload.get("text")
        if not isinstance(text, str) or not text.strip():
            yield self._error(
                command.request_id,
                "invalid_input",
                "消息文本不能为空",
                scope="request",
            )
            return

        sequence = 0
        message_id = f"msg_{uuid4().hex}"
        started_at = perf_counter()

        try:
            async for llm_event in self._llm.stream(text):
                if llm_event.type == LLMEventType.TEXT:
                    yield Envelope.create(
                        "stream_text",
                        command.request_id,
                        sequence,
                        {"text": llm_event.text, "message_id": message_id},
                    )
                    sequence += 1
                elif llm_event.type == LLMEventType.USAGE:
                    yield Envelope.create(
                        "usage",
                        command.request_id,
                        sequence,
                        {
                            "turn": self._usage_payload(llm_event.usage),
                            "cumulative": self._usage_payload(llm_event.usage),
                        },
                    )
                    sequence += 1
                elif llm_event.type == LLMEventType.COMPLETE:
                    yield Envelope.create(
                        "turn_complete",
                        command.request_id,
                        sequence,
                        {
                            "turn_index": 1,
                            "stop_reason": llm_event.stop_reason or "end_turn",
                            "duration_ms": round((perf_counter() - started_at) * 1000),
                            "model": self._llm.model_name,
                            "message_id": message_id,
                        },
                    )
        except Exception as exc:
            # FakeLLM 不应失败；这个边界保证未来适配器异常不会污染协议通道。
            yield self._error(
                command.request_id,
                "llm_stream_failed",
                f"模型流式响应失败：{exc}",
                scope="request",
                retryable=True,
                sequence=sequence,
            )

    @staticmethod
    def _usage_payload(usage: Any) -> dict[str, int]:
        return {
            "input_tokens": int(usage.input_tokens),
            "output_tokens": int(usage.output_tokens),
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

