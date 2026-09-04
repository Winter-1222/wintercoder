"""只按 ID 搜索或分页读取已经落盘的大工具结果。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import cast

from jixue.context import (
    DEFAULT_ARTIFACT_READ_CHARACTERS,
    MAX_ARTIFACT_READ_CHARACTERS,
    MAX_ARTIFACT_SEARCH_MATCHES,
    ToolResultStore,
)
from jixue.tools.base import BaseTool, ToolContext, ToolInput, ToolResult


def create_read_artifact_tool() -> BaseTool:
    """创建大结果专用读取工具；只认 artifact_id，不接受任意文件路径。"""

    def validate(tool_input: ToolInput) -> str | None:
        artifact_id = tool_input.get("artifact_id")
        offset = tool_input.get("offset", 0)
        limit = tool_input.get("limit", DEFAULT_ARTIFACT_READ_CHARACTERS)
        search = tool_input.get("search")
        if not isinstance(artifact_id, str) or not artifact_id.strip():
            return "artifact_id 必须是非空字符串"
        if type(offset) is not int or offset < 0:
            return "offset 必须是大于等于 0 的整数"
        if type(limit) is not int or not 200 <= limit <= MAX_ARTIFACT_READ_CHARACTERS:
            return f"limit 必须是 200 到 {MAX_ARTIFACT_READ_CHARACTERS} 之间的整数"
        if search is not None and (not isinstance(search, str) or not search):
            return "search 必须是非空字符串"
        return None

    async def read_artifact(context: ToolContext, tool_input: ToolInput) -> ToolResult:
        artifact_id = str(tool_input["artifact_id"]).strip()
        # BaseTool 已先执行 validate，这里的 cast 只是把这个事实告诉类型检查器。
        offset = cast(int, tool_input.get("offset", 0))
        limit = cast(int, tool_input.get("limit", DEFAULT_ARTIFACT_READ_CHARACTERS))
        search_value = tool_input.get("search")
        content, target = await ToolResultStore(context.project_root).read_text(artifact_id)

        if isinstance(search_value, str):
            # 大文件逐行搜索可能很慢，放在线程中才不会挡住 UI 的停止消息。
            return await asyncio.to_thread(
                _search_artifact,
                content,
                target,
                search_value,
                limit,
            )
        if offset >= len(content) and content:
            return ToolResult(
                f"offset 超出 artifact 范围；内容共 {len(content)} 个字符",
                is_error=True,
            )

        end = min(offset + limit, len(content))
        if end < len(content):
            # 提示文字也必须算进 limit。end 只能单向缩小，不能在 999/1000
            # 这样的位数边界来回跳动，否则工具会陷入死循环。
            while True:
                suffix = f"\n\n……还有内容，请从 offset={end} 继续读取。"
                overflow = end - offset + len(suffix) - limit
                if overflow <= 0:
                    break
                end = max(offset, end - overflow)
            visible = content[offset:end] + suffix
        else:
            visible = content[offset:end]
        return ToolResult(
            visible or "artifact 内容为空",
            metadata={
                "artifact_id": artifact_id,
                "artifact_path": str(target),
                "offset": offset,
                "next_offset": end if end < len(content) else None,
                "returned_characters": len(visible),
                "total_characters": len(content),
                "truncated": end < len(content),
            },
        )

    return BaseTool(
        tool_name="read_artifact",
        tool_description=(
            "按 artifact_id 读取过大的工具结果。优先用 search 查关键字；"
            "也可用 offset/limit 分段读取，单次最多返回 20000 个字符。"
        ),
        schema={
            "type": "object",
            "properties": {
                "artifact_id": {
                    "type": "string",
                    "description": "大工具结果预览中给出的 artifact_id",
                },
                "search": {
                    "type": "string",
                    "description": "可选，按原样文字搜索并返回命中行",
                },
                "offset": {
                    "type": "integer",
                    "minimum": 0,
                    "default": 0,
                    "description": "不搜索时，从第几个字符开始读取",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 200,
                    "maximum": MAX_ARTIFACT_READ_CHARACTERS,
                    "default": DEFAULT_ARTIFACT_READ_CHARACTERS,
                    "description": "本次最多返回多少字符",
                },
            },
            "required": ["artifact_id"],
            "additionalProperties": False,
        },
        handler=read_artifact,
        read_only=True,
        tool_category="file",
        validator=validate,
        # 当前实现会把一份 artifact 读入内存；串行执行避免并发复制多份大文本。
        concurrency_check=lambda _tool_input: False,
    )


def _search_artifact(content: str, target: Path, search: str, limit: int) -> ToolResult:
    """按字面文字搜索 artifact；结果仍受 limit 和最大命中数约束。"""

    matches: list[str] = []
    total_matches = 0
    for line_number, line in enumerate(content.splitlines(), start=1):
        if search not in line:
            continue
        total_matches += 1
        if len(matches) < MAX_ARTIFACT_SEARCH_MATCHES:
            matches.append(f"{line_number}: {line}")

    if not matches:
        return ToolResult(
            f"没有在 artifact 中找到：{search}",
            metadata={"artifact_path": str(target), "matches": 0},
        )

    visible = "\n".join(matches)
    truncated = total_matches > len(matches) or len(visible) > limit
    returned_matches = len(matches)
    if len(visible) > limit:
        suffix = "\n……搜索结果过长，已截断；请使用更具体的 search。"
        visible_body = visible[: max(0, limit - len(suffix))]
        # 最后一行即使只显示了一部分，也算一条“已展示”结果，但不能把后面的行算进去。
        returned_matches = visible_body.count("\n") + (1 if visible_body else 0)
        visible = visible_body + suffix
    return ToolResult(
        visible,
        metadata={
            "artifact_path": str(target),
            "matches": total_matches,
            "returned_matches": returned_matches,
            "returned_characters": len(visible),
            "truncated": truncated,
        },
    )
