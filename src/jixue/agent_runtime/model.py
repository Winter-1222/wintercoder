"""可取消的模型流，以及单次模型响应到 Agent 事件的转换。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field

from jixue.agent_runtime.control import RunControl, cancel_in_background
from jixue.agent_runtime.events import AgentEvent, AgentEventType, add_usage, event, usage_payload
from jixue.domain.conversation import (
    APIContentBlock,
    APIMessage,
    APITextBlock,
    APIToolUseBlock,
    Usage,
)
from jixue.llm.base import LLMClient, LLMEventType, LLMStreamEvent, ToolDefinition


@dataclass(slots=True)
class ModelResponse:
    """一次模型请求的结果；流中途失败时也保留已收到的用量和工具卡片。"""

    blocks: list[APIContentBlock] = field(default_factory=list)
    calls: list[LLMStreamEvent] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    stop_reason: str = "end_turn"
    event_received: bool = False

    @property
    def text(self) -> str:
        return "".join(block.text for block in self.blocks if isinstance(block, APITextBlock))


class ModelStream:
    """只负责请求和流转换；是否摘要、重试或执行工具由上层决定。"""

    def __init__(self, llm: LLMClient, control: RunControl, system_prompt: str) -> None:
        self._llm = llm
        self._control = control
        self._system_prompt = system_prompt

    def set_system_prompt(self, prompt: str) -> None:
        """只在新任务开始时刷新，工具循环内保持同一前缀。"""
        self._system_prompt = prompt

    @property
    def model_name(self) -> str:
        return self._llm.model_name

    async def stream(
        self,
        messages: Sequence[APIMessage],
        tool_definitions: Sequence[ToolDefinition],
        *,
        system_prompt: str | None = None,
    ) -> AsyncIterator[LLMStreamEvent]:
        """同时等待模型事件和取消信号，取消时关闭正在等待的流。"""

        iterator = self._llm.stream(
            messages,
            tool_definitions,
            system=self._system_prompt if system_prompt is None else system_prompt,
        ).__aiter__()
        while not self._control.cancel_event.is_set():
            next_event = asyncio.ensure_future(anext(iterator))
            cancel_wait = asyncio.create_task(self._control.cancel_event.wait())
            done, _ = await asyncio.wait(
                (next_event, cancel_wait),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if cancel_wait in done:
                # 大多数网络库会立刻响应 cancel，但少数底层连接可能要等超时才退出。
                # 这里不能继续 await 它，否则页面会显示“正在停止”却迟迟无法解锁。
                cancel_in_background(next_event)
                return

            cancel_wait.cancel()
            await asyncio.gather(cancel_wait, return_exceptions=True)
            try:
                yield next_event.result()
            except StopAsyncIteration:
                return

    async def respond(
        self,
        messages: Sequence[APIMessage],
        tools: Sequence[ToolDefinition],
        response: ModelResponse,
        *,
        message_id: str,
        task_usage: Usage,
        conversation_usage: Usage,
    ) -> AsyncIterator[AgentEvent]:
        """边记录一轮结果边发送事件，保持正文、工具和用量的原始顺序。"""

        async for item in self.stream(messages, tools):
            response.event_received = True
            if item.type is LLMEventType.TEXT:
                _append_text(response.blocks, item.text)
                yield event(AgentEventType.STREAM_TEXT, text=item.text, message_id=message_id)
            elif item.type is LLMEventType.TOOL_USE:
                response.calls.append(item)
                response.blocks.append(
                    APIToolUseBlock(item.tool_use_id, item.tool_name, item.tool_input)
                )
                payload: dict[str, object] = {
                    "id": item.tool_use_id,
                    "name": item.tool_name,
                    "input": dict(item.tool_input),
                }
                if item.tool_error:
                    payload["error"] = item.tool_error
                yield AgentEvent(AgentEventType.TOOL_USE, payload)
            elif item.type is LLMEventType.USAGE:
                response.usage = add_usage(response.usage, item.usage)
                current_usage = add_usage(task_usage, response.usage)
                yield event(
                    AgentEventType.USAGE,
                    turn=usage_payload(current_usage),
                    cumulative=usage_payload(add_usage(conversation_usage, current_usage)),
                )
            elif item.type is LLMEventType.COMPLETE:
                response.stop_reason = item.stop_reason or "end_turn"


def _append_text(blocks: list[APIContentBlock], text: str) -> None:
    """合并相邻文本，但保留文本块和工具块的先后顺序。"""

    if blocks and isinstance(blocks[-1], APITextBlock):
        blocks[-1] = APITextBlock(blocks[-1].text + text)
    else:
        blocks.append(APITextBlock(text))
