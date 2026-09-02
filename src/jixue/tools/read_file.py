"""第一个内置工具：读取项目目录中的 UTF-8 文本文件。"""

from __future__ import annotations

import asyncio

from jixue.tools.base import BaseTool, ToolContext, ToolInput, ToolResult


def create_read_file_tool() -> BaseTool:
    """返回已填好名称、Schema、权限标记和执行函数的基础工具。"""

    def validate(tool_input: ToolInput) -> str | None:
        path = tool_input.get("path")
        if not isinstance(path, str) or not path.strip():
            return "path 必须是非空字符串"
        return None

    async def read_file(context: ToolContext, tool_input: ToolInput) -> ToolResult:
        path = str(tool_input["path"]).strip()
        root = context.project_root.resolve()
        target = (root / path).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            return ToolResult("拒绝读取项目目录之外的文件", is_error=True)
        if not target.is_file():
            return ToolResult(f"文件不存在：{path}", is_error=True)

        # 文件读取是同步操作，放进工作线程后多个只读工具才能真正重叠执行。
        content = await asyncio.to_thread(target.read_text, encoding="utf-8")
        return ToolResult(
            content,
            metadata={"path": str(target), "characters": len(content)},
        )

    return BaseTool(
        tool_name="read_file",
        tool_description="读取项目目录内的 UTF-8 文本文件；需要查看代码或配置内容时使用。",
        schema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "相对于项目根目录的文件路径",
                }
            },
            "required": ["path"],
            "additionalProperties": False,
        },
        handler=read_file,
        read_only=True,
        tool_category="file",
        validator=validate,
        concurrency_check=lambda _tool_input: True,
    )
