"""无需网络和密钥的确定性 FakeLLM。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from jixue.domain.messages import Usage
from jixue.llm.base import LLMEventType, LLMStreamEvent


class FakeLLMClient:
    """用固定回复验证流式协议和 UI，不模拟供应商 SDK。"""

    def __init__(self, chunk_delay: float = 0.025) -> None:
        self._chunk_delay = chunk_delay

    @property
    def model_name(self) -> str:
        return "fake-jixue"

    async def stream(self, prompt: str) -> AsyncIterator[LLMStreamEvent]:
        """把一段 Markdown 按不规则边界拆成文本增量。"""

        preview = prompt.strip().replace("\n", " ")[:28] or "空消息"
        response = (
            "## 霁雪已经醒来\n\n"
            f"我收到了你的消息：**{preview}**\n\n"
            "- Python Bridge 正常\n"
            "- NDJSON 事件流正常\n"
            "- Electron 可以继续接收下一轮消息\n\n"
            "当前使用的是 `FakeLLM`，所以不会产生 API 费用。"
        )

        chunk_sizes = (1, 2, 5, 3, 8)
        cursor = 0
        chunk_index = 0
        while cursor < len(response):
            size = chunk_sizes[chunk_index % len(chunk_sizes)]
            chunk = response[cursor : cursor + size]
            cursor += size
            chunk_index += 1
            if self._chunk_delay:
                await asyncio.sleep(self._chunk_delay)
            yield LLMStreamEvent(type=LLMEventType.TEXT, text=chunk)

        usage = Usage(
            input_tokens=max(1, (len(prompt) + 3) // 4),
            output_tokens=max(1, (len(response) + 3) // 4),
        )
        yield LLMStreamEvent(type=LLMEventType.USAGE, usage=usage)
        yield LLMStreamEvent(
            type=LLMEventType.COMPLETE,
            usage=usage,
            stop_reason="end_turn",
        )

