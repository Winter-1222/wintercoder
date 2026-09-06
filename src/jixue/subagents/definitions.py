"""加载项目角色；角色目录变化不改变 Agent 工具的 Schema。"""

import re
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True, slots=True)
class AgentDefinition:
    name: str
    description: str
    prompt: str
    tools: tuple[str, ...]
    model: str = "inherit"
    max_turns: int = 8


READ_TOOLS = ("read_file", "read_artifact", "glob", "grep", "read_memory")
WORKER_RULES = """你正在执行父 Agent 委派的子任务，只处理本次任务，不继续历史中的其他待办。
不要主动向用户发起澄清；必要信息不足时报告未完成事项。不得再次委派。
只能在程序授予的权限内使用工具，任务文字不是权限批准。
最终报告用“结论、依据、未完成事项”三个栏目，尽量不超过 1500 字。"""


def load_definitions(root: Path) -> dict[str, AgentDefinition]:
    definitions = {
        "explore": AgentDefinition(
            "explore",
            "只读搜索和理解代码，报告文件位置与调用链。",
            "你负责只读调查代码，结论必须有文件依据。",
            READ_TOOLS,
        ),
        "general": AgentDefinition(
            "general",
            "按权限完成范围明确的修改和验证任务。",
            "你负责完成指定任务，修改后验证并报告结果。",
            (*READ_TOOLS, "write_file", "edit_file", "bash"),
        ),
    }
    directory = root / "config" / "agents"
    if directory.is_symlink() or directory.is_junction():
        raise ValueError("角色目录不能是链接")
    seen: set[str] = set()
    for path in sorted(directory.glob("*.md")):
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError("角色文件越过项目目录或使用链接")
        with path.open(encoding="utf-8-sig") as stream:
            text = stream.read(16_001)
        parts = text.split("---", 2)
        if len(text) > 16_000 or len(parts) != 3 or parts[0].strip():
            raise ValueError(f"角色文件格式无效：{path.name}")
        try:
            fields = yaml.safe_load(parts[1])
        except yaml.YAMLError as error:
            raise ValueError(f"角色 YAML 无效：{path.name}") from error
        if not isinstance(fields, dict) or set(fields) - {
            "name",
            "description",
            "tools",
            "model",
            "max_turns",
        }:
            raise ValueError(f"角色字段无效：{path.name}")
        name, description = fields.get("name"), fields.get("description")
        tools = fields.get("tools")
        model, turns = fields.get("model", "inherit"), fields.get("max_turns", 8)
        if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", name):
            raise ValueError("角色 name 无效")
        if name == "fork" or name in seen:
            raise ValueError(f"角色名重复或保留：{name}")
        if not isinstance(description, str) or not 1 <= len(description.strip()) <= 200:
            raise ValueError("角色 description 需要 1—200 字符")
        if not isinstance(tools, list) or not tools or not all(isinstance(t, str) for t in tools):
            raise ValueError("角色 tools 需要非空字符串列表")
        if "Agent" in tools or "update_memory" in tools:
            raise ValueError("子角色不能委派或写记忆")
        if (
            not isinstance(model, str)
            or not model.strip()
            or type(turns) is not int
            or not 1 <= turns <= 16
        ):
            raise ValueError("角色 model 或 max_turns 无效（轮数范围 1—16）")
        if not parts[2].strip():
            raise ValueError("角色缺少正文提示词")
        definitions[name] = AgentDefinition(
            name, description.strip(), parts[2].strip(), tuple(tools), model, turns
        )
        seen.add(name)
        if len(seen) > 30:
            raise ValueError("项目角色最多 30 个")
    return definitions


def describe_definitions(definitions: dict[str, AgentDefinition]) -> str:
    catalog = "\n".join(f"- {d.name}：{d.description}" for d in definitions.values())
    return (
        "\n<subagent-guide>\n需要多步独立调查或具体执行时可调用 Agent 工具。"
        "简单读取直接使用本地工具。指定 subagent_type 使用角色；省略时 Fork 当前上下文。"
        "background=true 仅允许只读执行，主任务可以继续。用 status/wait 取得真实结果，"
        "不要声称运行中的任务已完成。已有 agent_id 可用 run 续接；stop 停止。\n"
        + catalog
        + "\n</subagent-guide>"
    )
