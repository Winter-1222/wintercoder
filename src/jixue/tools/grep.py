"""在项目文本文件中搜索指定文字。"""

from __future__ import annotations

from collections.abc import Iterator
from os import walk
from pathlib import Path

from jixue.tools.base import BaseTool, ToolContext, ToolInput, ToolResult

MAX_RESULTS = 100
MAX_LINE_LENGTH = 300
SKIPPED_NAMES = {".git", ".env", "node_modules", "out", "__pycache__"}


def _iter_files(target: Path) -> Iterator[Path]:
    """遍历源码文件，并跳过依赖、构建产物和敏感配置目录。"""

    if target.is_file():
        yield target
        return
    for directory, names, files in walk(target):
        # 排序让相同输入得到稳定结果，模型和测试都更容易复盘。
        names[:] = sorted(name for name in names if name not in SKIPPED_NAMES)
        for name in sorted(files):
            if name not in SKIPPED_NAMES:
                yield Path(directory) / name


def create_grep_tool() -> BaseTool:
    """创建按字面文本逐行搜索的 grep 工具。"""

    def validate(tool_input: ToolInput) -> str | None:
        pattern = tool_input.get("pattern")
        path = tool_input.get("path", ".")
        if not isinstance(pattern, str) or not pattern:
            return "pattern 必须是非空字符串"
        if not isinstance(path, str) or not path.strip():
            return "path 必须是非空字符串"
        return None

    async def grep_text(context: ToolContext, tool_input: ToolInput) -> ToolResult:
        pattern = str(tool_input["pattern"])
        path = str(tool_input.get("path", ".")).strip()
        root = context.project_root.resolve()
        target = (root / path).resolve()
        try:
            relative_target = target.relative_to(root)
        except ValueError:
            return ToolResult("拒绝搜索项目目录之外的文件", is_error=True)
        if any(part in SKIPPED_NAMES for part in relative_target.parts):
            return ToolResult("拒绝搜索依赖、构建产物或敏感配置", is_error=True)
        if not target.exists():
            return ToolResult(f"搜索路径不存在：{path}", is_error=True)

        results: list[str] = []
        files_scanned = 0
        for file_path in _iter_files(target):
            try:
                resolved = file_path.resolve()
                relative = resolved.relative_to(root)
                lines = resolved.read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeDecodeError, ValueError):
                # 图片、数据库等非 UTF-8 文件不是搜索失败，安静跳过即可。
                continue
            files_scanned += 1
            for line_number, line in enumerate(lines, start=1):
                if pattern not in line:
                    continue
                preview = line.strip()
                if len(preview) > MAX_LINE_LENGTH:
                    preview = preview[:MAX_LINE_LENGTH] + "……"
                results.append(f"{relative.as_posix()}:{line_number}: {preview}")
                if len(results) > MAX_RESULTS:
                    break
            if len(results) > MAX_RESULTS:
                break

        truncated = len(results) > MAX_RESULTS
        visible = results[:MAX_RESULTS]
        if not visible:
            return ToolResult(
                "没有找到匹配内容",
                metadata={"matches": 0, "files_scanned": files_scanned},
            )

        content = "\n".join(visible)
        if truncated:
            content += f"\n……结果过多，只显示前 {MAX_RESULTS} 条"
        return ToolResult(
            content,
            metadata={
                "matches": len(visible),
                "files_scanned": files_scanned,
                "truncated": truncated,
            },
        )

    return BaseTool(
        tool_name="grep",
        tool_description="在项目 UTF-8 文本文件中逐行搜索字面文本，返回路径、行号和内容。",
        schema={
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "要查找的原样文字，不是正则表达式",
                },
                "path": {
                    "type": "string",
                    "description": "相对项目根目录的文件或目录，默认搜索整个项目",
                    "default": ".",
                },
            },
            "required": ["pattern"],
            "additionalProperties": False,
        },
        handler=grep_text,
        read_only=True,
        tool_category="search",
        concurrency_check=lambda _tool_input: True,
    )
