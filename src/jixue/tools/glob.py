"""按路径模式查找项目中的文件。"""

from __future__ import annotations

import asyncio
from fnmatch import fnmatchcase
from os import walk
from pathlib import Path

from jixue.tools.base import BaseTool, ToolContext, ToolInput, ToolResult

MAX_MATCHES = 200
SKIPPED_NAMES = {".git", ".env", "node_modules", "out", "__pycache__"}


def _matches(path: tuple[str, ...], pattern: tuple[str, ...]) -> bool:
    """逐段匹配路径；** 可以跨越零个或多个目录。"""

    if not pattern:
        return not path
    if pattern[0] == "**":
        return _matches(path, pattern[1:]) or bool(path) and _matches(path[1:], pattern)
    return bool(path) and fnmatchcase(path[0], pattern[0]) and _matches(path[1:], pattern[1:])


def _find_matches(root: Path, pattern: tuple[str, ...]) -> list[str]:
    """在线程中遍历磁盘，返回数量受限的相对路径。"""

    matches: list[str] = []
    for directory, names, files in walk(root):
        # 在遍历阶段剪掉依赖目录，避免先扫描整个 node_modules 再过滤。
        names[:] = sorted(name for name in names if name not in SKIPPED_NAMES)
        for name in sorted(files):
            if name in SKIPPED_NAMES:
                continue
            target = Path(directory) / name
            relative = target.relative_to(root)
            if not _matches(relative.parts, pattern):
                continue
            try:
                target.resolve().relative_to(root)
            except ValueError:
                continue
            matches.append(relative.as_posix())
            if len(matches) > MAX_MATCHES:
                return matches
    return matches


def create_glob_tool() -> BaseTool:
    """创建只返回文件路径、不读取文件内容的 glob 工具。"""

    def validate(tool_input: ToolInput) -> str | None:
        pattern = tool_input.get("pattern")
        if not isinstance(pattern, str) or not pattern.strip():
            return "pattern 必须是非空字符串"
        return None

    async def glob_files(context: ToolContext, tool_input: ToolInput) -> ToolResult:
        pattern = str(tool_input["pattern"]).strip()
        pattern_path = Path(pattern)
        # glob 接收相对模式；提前拒绝绝对路径和 ..，避免搜索项目外部。
        if pattern_path.is_absolute() or ".." in pattern_path.parts:
            return ToolResult("拒绝搜索项目目录之外的文件", is_error=True)

        root = context.project_root.resolve()
        matches = await asyncio.to_thread(_find_matches, root, pattern_path.parts)

        truncated = len(matches) > MAX_MATCHES
        visible = matches[:MAX_MATCHES]
        if not visible:
            return ToolResult("没有找到匹配文件", metadata={"matches": 0})

        content = "\n".join(visible)
        if truncated:
            content += f"\n……结果过多，只显示前 {MAX_MATCHES} 个文件"
        return ToolResult(
            content,
            metadata={"matches": len(visible), "truncated": truncated},
        )

    return BaseTool(
        tool_name="glob",
        tool_description="按 glob 模式查找项目内文件，例如 **/*.py；只返回路径。",
        schema={
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "相对于项目根目录的 glob 模式，例如 src/**/*.py",
                }
            },
            "required": ["pattern"],
            "additionalProperties": False,
        },
        handler=glob_files,
        read_only=True,
        tool_category="search",
        concurrency_check=lambda _tool_input: True,
    )
