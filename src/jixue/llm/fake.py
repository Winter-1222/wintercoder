"""无需网络和密钥的确定性 FakeLLM。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence

from jixue.domain.conversation import APIMessage, Usage
from jixue.llm.base import LLMEventType, LLMStreamEvent


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
    ) -> AsyncIterator[LLMStreamEvent]:
        """读取完整历史，并把固定 Markdown 按不规则边界拆成文本增量。"""

        # ConversationManager 保证最后一条是本轮用户输入；防御性回退只用于直接调用。
        latest_content = messages[-1].content if messages else ""
        # 只回显前 28 个字符并把换行改为空格，避免固定回复被超长输入撑大。
        preview = latest_content.strip().replace("\n", " ")[:28] or "空消息"
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
        history_characters = sum(len(message.content) for message in messages)
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
