"""集中注册、启用和导出工具定义。"""

from __future__ import annotations

from jixue.tools.base import Tool, ToolContext, ToolInput, ToolResult


class ToolRegistry:
    """Agent 只从这里查工具，不直接依赖具体工具文件。"""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self._enabled: set[str] = set()

    def register(self, tool: Tool, *, enabled: bool = True) -> None:
        name = tool.name()
        if name in self._tools:
            raise ValueError(f"工具已注册：{name}")
        self._tools[name] = tool
        if enabled:
            self._enabled.add(name)

    def set_enabled(self, name: str, enabled: bool) -> None:
        if name not in self._tools:
            raise KeyError(f"工具不存在：{name}")
        if enabled:
            self._enabled.add(name)
        else:
            self._enabled.discard(name)

    def get(self, name: str) -> Tool | None:
        """禁用工具对 Agent 来说等同于不存在。"""

        return self._tools.get(name) if name in self._enabled else None

    def enabled_tools(self) -> tuple[Tool, ...]:
        # 固定按名称排序，让每次 API 请求的工具顺序稳定，方便 Prompt Cache 命中。
        return tuple(self._tools[name] for name in sorted(self._enabled))

    async def execute(
        self,
        name: str,
        context: ToolContext,
        tool_input: ToolInput,
    ) -> ToolResult:
        """找不到或已禁用也返回 ToolResult，让模型有机会改正。"""

        tool = self.get(name)
        if tool is None:
            return ToolResult(f"工具不存在或已禁用：{name}", is_error=True)
        return await tool.execute(context, tool_input)

    def to_api_format(self) -> list[dict[str, object]]:
        """只输出通用 JSON；Anthropic SDK 类型仍留在适配器内部。"""

        return [
            {
                "name": tool.name(),
                "description": tool.description(),
                "input_schema": dict(tool.input_schema()),
            }
            for tool in self.enabled_tools()
        ]
