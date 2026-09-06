"""Agent 组装入口：连接运行控制、模型流、工具执行、上下文压缩和任务循环。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

from jixue.agent_runtime.compaction import ContextCompactor
from jixue.agent_runtime.control import RunControl
from jixue.agent_runtime.events import AgentEvent, AgentEventType, AgentMode, error_event
from jixue.agent_runtime.execution import ToolExecutor
from jixue.agent_runtime.loop import AgentLoop
from jixue.agent_runtime.model import ModelStream
from jixue.context import AUTO_COMPACTION_TRIGGER_CHARACTERS
from jixue.domain.conversation import ConversationManager, Message
from jixue.llm.base import LLMClient
from jixue.permission import PermissionMode
from jixue.prompt import build_system_prompt
from jixue.tools import ToolContext, ToolRegistry

# Bridge 和调用方继续从同一个入口导入公共类型。
__all__ = ["Agent", "AgentEvent", "AgentEventType", "AgentMode"]


class Agent:
    """组装具体组件并提供对外入口，执行细节见 agent_runtime 各模块。"""

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
        conversation = conversation or ConversationManager()
        tools = tools or ToolRegistry()
        tool_context = tool_context or ToolContext(Path.cwd().resolve())

        self._project_root = tool_context.project_root
        self._conversation = conversation
        self._control = RunControl()
        self._model = ModelStream(
            llm, self._control, build_system_prompt(tool_context.project_root)
        )
        self._executor = ToolExecutor(tools, tool_context, self._control)
        self._compactor = ContextCompactor(conversation, self._model, self._control)
        self._loop = AgentLoop(
            conversation=conversation,
            model=self._model,
            executor=self._executor,
            compactor=self._compactor,
            control=self._control,
            tools=tools,
            project_root=tool_context.project_root,
            max_iterations=max_iterations,
            auto_compaction_trigger_characters=auto_compaction_trigger_characters,
        )

    @property
    def model_name(self) -> str:
        return self._model.model_name

    @property
    def messages(self) -> tuple[Message, ...]:
        return self._conversation.messages

    @property
    def mode(self) -> AgentMode:
        return self._control.mode

    def set_mode(self, mode: AgentMode) -> None:
        self._control.set_mode(mode)

    @property
    def permission_mode(self) -> PermissionMode:
        return self._control.permission_mode

    def set_permission_mode(self, mode: PermissionMode) -> None:
        self._control.set_permission_mode(mode)

    def cancel(self) -> bool:
        return self._control.cancel()

    def respond_permission(self, tool_use_id: str, allow: bool) -> bool:
        return self._control.respond_permission(tool_use_id, allow)

    async def run(self, user_text: str) -> AsyncIterator[AgentEvent]:
        """统一管理任务生命周期，普通任务和压缩命令分别交给对应组件。"""

        text = user_text.strip()
        if not text:
            yield error_event("invalid_input", "消息文本不能为空")
            return
        if self._control.is_running:
            yield error_event("agent_busy", "已有任务正在运行")
            return
        self._control.begin()
        try:
            self._model.set_system_prompt(
                await asyncio.to_thread(build_system_prompt, self._project_root)
            )
            stream = self._compactor.run_manual() if text == "/compact" else self._loop.run(text)
            async for item in stream:
                yield item
        finally:
            self._control.finish()
