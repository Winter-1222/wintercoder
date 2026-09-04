"""读取普通项目文件，并支持按行号只取需要的部分。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import cast

from jixue.tools.base import BaseTool, ToolContext, ToolInput, ToolResult

MAX_READ_LINES = 500


def create_read_file_tool() -> BaseTool:
    """创建普通文件读取工具；不允许绕过专用工具直接读取运行数据。"""

    def validate(tool_input: ToolInput) -> str | None:
        path = tool_input.get("path")
        if not isinstance(path, str) or not path.strip():
            return "path 必须是非空字符串"
        start_line = tool_input.get("start_line", 1)
        end_line = tool_input.get("end_line")
        if type(start_line) is not int or start_line < 1:
            return "start_line 必须是大于 0 的整数"
        if end_line is not None and (type(end_line) is not int or end_line < start_line):
            return "end_line 必须是不小于 start_line 的整数"
        if end_line is not None and end_line - start_line + 1 > MAX_READ_LINES:
            return f"一次最多读取 {MAX_READ_LINES} 行，请缩小行号范围"
        return None

    async def read_file(context: ToolContext, tool_input: ToolInput) -> ToolResult:
        path = str(tool_input["path"]).strip()
        root = context.project_root.resolve()
        target = (root / path).resolve()
        try:
            relative = target.relative_to(root)
        except ValueError:
            return ToolResult("拒绝读取项目目录之外的文件", is_error=True)
        if _is_private_path(relative.parts):
            return ToolResult("拒绝读取密钥或霁雪运行数据，请使用专用工具", is_error=True)
        if not target.is_file():
            return ToolResult(f"文件不存在：{path}", is_error=True)

        # BaseTool 已先执行 validate，这里的 cast 只是把这个事实告诉类型检查器。
        start_line = cast(int, tool_input.get("start_line", 1))
        requested_end = cast(int | None, tool_input.get("end_line"))
        explicit_range = "start_line" in tool_input or "end_line" in tool_input
        # 读取和分行都可能处理大文本，一起放进线程，UI 才能及时处理停止消息。
        return await asyncio.to_thread(
            _read_file_content,
            target,
            start_line,
            requested_end,
            explicit_range,
        )

    return BaseTool(
        tool_name="read_file",
        tool_description=(
            "读取项目目录内的 UTF-8 文本文件；大文件应使用 start_line/end_line 分段读取，"
            "每次最多 500 行。不能用它读取 .env 或 .jixue 运行数据。"
        ),
        schema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "相对于项目根目录的文件路径",
                },
                "start_line": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "可选，开始行号，从 1 开始",
                },
                "end_line": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "可选，结束行号（包含）；一次最多读取 500 行",
                },
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


def _read_file_content(
    target: Path,
    start_line: int,
    requested_end: int | None,
    explicit_range: bool,
) -> ToolResult:
    """在线程中读取并切片，避免大文本分行时卡住 Agent 事件循环。"""

    content = target.read_text(encoding="utf-8")
    lines = content.splitlines(keepends=True)

    if explicit_range:
        if start_line > len(lines):
            return ToolResult(
                f"start_line 超出文件范围；文件共 {len(lines)} 行",
                is_error=True,
            )
        end_line = (
            min(start_line + MAX_READ_LINES - 1, len(lines))
            if requested_end is None
            else min(requested_end, len(lines))
        )
        visible = "".join(lines[start_line - 1 : end_line])
        truncated = start_line > 1 or end_line < len(lines)
        if truncated:
            visible += (
                f"\n……文件共 {len(lines)} 行，本次显示第 {start_line}-{end_line} 行。"
                "请继续指定 start_line/end_line 读取。"
            )
    else:
        # 保持第一章以来的兼容行为：不传行号时读取全文。
        # 若全文过大，Agent 的统一 ToolResultStore 会把它落盘并只发送预览。
        visible = content
        end_line = len(lines)
        truncated = False

    return ToolResult(
        visible,
        metadata={
            "path": str(target),
            "characters": len(visible),
            "total_characters": len(content),
            "start_line": start_line,
            "end_line": end_line,
            "total_lines": len(lines),
            "truncated": truncated,
        },
    )


def _is_private_path(parts: tuple[str, ...]) -> bool:
    """密钥和内部运行数据只能由对应组件访问，不能作为普通文件喂给模型。"""

    lowered = (part.casefold() for part in parts)
    return any(part == ".jixue" or part == ".env" or part.startswith(".env.") for part in lowered)
