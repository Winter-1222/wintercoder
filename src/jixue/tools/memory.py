"""模型看索引决定读哪条；记忆读写统一经过模式、权限与执行器。"""

import asyncio

from jixue.memory import MemoryStore
from jixue.memory_format import (
    MAX_DESCRIPTION_CHARACTERS,
    MAX_ENTRY_CHARACTERS,
    MEMORY_TYPES,
    NAME_PATTERN,
    validate_content,
    validate_metadata,
    validate_name,
)
from jixue.tools.base import BaseTool, ToolContext, ToolInput, ToolResult


def _validate_read(tool_input: ToolInput) -> str | None:
    if set(tool_input) - {"name"}:
        return "read_memory 只接受可选的 name"
    if "name" in tool_input:
        name = tool_input["name"]
        if not isinstance(name, str):
            return "name 必须是字符串"
        try:
            validate_name(name)
        except ValueError as error:
            return str(error)
    return None


def _validate_update(tool_input: ToolInput) -> str | None:
    action = tool_input.get("action")
    if not isinstance(action, str) or action not in {"remember", "forget", "rebuild_index"}:
        return "action 只允许 remember、forget 或 rebuild_index"
    if action == "rebuild_index":
        return None if set(tool_input) == {"action"} else "rebuild_index 只接受 action"
    allowed = {"action", "name", "description", "type", "content"}
    if set(tool_input) - allowed:
        return "记忆参数包含未知字段；使用 name 标识记忆"
    required = ["name"] + (["description", "type", "content"] if action == "remember" else [])
    if any(not isinstance(tool_input.get(key), str) for key in required):
        return "remember 需要 name、description、type、content；forget 需要 name"
    try:
        validate_name(str(tool_input["name"]))
        if action == "remember":
            validate_metadata(
                str(tool_input["name"]), str(tool_input["description"]), str(tool_input["type"])
            )
            validate_content(str(tool_input["content"]))
    except ValueError as error:
        return str(error)
    return None


async def _read(context: ToolContext, tool_input: ToolInput) -> ToolResult:
    store = MemoryStore(context.project_root)
    if "name" in tool_input:
        return ToolResult(await asyncio.to_thread(store.read, str(tool_input["name"])))
    return ToolResult(await asyncio.to_thread(store.index))


async def _update(context: ToolContext, tool_input: ToolInput) -> ToolResult:
    store = MemoryStore(context.project_root)
    if tool_input["action"] == "rebuild_index":
        return ToolResult(await asyncio.to_thread(store.rebuild_index))
    name = str(tool_input["name"])
    if tool_input["action"] == "forget":
        result = await asyncio.to_thread(store.forget, name)
    else:
        result = await asyncio.to_thread(
            store.remember,
            name,
            str(tool_input["description"]),
            str(tool_input["type"]),
            str(tool_input["content"]),
        )
    return ToolResult(result)


def create_read_memory_tool() -> BaseTool:
    return BaseTool(
        tool_name="read_memory",
        tool_description="不传 name 时列出索引；传索引中的 name 时仅加载那条记忆的完整正文。",
        schema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "pattern": f"^{NAME_PATTERN}$"},
            },
            "additionalProperties": False,
        },
        handler=_read,
        validator=_validate_read,
        read_only=True,
        tool_category="memory",
    )


def create_update_memory_tool() -> BaseTool:
    return BaseTool(
        tool_name="update_memory",
        tool_description=(
            "记住或忘记长期记忆并更新索引。同名更新；remember 必须提供 name、description、type、"
            "content。只保存用户明确要求或确认、跨会话有用的信息，"
            "不存密钥、猜测、临时执行进度或项目指令中已有的内容。"
            "手工编辑记忆文件或索引异常时，传 action=rebuild_index 重建索引，无需其他参数。"
        ),
        schema={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["remember", "forget", "rebuild_index"]},
                "name": {"type": "string", "pattern": f"^{NAME_PATTERN}$"},
                "description": {"type": "string", "maxLength": MAX_DESCRIPTION_CHARACTERS},
                "type": {"type": "string", "enum": list(MEMORY_TYPES)},
                "content": {"type": "string", "maxLength": MAX_ENTRY_CHARACTERS},
            },
            "required": ["action"],
            "additionalProperties": False,
        },
        handler=_update,
        validator=_validate_update,
        destructive=True,
        tool_category="memory",
    )
