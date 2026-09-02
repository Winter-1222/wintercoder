"""把 Electron 命令翻译给 Agent，再把 Agent 事件包装成协议信封。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from jixue import __version__
from jixue.agent import Agent
from jixue.domain.conversation import Message
from jixue.domain.events import Envelope


class BridgeApplication:
    """Bridge 只负责协议，不再包含 LLM 或工具执行细节。"""

    def __init__(self, agent: Agent) -> None:
        self._agent = agent
        # 同一会话一次只处理一条消息，避免两条历史交叉写入。
        self._chat_lock = asyncio.Lock()
        self._active_request_id: str | None = None

    @property
    def messages(self) -> tuple[Message, ...]:
        """保留只读入口，方便本地测试检查 Agent 保存的历史。"""

        return self._agent.messages

    async def handle(self, command: Envelope) -> AsyncIterator[Envelope]:
        if command.type == "bridge.hello":
            yield Envelope.create(
                "bridge.ready",
                command.request_id,
                0,
                {
                    "protocol_version": command.version,
                    "backend_version": __version__,
                    "model": self._agent.model_name,
                    "capabilities": [
                        "stream_text",
                        "tool_use",
                        "tool_result",
                        "usage",
                        "turn_complete",
                        "loop_complete",
                        "cancel",
                    ],
                },
            )
        elif command.type == "chat.send":
            text = command.payload.get("text")
            user_text = text if isinstance(text, str) else ""
            async with self._chat_lock:
                self._active_request_id = command.request_id
                try:
                    sequence = 0
                    async for event in self._agent.run(user_text):
                        yield Envelope.create(
                            event.type.value,
                            command.request_id,
                            sequence,
                            event.payload,
                        )
                        sequence += 1
                finally:
                    self._active_request_id = None
        elif command.type == "chat.cancel":
            target = command.payload.get("target_request_id")
            target_request_id = target if isinstance(target, str) else ""
            accepted = target_request_id == self._active_request_id and self._agent.cancel()
            yield Envelope.create(
                "cancel.accepted",
                command.request_id,
                0,
                {
                    "target_request_id": target_request_id,
                    "accepted": accepted,
                },
            )
        else:
            yield self._error(
                command.request_id,
                "unknown_command",
                f"未知命令：{command.type}",
                scope="command",
            )

    @staticmethod
    def _error(
        request_id: str,
        code: str,
        message: str,
        *,
        scope: str,
    ) -> Envelope:
        return Envelope.create(
            "error",
            request_id,
            0,
            {
                "code": code,
                "message": message,
                "retryable": False,
                "scope": scope,
            },
        )
