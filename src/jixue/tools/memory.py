"""记忆读写也走 Registry、Plan/Do 和权限确认，不另开后台写入通道。"""

import asyncio
import re

from jixue.memory import KEY_PATTERN, MAX_ENTRY_CHARACTERS, MemoryStore
from jixue.tools.base import BaseTool, ToolContext, ToolInput, ToolResult


async def _read(context: ToolContext, _tool_input: ToolInput) -> ToolResult:
    return ToolResult(await asyncio.to_thread(MemoryStore(context.project_root).text))


def _validate(tool_input: ToolInput) -> str | None:
    action = tool_input.get("action")
    if not isinstance(action, str) or action not in {"remember", "forget"}:
        return "action 只能是 remember 或 forget"
    key = tool_input.get("key")
    if not isinstance(key, str) or not re.fullmatch(KEY_PATTERN, key):
        return "key 必须是 1—64 位英文、数字、下划线或短横线"
    content = tool_input.get("content", "")
    if not isinstance(content, str):
        return "content 必须是字符串"
    if (
        tool_input.get("action") == "remember"
        and not 0 < len(content.strip()) <= MAX_ENTRY_CHARACTERS
    ):
        return "remember 需要 1—1000 字符的 content"
    return None


async def _update(context: ToolContext, tool_input: ToolInput) -> ToolResult:
    store = MemoryStore(context.project_root)
    result = await asyncio.to_thread(
        store.update,
        str(tool_input["action"]),
        str(tool_input["key"]),
        str(tool_input.get("content", "")),
    )
    return ToolResult(result)


def create_read_memory_tool() -> BaseTool:
    return BaseTool(
        tool_name="read_memory",
        tool_description="读取项目长期记忆及条目键，用于核实或忘记。",
        schema={"type": "object", "properties": {}, "additionalProperties": False},
        handler=_read,
        read_only=True,
        tool_category="memory",
    )


def create_update_memory_tool() -> BaseTool:
    return BaseTool(
        tool_name="update_memory",
        tool_description=(
            "记住或忘记跨会话有效的项目事实、用户偏好。相同 key 更新原条目。"
            "只保存用户明确要求或已经确认的稳定信息；不保存密钥或临时任务进度。"
        ),
        schema={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["remember", "forget"]},
                "key": {"type": "string", "pattern": f"^{KEY_PATTERN}$"},
                "content": {"type": "string", "maxLength": MAX_ENTRY_CHARACTERS},
            },
            "required": ["action", "key"],
            "additionalProperties": False,
        },
        handler=_update,
        validator=_validate,
        destructive=True,
        tool_category="memory",
    )
