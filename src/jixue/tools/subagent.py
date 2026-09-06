"""统一 Agent 工具：角色增加不会改变工具 Schema。"""

from jixue.tools.base import BaseTool, ToolHandler, ToolInput


def validate_subagent_input(data: ToolInput) -> str | None:
    action = data.get("action")
    if not isinstance(action, str) or action not in {"run", "status", "wait", "stop"}:
        return "action 只允许 run、status、wait、stop"
    allowed = {"action", "agent_id", "subagent_type", "prompt", "background"}
    if set(data) - allowed:
        return "Agent 参数包含未知字段"
    for key in ("agent_id", "subagent_type", "prompt"):
        if key in data and (not isinstance(data[key], str) or not str(data[key]).strip()):
            return f"{key} 必须是非空字符串"
    if "background" in data and type(data["background"]) is not bool:
        return "background 必须是布尔值"
    if action == "run":
        if "prompt" not in data or len(str(data["prompt"])) > 12_000:
            return "run 需要 1—12000 字符的 prompt"
        if "agent_id" in data and "subagent_type" in data:
            return "续接不能重新指定角色"
    elif "agent_id" not in data or set(data) - {"action", "agent_id"}:
        return "status/wait/stop 只接受 action 和 agent_id"
    return None


def create_subagent_tool(handler: ToolHandler) -> BaseTool:
    return BaseTool(
        tool_name="Agent",
        tool_description=(
            "委派并管理子任务。run 指定 subagent_type 使用角色，省略则 Fork；"
            "prompt 写明目标与必要背景。background=true 后台只读。"
            "run 带 agent_id 续接；status 查询、wait 等待、stop 停止。"
        ),
        schema={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["run", "status", "wait", "stop"]},
                "subagent_type": {"type": "string"},
                "agent_id": {"type": "string"},
                "prompt": {"type": "string", "maxLength": 12_000},
                "background": {"type": "boolean"},
            },
            "required": ["action"],
            "additionalProperties": False,
        },
        handler=handler,
        validator=validate_subagent_input,
        # 委派本身不批准任何子工具；执行权限由每个子 Agent 再检查。
        read_only=True,
        tool_category="subagent",
    )
