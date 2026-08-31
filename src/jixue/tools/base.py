"""工具的公共合同：定义、校验、执行和结果。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

type ToolInput = Mapping[str, object]
type ToolHandler = Callable[["ToolContext", ToolInput], Awaitable["ToolResult"]]
type ToolValidator = Callable[[ToolInput], str | None]
type ConcurrencyCheck = Callable[[ToolInput], bool]


@dataclass(frozen=True, slots=True)
class ToolContext:
    """执行工具时由 Agent 提供的环境；现在只需要项目根目录。"""

    project_root: Path


@dataclass(frozen=True, slots=True)
class ToolResult:
    """工具失败也是正常结果；metadata 只给 UI，不发送给模型。"""

    content: str
    is_error: bool = False
    metadata: Mapping[str, object] = field(default_factory=dict)


class Tool(Protocol):
    """所有工具必须提供的最小接口。"""

    def name(self) -> str: ...

    def description(self) -> str: ...

    def input_schema(self) -> Mapping[str, object]: ...

    async def execute(self, context: ToolContext, tool_input: ToolInput) -> ToolResult: ...

    def is_read_only(self) -> bool: ...

    def is_destructive(self) -> bool: ...

    def is_concurrency_safe(self, tool_input: ToolInput) -> bool: ...

    def category(self) -> str: ...

    def validate_input(self, tool_input: ToolInput) -> str | None: ...


def _valid_input(_tool_input: ToolInput) -> str | None:
    return None


def _serial_only(_tool_input: ToolInput) -> bool:
    return False


@dataclass(slots=True)
class BaseTool:
    """保存通用字段，并把可修复失败包装成 ToolResult。"""

    tool_name: str
    tool_description: str
    schema: Mapping[str, object]
    handler: ToolHandler
    read_only: bool = False
    destructive: bool = False
    tool_category: str = "other"
    validator: ToolValidator = _valid_input
    concurrency_check: ConcurrencyCheck = _serial_only

    def name(self) -> str:
        return self.tool_name

    def description(self) -> str:
        return self.tool_description

    def input_schema(self) -> Mapping[str, object]:
        return self.schema

    async def execute(self, context: ToolContext, tool_input: ToolInput) -> ToolResult:
        # 参数错误交给模型修正，不能让一次错误结束整个 Agent。
        error = self.validate_input(tool_input)
        if error:
            return ToolResult(error, is_error=True)
        try:
            return await self.handler(context, tool_input)
        except (OSError, ValueError, RuntimeError) as error:
            return ToolResult(str(error), is_error=True)

    def is_read_only(self) -> bool:
        return self.read_only

    def is_destructive(self) -> bool:
        return self.destructive

    def is_concurrency_safe(self, tool_input: ToolInput) -> bool:
        return self.concurrency_check(tool_input)

    def category(self) -> str:
        return self.tool_category

    def validate_input(self, tool_input: ToolInput) -> str | None:
        return self.validator(tool_input)
