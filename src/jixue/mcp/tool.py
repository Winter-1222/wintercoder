"""把任意 MCP transport 的工具包装成霁雪 Tool。"""

from __future__ import annotations

import re
from collections.abc import Mapping

from jixue.mcp.client import MCPToolDefinition, MCPTransport
from jixue.tools import ToolContext, ToolInput, ToolResult


class MCPToolWrapper:
    """适配 MCP 工具；Agent 不需要知道工具来自本地函数还是外部 Server。"""

    def __init__(self, transport: MCPTransport, definition: MCPToolDefinition) -> None:
        self._transport = transport
        self._definition = definition
        safe_server = _safe_name(transport.server_name)
        safe_tool = _safe_name(definition.name)
        self._name = f"mcp__{safe_server}__{safe_tool}"

    def name(self) -> str:
        return self._name

    def description(self) -> str:
        return f"来自 MCP Server {self._transport.server_name}。{self._definition.description}"

    def input_schema(self) -> Mapping[str, object]:
        return self._definition.input_schema

    async def execute(self, context: ToolContext, tool_input: ToolInput) -> ToolResult:
        del context  # 工作目录由 transport 建立连接时固定，这里不再重复传递。
        error = self.validate_input(tool_input)
        if error:
            return ToolResult(error, is_error=True)
        try:
            result = await self._transport.call_tool(self._definition.name, tool_input)
        except MemoryError:
            # 内存不足属于系统级故障，不伪装成普通工具失败。
            raise
        except Exception as error:
            # 连接断开、Server 报错等是模型可利用的反馈，不中断整个 Agent Loop。
            return ToolResult(f"MCP 工具调用失败：{error}", is_error=True)
        return ToolResult(result.content, result.is_error, result.metadata)

    def is_read_only(self) -> bool:
        return self._definition.read_only

    def is_destructive(self) -> bool:
        return self._definition.destructive

    def is_concurrency_safe(self, tool_input: ToolInput) -> bool:
        del tool_input
        # MCP 标准没有“并发安全”声明；即使只读，Server 内部也可能不支持并发。
        return False

    def category(self) -> str:
        return "mcp"

    def validate_input(self, tool_input: ToolInput) -> str | None:
        """先检查通用 schema 的必填项，详细类型仍由 MCP Server 校验。"""

        required = self._definition.input_schema.get("required", [])
        if not isinstance(required, list):
            return "MCP 工具的 required schema 不是数组"
        missing = [name for name in required if isinstance(name, str) and name not in tool_input]
        if missing:
            return f"缺少必填参数：{', '.join(missing)}"
        return None


def _safe_name(name: str) -> str:
    """API 工具名只保留简单字符；远端调用仍使用原始名称。"""

    return re.sub(r"[^A-Za-z0-9_-]", "_", name)
