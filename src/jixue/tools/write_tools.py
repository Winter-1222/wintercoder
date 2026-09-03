"""项目内写文件和精确编辑工具。"""

from __future__ import annotations

import asyncio

from jixue.permission import resolve_project_path
from jixue.tools.base import BaseTool, ToolContext, ToolInput, ToolResult

MAX_CONTENT_CHARACTERS = 1_000_000


def create_write_file_tool() -> BaseTool:
    """创建“写入完整文件”工具；已有内容会被整体替换。"""

    def validate(tool_input: ToolInput) -> str | None:
        path = tool_input.get("path")
        content = tool_input.get("content")
        if not isinstance(path, str) or not path.strip():
            return "path 必须是非空字符串"
        if not isinstance(content, str):
            return "content 必须是字符串"
        if len(content) > MAX_CONTENT_CHARACTERS:
            return f"content 不能超过 {MAX_CONTENT_CHARACTERS} 个字符"
        return None

    async def write_file(context: ToolContext, tool_input: ToolInput) -> ToolResult:
        path = str(tool_input["path"]).strip()
        content = str(tool_input["content"])
        target = resolve_project_path(context.project_root, path)
        existed = target.exists()

        def write() -> None:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

        # 磁盘写入放到线程，避免大文件短暂卡住 Agent 的事件循环。
        await asyncio.to_thread(write)
        return ToolResult(
            f"已{'覆盖' if existed else '创建'}文件：{path}",
            metadata={
                "path": str(target),
                "characters": len(content),
                "created": not existed,
            },
        )

    return BaseTool(
        tool_name="write_file",
        tool_description="创建或整体覆盖项目内 UTF-8 文本文件；局部修改优先使用 edit_file。",
        schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "项目内相对路径"},
                "content": {"type": "string", "description": "要写入的完整文件内容"},
            },
            "required": ["path", "content"],
            "additionalProperties": False,
        },
        handler=write_file,
        destructive=True,
        tool_category="file",
        validator=validate,
    )


def create_edit_file_tool() -> BaseTool:
    """创建精确替换工具；旧文字必须在文件中只出现一次。"""

    def validate(tool_input: ToolInput) -> str | None:
        path = tool_input.get("path")
        old_text = tool_input.get("old_text")
        new_text = tool_input.get("new_text")
        if not isinstance(path, str) or not path.strip():
            return "path 必须是非空字符串"
        if not isinstance(old_text, str) or not old_text:
            return "old_text 必须是非空字符串"
        if not isinstance(new_text, str):
            return "new_text 必须是字符串"
        if old_text == new_text:
            return "old_text 和 new_text 不能相同"
        return None

    async def edit_file(context: ToolContext, tool_input: ToolInput) -> ToolResult:
        path = str(tool_input["path"]).strip()
        old_text = str(tool_input["old_text"])
        new_text = str(tool_input["new_text"])
        target = resolve_project_path(context.project_root, path)
        if not target.is_file():
            return ToolResult(f"文件不存在：{path}", is_error=True)

        content = await asyncio.to_thread(target.read_text, encoding="utf-8")
        matches = content.count(old_text)
        if matches == 0:
            return ToolResult("没有找到 old_text，文件未修改", is_error=True)
        if matches > 1:
            return ToolResult(f"old_text 出现了 {matches} 次，请提供更多上下文", is_error=True)

        updated = content.replace(old_text, new_text, 1)
        await asyncio.to_thread(target.write_text, updated, encoding="utf-8")
        return ToolResult(
            f"已修改文件：{path}",
            metadata={
                "path": str(target),
                "characters": len(updated),
                "replacements": 1,
            },
        )

    return BaseTool(
        tool_name="edit_file",
        tool_description="精确替换项目文件中唯一一段旧文字；适合局部修改，匹配不唯一时不会写入。",
        schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "项目内相对路径"},
                "old_text": {"type": "string", "description": "必须唯一匹配的原文字"},
                "new_text": {"type": "string", "description": "替换后的文字"},
            },
            "required": ["path", "old_text", "new_text"],
            "additionalProperties": False,
        },
        handler=edit_file,
        destructive=True,
        tool_category="file",
        validator=validate,
    )
