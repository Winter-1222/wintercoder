"""把 Electron 命令翻译给 Agent，再把 Agent 事件包装成协议信封。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from jixue import __version__
from jixue.agent import Agent, AgentMode
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
                    "mode": self._agent.mode.value,
                    "capabilities": [
                        "stream_text",
                        "tool_use",
                        "tool_result",
                        "usage",
                        "turn_complete",
                        "loop_complete",
                        "cancel",
                        "permission",
                        "mode",
                    ],
                },
            )
        elif command.type == "agent.mode":
            raw_mode = command.payload.get("mode")
            try:
                mode = AgentMode(raw_mode) if isinstance(raw_mode, str) else None
            except ValueError:
                mode = None
            if mode is None:
                yield self._error(
                    command.request_id,
                    "invalid_mode",
                    "模式只能是 plan 或 do",
                    scope="mode",
                )
            elif self._active_request_id is not None:
                yield self._error(
                    command.request_id,
                    "mode_busy",
                    "任务运行中不能切换模式",
                    scope="mode",
                )
            else:
                self._agent.set_mode(mode)
                yield Envelope.create(
                    "mode.changed",
                    command.request_id,
                    0,
                    {"mode": mode.value},
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
        elif command.type == "permission.respond":
            target = command.payload.get("target_request_id")
            tool_use_id = command.payload.get("tool_use_id")
            allow = command.payload.get("allow")
            if (
                not isinstance(target, str)
                or not isinstance(tool_use_id, str)
                or not isinstance(allow, bool)
            ):
                yield self._error(
                    command.request_id,
                    "invalid_permission_response",
                    "权限回复必须包含 target_request_id、tool_use_id 和布尔值 allow",
                    scope="permission",
                )
            else:
                # target_request_id 防止旧任务的确认按钮误操作当前任务；
                # tool_use_id 再精确到本次工具调用，两层都匹配才会唤醒 Agent。
                accepted = (
                    target == self._active_request_id
                    and self._agent.respond_permission(tool_use_id, allow)
                )
                yield Envelope.create(
                    "permission.resolved",
                    command.request_id,
                    0,
                    {
                        "target_request_id": target,
                        "tool_use_id": tool_use_id,
                        "allow": allow,
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
