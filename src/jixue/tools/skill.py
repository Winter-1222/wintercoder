"""Skill 加载是一个普通只读工具，权限与结果处理沿用原有执行器。"""

import asyncio

from jixue.skills import NAME_PATTERN, SkillStore, validate_skill_name
from jixue.tools.base import BaseTool, ToolContext, ToolInput, ToolResult


def create_load_skill_tool() -> BaseTool:
    def validate(tool_input: ToolInput) -> str | None:
        if set(tool_input) != {"name"} or not isinstance(tool_input.get("name"), str):
            return "load_skill 只接受一个字符串参数 name"
        try:
            validate_skill_name(str(tool_input["name"]))
        except ValueError as error:
            return str(error)
        return None

    async def load(context: ToolContext, tool_input: ToolInput) -> ToolResult:
        content = await asyncio.to_thread(
            SkillStore(context.project_root).load, str(tool_input["name"])
        )
        return ToolResult(content)

    return BaseTool(
        tool_name="load_skill",
        tool_description=(
            "按 available-skills 中的 name 加载该技能的完整 SKILL.md 说明；"
            "不加载引用资料，不执行脚本。"
        ),
        schema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "pattern": f"^{NAME_PATTERN}$", "maxLength": 64},
            },
            "required": ["name"],
            "additionalProperties": False,
        },
        handler=load,
        validator=validate,
        read_only=True,
        tool_category="skill",
        concurrency_check=lambda _: True,
    )
