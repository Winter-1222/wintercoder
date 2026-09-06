"""组装可切换的会话；共享工具注册表和 MCP 连接，不共享对话消息。"""

from pathlib import Path

from jixue.agent import Agent
from jixue.domain.events import Envelope
from jixue.llm.base import LLMClient
from jixue.sessions.store import RestoredSession, SessionStore
from jixue.tools import ToolContext, ToolRegistry


class SessionController:
    def __init__(self, llm: LLMClient, tools: ToolRegistry, project_root: Path) -> None:
        self.store = SessionStore(project_root)
        self._llm = llm
        self._tools = tools
        self._root = project_root
        self._activate(self.store.current())

    def _activate(self, restored: RestoredSession) -> None:
        self.session_id = restored.session_id
        self.conversation = restored.conversation
        self.agent = Agent(
            self._llm,
            conversation=self.conversation,
            tools=self._tools,
            tool_context=ToolContext(self._root),
        )

    def open(self, action: str, session_id: str = "") -> list[Envelope]:
        if action == "new":
            restored = self.store.create()
        else:
            restored = self.store.load(session_id if action == "switch" else self.session_id)
        # 读取及校验成功才更新当前指针和 Agent；损坏会话不会覆盖现有上下文。
        self.store.select(restored.session_id)
        self._activate(restored)
        return restored.events

    def state(self) -> dict[str, object]:
        usage = self.conversation.total_usage
        return {
            "session_id": self.session_id,
            "sessions": self.store.list_sessions(),
            "model": self.agent.model_name,
            "mode": self.agent.mode.value,
            "permission_mode": self.agent.permission_mode.value,
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "completed_turns": self.conversation.completed_turns,
        }
