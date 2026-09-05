"""无需网络和密钥的确定性 FakeLLM。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from uuid import uuid4

from jixue.domain.conversation import APIMessage, APIToolResultBlock, Usage
from jixue.llm.base import LLMEventType, LLMStreamEvent, ToolDefinition


class FakeLLMClient:
    """用固定回复验证流式协议和 UI；它不是 AI，也不模拟供应商 SDK 类型。"""

    def __init__(self, chunk_delay: float = 0.025) -> None:
        """保存每个文本分片之间的等待秒数，0 可用于快速自动化测试。"""

        self._chunk_delay = chunk_delay

    @property
    def model_name(self) -> str:
        """返回状态栏显示名，让上层不需要通过类名猜测模型。"""

        return "fake-jixue"

    async def stream(
        self,
        messages: Sequence[APIMessage],
        tools: Sequence[ToolDefinition] = (),
        *,
        system: str = "",
    ) -> AsyncIterator[LLMStreamEvent]:
        """读取完整历史，并把固定 Markdown 按不规则边界拆成文本增量。"""

        latest_content = messages[-1].content if messages else ""
        latest_user_text = _without_system_reminder(latest_content)
        history_characters = sum(len(str(message.content)) for message in messages)
        is_compaction = "<jixue-compaction-request>" in latest_user_text

        # `/loop 文件1 文件2` 会在三轮 LLM 请求中连续触发两次读文件，
        # 用来离线观察“模型 → 工具 → 模型 → 工具 → 模型”的完整循环。
        command_index = next(
            (
                index
                for index in range(len(messages) - 1, -1, -1)
                if messages[index].role == "user" and isinstance(messages[index].content, str)
            ),
            -1,
        )
        loop_paths: list[str] = []
        loop_result_count = 0
        if command_index >= 0:
            command = messages[command_index].content
            assert isinstance(command, str)
            command = _without_system_reminder(command)
            if command.startswith("/loop "):
                loop_paths = command.removeprefix("/loop ").split()
                loop_result_count = sum(
                    isinstance(block, APIToolResultBlock)
                    for message in messages[command_index + 1 :]
                    if isinstance(message.content, tuple)
                    for block in message.content
                )

        # 仅供离线端到端测试：/write 路径 内容 会请求真实 write_file，
        # 后续仍要经过与真实模型完全相同的权限确认和工具执行链路。
        if not is_compaction and latest_user_text.startswith("/write "):
            path, separator, content = latest_user_text.removeprefix("/write ").partition(" ")
            has_write_file = any(tool.get("name") == "write_file" for tool in tools)
            if path and separator and content and has_write_file:
                usage = Usage(max(1, history_characters // 4), 8)
                yield LLMStreamEvent(
                    LLMEventType.TOOL_USE,
                    tool_use_id=f"fake_write_{uuid4().hex}",
                    tool_name="write_file",
                    tool_input={"path": path, "content": content},
                )
                yield LLMStreamEvent(LLMEventType.USAGE, usage=usage)
                yield LLMStreamEvent(
                    LLMEventType.COMPLETE,
                    usage=usage,
                    stop_reason="tool_use",
                )
                return

        if not is_compaction and len(loop_paths) == 2 and loop_result_count < 2:
            has_read_file = any(tool.get("name") == "read_file" for tool in tools)
            if has_read_file:
                usage = Usage(max(1, history_characters // 4), 8)
                yield LLMStreamEvent(
                    LLMEventType.TOOL_USE,
                    tool_use_id=f"fake_loop_{uuid4().hex}",
                    tool_name="read_file",
                    tool_input={"path": loop_paths[loop_result_count]},
                )
                yield LLMStreamEvent(LLMEventType.USAGE, usage=usage)
                yield LLMStreamEvent(
                    LLMEventType.COMPLETE,
                    usage=usage,
                    stop_reason="tool_use",
                )
                return
        # `/read 路径` 是教学用的确定性入口，方便不花 API 费用手测工具闭环。
        if not is_compaction and latest_user_text.startswith("/read "):
            path = latest_user_text.removeprefix("/read ").strip()
            has_read_file = any(tool.get("name") == "read_file" for tool in tools)
            if path and has_read_file:
                usage = Usage(max(1, history_characters // 4), 8)
                yield LLMStreamEvent(
                    LLMEventType.TOOL_USE,
                    tool_use_id=f"fake_read_{uuid4().hex}",
                    tool_name="read_file",
                    tool_input={"path": path},
                )
                yield LLMStreamEvent(LLMEventType.USAGE, usage=usage)
                yield LLMStreamEvent(
                    LLMEventType.COMPLETE,
                    usage=usage,
                    stop_reason="tool_use",
                )
                return

        if is_compaction:
            response = """<analysis>整理较早对话。</analysis>
<summary>
1. 主要请求和意图：继续任务。
2. 关键技术概念：见原对话。
3. 文件和代码段：无。
4. 错误和修复：无。
5. 问题解决过程：已讨论。
6. 所有用户消息：已概括。
7. 待办任务：继续。
8. 当前工作：按用户要求推进。
9. 可能的下一步：读取近期原文。
</summary>"""
        elif len(loop_paths) == 2 and loop_result_count >= 2:
            response = (
                "## Agent Loop 完成\n\n"
                f"我按顺序读取了 `{loop_paths[0]}` 和 `{loop_paths[1]}`。\n\n"
                "这次任务一共经历了 **3 轮 LLM 请求** 和 **2 次工具执行**。"
            )
        elif isinstance(latest_content, tuple):
            results = [block for block in latest_content if isinstance(block, APIToolResultBlock)]
            previews = []
            for result in results:
                content = result.content
                if len(content) > 400:
                    content = content[:400] + "\n\n（FakeLLM 仅展示前 400 字）"
                label = "执行失败" if result.is_error else "执行成功"
                previews.append(f"**{label}**\n\n{content}")
            response = "## 工具执行完成\n\n" + "\n\n".join(previews)
        else:
            # 只回显前 28 个字符，避免固定回复被超长输入撑大。
            preview = latest_user_text.strip().replace("\n", " ")[:28] or "空消息"
            response = (
                "## 霁雪已经醒来\n\n"
                f"我收到了你的消息：**{preview}**\n\n"
                "- Python Bridge 正常\n"
                "- NDJSON 事件流正常\n"
                "- Electron 可以继续接收下一轮消息\n\n"
                "当前使用的是 `FakeLLM`，所以不会产生 API 费用。"
            )

        # 故意使用不规则分片，模拟真实 SDK 可能在任意 Markdown 边界切开的情况。
        chunk_sizes = (1, 2, 5, 3, 8)
        cursor = 0
        chunk_index = 0
        while cursor < len(response):
            size = chunk_sizes[chunk_index % len(chunk_sizes)]
            chunk = response[cursor : cursor + size]
            cursor += size
            chunk_index += 1
            if self._chunk_delay:
                # sleep 是异步等待，不会阻塞整个事件循环处理其他协程。
                await asyncio.sleep(self._chunk_delay)
            # 每次 yield 一段，BridgeApplication 就能立即把它转成 stream_text。
            yield LLMStreamEvent(type=LLMEventType.TEXT, text=chunk)

        # FakeLLM 没有真实 tokenizer，仅用约 4 字符/Token 的保守估算验证状态栏。
        usage = Usage(
            input_tokens=max(1, (history_characters + 3) // 4),
            output_tokens=max(1, (len(response) + 3) // 4),
        )
        # 文本全部发送后，依次产生用量和完成事件，顺序与真实流式调用保持一致。
        yield LLMStreamEvent(type=LLMEventType.USAGE, usage=usage)
        yield LLMStreamEvent(
            type=LLMEventType.COMPLETE,
            usage=usage,
            stop_reason="end_turn",
        )


def _without_system_reminder(content: object) -> str:
    """Fake 只解析用户原话，忽略 Agent 临时附加的动态提醒。"""

    if not isinstance(content, str):
        return ""
    return content.split("\n\n<system-reminder>", maxsplit=1)[0]
