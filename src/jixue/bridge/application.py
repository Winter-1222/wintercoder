"""把 Electron 命令翻译给 Agent，再把 Agent 事件包装成协议信封。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from jixue import __version__
from jixue.agent import Agent, AgentMode
from jixue.bridge.sessions import SessionController
from jixue.domain.conversation import Message
from jixue.domain.events import Envelope
from jixue.permission import PermissionMode
from jixue.sessions.store import collect_event


class BridgeApplication:
    """Bridge 只负责协议，不再包含 LLM 或工具执行细节。"""

    def __init__(self, agent: Agent, sessions: SessionController | None = None) -> None:
        self._agent = agent
        self._sessions = sessions
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
                    "permission_mode": self._agent.permission_mode.value,
                    "capabilities": [
                        "stream_text",
                        "tool_use",
                        "tool_result",
                        "usage",
                        "turn_complete",
                        "loop_complete",
                        "cancel",
                        "permission",
                        "permission_mode",
                        "mode",
                        "mcp_status",
                        "sessions",
                        "project_memory",
                    ],
                },
            )
        elif command.type.startswith("session."):
            if self._sessions is None:
                yield self._error(
                    command.request_id, "sessions_disabled", "会话存储未启用", scope="session"
                )
                return
            if self._chat_lock.locked():
                yield self._error(
                    command.request_id,
                    "session_busy",
                    "任务运行中不能加载或切换会话",
                    scope="session",
                )
                return
            async with self._chat_lock:
                try:
                    action = command.type.removeprefix("session.")
                    if action not in {"current", "new", "switch", "list"}:
                        raise ValueError("未知会话操作")
                    if action != "list":
                        session_id = command.payload.get("session_id", "")
                        if not isinstance(session_id, str):
                            raise ValueError("会话编号必须是字符串")
                        replay = await asyncio.to_thread(self._sessions.open, action, session_id)
                        self._agent = self._sessions.agent
                        yield Envelope.create("session.reset", command.request_id, 0, {})
                        for replay_event in replay:
                            yield replay_event
                    state = await asyncio.to_thread(self._sessions.state)
                    yield Envelope.create(
                        "session.loaded" if action != "list" else "session.list",
                        command.request_id,
                        1,
                        state,
                    )
                except (OSError, ValueError, KeyError, TypeError) as error:
                    yield self._error(
                        command.request_id, "session_error", str(error), scope="session"
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
            elif self._chat_lock.locked():
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
        elif command.type == "permission.mode":
            raw_mode = command.payload.get("mode")
            try:
                permission_mode = PermissionMode(raw_mode) if isinstance(raw_mode, str) else None
            except ValueError:
                permission_mode = None
            if permission_mode is None:
                yield self._error(
                    command.request_id,
                    "invalid_permission_mode",
                    "权限模式无效",
                    scope="permission_mode",
                )
            elif self._chat_lock.locked():
                yield self._error(
                    command.request_id,
                    "permission_mode_busy",
                    "任务运行中不能切换权限模式",
                    scope="permission_mode",
                )
            else:
                self._agent.set_permission_mode(permission_mode)
                yield Envelope.create(
                    "permission_mode.changed",
                    command.request_id,
                    0,
                    {"mode": permission_mode.value},
                )
        elif command.type == "chat.send":
            text = command.payload.get("text")
            user_text = text if isinstance(text, str) else ""
            if self._chat_lock.locked():
                yield self._error(
                    command.request_id, "agent_busy", "已有任务正在运行", scope="agent"
                )
                return
            async with self._chat_lock:
                self._active_request_id = command.request_id
                events: list[Envelope] = []
                terminal: list[Envelope] = []
                try:
                    if self._sessions:
                        await asyncio.to_thread(
                            self._sessions.store.start_turn,
                            self._sessions.session_id,
                            command.request_id,
                            user_text,
                        )
                    sequence = 0
                    async for event in self._agent.run(user_text):
                        envelope = Envelope.create(
                            event.type.value, command.request_id, sequence, event.payload
                        )
                        collect_event(events, envelope)
                        # 收尾事件必须在快照落盘之后发送，UI 才能放心开始下一次操作。
                        if event.type.value in {"loop_complete", "error"}:
                            terminal.append(envelope)
                        else:
                            yield envelope
                        sequence += 1
                    if self._sessions:
                        await asyncio.to_thread(
                            self._sessions.store.finish_turn,
                            self._sessions.session_id,
                            self._sessions.conversation,
                            events,
                        )
                    if self._sessions:
                        state = await asyncio.to_thread(self._sessions.state)
                        yield Envelope.create("session.list", command.request_id, sequence, state)
                    for envelope in terminal:
                        yield envelope
                except (OSError, ValueError, KeyError, TypeError) as error:
                    # 存盘失败明确显示；开始记录失败时不会执行模型或工具。
                    for envelope in terminal:
                        yield envelope
                    yield self._error(
                        command.request_id,
                        "session_save_failed",
                        f"会话保存失败：{error}",
                        scope="storage",
                    )
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
                accepted = target == self._active_request_id and self._agent.respond_permission(
                    tool_use_id, allow
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
