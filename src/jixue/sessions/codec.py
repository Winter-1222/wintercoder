"""会话快照的 JSON 编解码；不依赖任何模型供应商。"""

from dataclasses import asdict
from typing import Any

from jixue.domain.conversation import (
    APIContentBlock,
    APITextBlock,
    APIToolResultBlock,
    APIToolUseBlock,
    ConversationManager,
    Message,
    MessageStatus,
    Usage,
)


def encode_conversation(conversation: ConversationManager) -> dict[str, object]:
    messages = []
    for message in conversation.messages:
        item = asdict(message)
        if isinstance(message.content, tuple):
            item["content"] = [
                {
                    "type": "text"
                    if isinstance(block, APITextBlock)
                    else "tool_use"
                    if isinstance(block, APIToolUseBlock)
                    else "tool_result",
                    **asdict(block),
                }
                for block in message.content
            ]
        messages.append(item)
    return {
        "messages": messages,
        "usage": asdict(conversation.total_usage),
        "completed_turns": conversation.completed_turns,
    }


def decode_conversation(data: dict[str, Any]) -> ConversationManager:
    if not isinstance(data, dict) or not isinstance(data.get("messages"), list):
        raise ValueError("会话快照格式无效")
    messages = []
    for item in data["messages"]:
        _validate_message(item)
        content = item["content"]
        if not isinstance(content, str):
            blocks: list[APIContentBlock] = []
            for block in content:
                fields = {key: value for key, value in block.items() if key != "type"}
                kind = block["type"]
                if kind == "text":
                    blocks.append(APITextBlock(**fields))
                elif kind == "tool_use":
                    blocks.append(APIToolUseBlock(**fields))
                elif kind == "tool_result":
                    blocks.append(APIToolResultBlock(**fields))
                else:
                    raise ValueError("会话包含未知内容块")
            content = tuple(blocks)
        if item["role"] not in {"user", "assistant"}:
            raise ValueError("会话角色无效")
        messages.append(
            Message(
                role=item["role"],
                content=content,
                status=MessageStatus(item["status"]),
                id=item["id"],
                created_at=item["created_at"],
                usage=Usage(**item["usage"]),
                is_summary=item["is_summary"],
            )
        )
    usage = Usage(**data["usage"])
    turns = data["completed_turns"]
    if any(
        type(value) is not int or value < 0
        for value in (usage.input_tokens, usage.output_tokens, turns)
    ):
        raise ValueError("会话用量或轮数无效")
    return ConversationManager(messages, total_usage=usage, completed_turns=turns)


def _validate_message(item: Any) -> None:
    """本地文件也可能被手工改坏；在替换当前会话之前拒绝错误结构。"""
    if not isinstance(item, dict):
        raise ValueError("消息必须是对象")
    if (
        not all(isinstance(item.get(key), str) for key in ("id", "created_at"))
        or type(item.get("is_summary")) is not bool
    ):
        raise ValueError("消息元数据无效")
    usage = item.get("usage")
    if not isinstance(usage, dict) or any(
        type(usage.get(key)) is not int or usage[key] < 0
        for key in ("input_tokens", "output_tokens")
    ):
        raise ValueError("消息用量无效")
    content = item.get("content")
    if isinstance(content, str):
        return
    if not isinstance(content, list):
        raise ValueError("消息正文必须是文字或内容块列表")
    for block in content:
        if not isinstance(block, dict):
            raise ValueError("内容块必须是对象")
        kind = block.get("type")
        if kind == "text":
            valid = isinstance(block.get("text"), str)
        elif kind == "tool_use":
            valid = all(isinstance(block.get(key), str) for key in ("id", "name")) and isinstance(
                block.get("input"), dict
            )
        elif kind == "tool_result":
            valid = (
                all(isinstance(block.get(key), str) for key in ("tool_use_id", "content"))
                and type(block.get("is_error")) is bool
            )
        else:
            valid = False
        if not valid:
            raise ValueError("会话内容块格式无效")
