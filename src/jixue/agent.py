"""Agent 组装入口：连接运行控制、模型流、工具执行、上下文压缩和任务循环。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Sequence
from pathlib import Path
from uuid import uuid4

from jixue.agent_runtime.compaction import ContextCompactor
from jixue.agent_runtime.control import RunControl
from jixue.agent_runtime.events import AgentEvent, AgentEventType, AgentMode, error_event
from jixue.agent_runtime.execution import ToolExecutor
from jixue.agent_runtime.loop import AgentLoop
from jixue.agent_runtime.model import ModelStream, RequestSnapshot
from jixue.context import AUTO_COMPACTION_TRIGGER_CHARACTERS
from jixue.domain.conversation import ConversationManager, Message
from jixue.llm.base import LLMClient, ToolDefinition
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
        system_prompt: str | None = None,
        prompt_suffix: Callable[[], str] | None = None,
        reminder_override: str | None = None,
        tool_definitions: Sequence[ToolDefinition] | None = None,
        blocked_tools: frozenset[str] = frozenset(),
        preserve_message_boundary: bool = False,
    ) -> None:
        if max_iterations < 1:
            raise ValueError("最大循环轮数必须大于 0")
        if auto_compaction_trigger_characters < 1:
            raise ValueError("自动压缩触发线必须大于 0")
        conversation = conversation or ConversationManager()
        tools = tools or ToolRegistry()
        tool_context = tool_context or ToolContext(Path.cwd().resolve())

        self._fixed_system = system_prompt
        self._prompt_suffix = prompt_suffix
        self.request_id = ""
        self._project_root = tool_context.project_root
        self._conversation = conversation
        self._control = RunControl()
        self._model = ModelStream(
            llm,
            self._control,
            system_prompt
            if system_prompt is not None
            else build_system_prompt(tool_context.project_root),
        )
        self._tools = tools
        self._executor = ToolExecutor(tools, tool_context, self._control, blocked_tools)
        self._compactor = ContextCompactor(
            conversation, self._model, self._control,
            preserve_message_boundary=preserve_message_boundary,
        )
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
            reminder_override=reminder_override,
            tool_definitions=tool_definitions,
        )

    @property
    def is_running(self) -> bool:
        return self._control.is_running

    def register_available_tools(self, tools: ToolRegistry) -> None:
        for tool in tools.enabled_tools():
            if self._tools.get(tool.name()) is None:
                self._tools.register(tool)

    @property
    def request_snapshot(self) -> RequestSnapshot:
        if self._model.last_request is None:
            raise ValueError("父 Agent 尚未请求模型，无法 Fork")
        return self._model.last_request

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

    async def run(self, user_text: str, *, request_id: str = "") -> AsyncIterator[AgentEvent]:
        """统一管理任务生命周期，普通任务和压缩命令分别交给对应组件。"""

        text = user_text.strip()
        if not text:
            yield error_event("invalid_input", "消息文本不能为空")
            return
        if self._control.is_running:
            yield error_event("agent_busy", "已有任务正在运行")
            return
        self.request_id = request_id or f"req_{uuid4().hex}"
        self._control.begin()
        try:
            system = self._fixed_system
            if system is None:
                system = await asyncio.to_thread(build_system_prompt, self._project_root)
            if self._prompt_suffix:
                system += await asyncio.to_thread(self._prompt_suffix)
            self._model.set_system_prompt(system)
            stream = self._compactor.run_manual() if text == "/compact" else self._loop.run(text)
            async for item in stream:
                yield item
        finally:
            self._control.finish()
