"""第五章权限核心：在工具真正执行前给出允许、拒绝或询问。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # 这里只需要类型提示；运行时导入 tools 会让 write_tools 与本模块互相等待。
    from jixue.tools.base import Tool, ToolInput


class PermissionDecision(StrEnum):
    """权限判断只有三种结果，Agent 后续只需按结果分支。"""

    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


class PermissionMode(StrEnum):
    """用户选择的整体权限策略；模式只影响“询问”，不能绕过硬拦截。"""

    CONFIRM_EDITS = "confirm_edits"
    ASK_ALL = "ask_all"
    AUTO_ALLOW = "auto_allow"


@dataclass(frozen=True, slots=True)
class PermissionCheck:
    """同时返回决定和中文原因，原因以后可直接展示在确认卡片上。"""

    decision: PermissionDecision
    reason: str


# 黑名单只负责拦截少量“无论什么模式都不该执行”的系统级灾难命令。
# 它故意不是完整安全方案；其他命令还会继续经过规则、模式和用户确认。
_HARD_BLOCKED_COMMANDS = (
    re.compile(r"\brm\s+-(?:[a-z]*r[a-z]*f|[a-z]*f[a-z]*r)[a-z]*\s+(?:--\s+)?(?:/|/\*|~|\$home)(?:\s|$|[;&|])"),
    re.compile(r"\b(?:format\s+[a-z]:|diskpart\b)"),
    re.compile(r"\b(?:rd|rmdir)\s+/s\s+/q\s+[a-z]:\\(?:\*|\s|$)"),
    re.compile(r"\bremove-item\b(?=[^;&|]*-recurse)(?=[^;&|]*-force)[^;&|]*[a-z]:\\(?:\*|\s|$)"),
)

# 第五章先只放几条容易看懂、不会修改仓库的精确规则。
# 使用精确匹配很重要：`git status; 删除命令` 不会被误当成安全命令。
_SAFE_SHELL_COMMANDS = {
    "git status",
    "git status --short",
    "git diff",
    "git diff --staged",
    "git log -1 --oneline",
    "git branch --show-current",
}


def evaluate_permission(
    project_root: Path,
    tool: Tool,
    tool_input: ToolInput,
    mode: PermissionMode = PermissionMode.CONFIRM_EDITS,
) -> PermissionCheck:
    """先做永远生效的安全检查，再按权限模式决定执行还是询问。"""

    if tool.category() == "shell":
        command = tool_input.get("command")
        if isinstance(command, str) and _is_hard_blocked(command):
            return PermissionCheck(
                PermissionDecision.DENY,
                "命令可能破坏系统或整个磁盘，已被硬拦截",
            )

    if tool.category() in {"file", "search"}:
        path = tool_input.get("path")
        if isinstance(path, str):
            try:
                resolve_project_path(project_root, path)
            except ValueError as error:
                return PermissionCheck(PermissionDecision.DENY, str(error))

        # 只有 glob 的 pattern 表示路径；grep 的同名参数是要搜索的普通文字。
        pattern = tool_input.get("pattern")
        if tool.name() == "glob" and isinstance(pattern, str):
            pattern_path = Path(pattern)
            if pattern_path.is_absolute() or ".." in pattern_path.parts:
                return PermissionCheck(
                    PermissionDecision.DENY,
                    "路径模式越过了项目目录，已被沙箱拒绝",
                )

    # “每次都询问”用于学习和审计：只要没被上面的规则拒绝，就交给用户决定。
    if mode is PermissionMode.ASK_ALL:
        return PermissionCheck(PermissionDecision.ASK, "当前是每次都询问模式")

    # “自动允许”省去确认步骤，但它位于硬拦截和路径沙箱之后，所以不能越过安全底线。
    if mode is PermissionMode.AUTO_ALLOW:
        return PermissionCheck(PermissionDecision.ALLOW, "当前是自动允许模式")

    if tool.category() == "shell":
        command = tool_input.get("command")
        if isinstance(command, str) and _is_safe_shell_command(command):
            return PermissionCheck(
                PermissionDecision.ALLOW,
                "命中内置只读命令规则，可直接执行",
            )

    if tool.is_read_only():
        return PermissionCheck(PermissionDecision.ALLOW, "只读工具可以直接执行")
    return PermissionCheck(
        PermissionDecision.ASK,
        "工具可能改变文件或系统状态，需要用户确认",
    )


def resolve_project_path(project_root: Path, raw_path: str) -> Path:
    """把相对路径变成项目内绝对路径；越界时立即拒绝。"""

    try:
        root = project_root.resolve()
        target = (root / raw_path).resolve()
        target.relative_to(root)
    except (OSError, RuntimeError, ValueError) as error:
        raise ValueError("路径越过了项目目录，已被沙箱拒绝") from error
    return target


def _is_hard_blocked(command: str) -> bool:
    """统一大小写、引号和空白后匹配明显的灾难命令。"""

    normalized = " ".join(command.casefold().replace('"', "").replace("'", "").split())
    return any(pattern.search(normalized) for pattern in _HARD_BLOCKED_COMMANDS)


def _is_safe_shell_command(command: str) -> bool:
    """只接受规则表中的完整命令，不对前缀或通配符做宽松匹配。"""

    normalized = " ".join(command.casefold().split())
    return normalized in _SAFE_SHELL_COMMANDS
