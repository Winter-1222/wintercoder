"""唯一允许导入 anthropic SDK 的文件。"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable, Sequence
from typing import cast

import anthropic
from anthropic.types import MessageParam, ToolParam

from jixue.domain.conversation import APIMessage, Usage
from jixue.llm.base import (
    LLMClientError,
    LLMEventType,
    LLMStreamEvent,
    ToolDefinition,
)
from jixue.llm.config import LLMConfig

type AnthropicClientFactory = Callable[..., anthropic.AsyncAnthropic]


class AnthropicLLMClient:
    """把 Anthropic 协议流翻译成霁雪事件。"""

    def __init__(
        self,
        config: LLMConfig,
        *,
        max_tokens: int = 64_000,
        client_factory: AnthropicClientFactory = anthropic.AsyncAnthropic,
    ) -> None:
        self._config = config
        self._max_tokens = max_tokens
        self._client_factory = client_factory
        self._client: anthropic.AsyncAnthropic | None = None

    @property
    def model_name(self) -> str:
        return self._config.model

    async def stream(
        self,
        messages: Sequence[APIMessage],
        tools: Sequence[ToolDefinition] = (),
    ) -> AsyncIterator[LLMStreamEvent]:
        sdk_messages: list[MessageParam] = [
            {"role": message.role, "content": message.content}
            for message in messages
        ]
        sdk_tools = cast(list[ToolParam], [dict(tool) for tool in tools])
        tool_buffers: dict[int, tuple[str, str, list[str]]] = {}
        try:
            async with self._get_client().messages.stream(
                model=self._config.model,
                max_tokens=self._max_tokens,
                messages=sdk_messages,
                tools=sdk_tools,
                cache_control={"type": "ephemeral"},
            ) as stream:
                async for event in stream:
                    if event.type == "content_block_start":
                        block = event.content_block
                        if block.type == "tool_use":
                            tool_buffers[event.index] = (block.id, block.name, [])
                    elif event.type == "content_block_delta":
                        if event.delta.type == "text_delta" and event.delta.text:
                            yield LLMStreamEvent(LLMEventType.TEXT, text=event.delta.text)
                        elif event.delta.type == "input_json_delta":
                            buffer = tool_buffers.get(event.index)
                            if buffer is not None:
                                buffer[2].append(event.delta.partial_json)
                    elif event.type == "content_block_stop":
                        buffer = tool_buffers.pop(event.index, None)
                        if buffer is not None:
                            yield _tool_use_event(buffer)

                # 流若在 JSON 结束块之前中断，也生成错误事件，不能丢失整个请求。
                for buffer in tool_buffers.values():
                    yield _tool_use_event(buffer, interrupted=True)
                final = await stream.get_final_message()
        except anthropic.APIError as error:
            raise _translate_error(error) from error

        usage = Usage(final.usage.input_tokens, final.usage.output_tokens)
        yield LLMStreamEvent(LLMEventType.USAGE, usage=usage)
        yield LLMStreamEvent(
            LLMEventType.COMPLETE,
            usage=usage,
            stop_reason=final.stop_reason or "end_turn",
        )

    def _get_client(self) -> anthropic.AsyncAnthropic:
        if not self._config.api_key:
            raise LLMClientError(
                "credentials_missing",
                "请先在项目 .env 中设置 DEEPSEEK_API_KEY",
                retryable=False,
            )
        if self._client is None:
            self._client = self._client_factory(
                api_key=self._config.api_key,
                base_url=self._config.base_url,
                max_retries=2,
            )
        return self._client


def _tool_use_event(
    buffer: tuple[str, str, list[str]],
    *,
    interrupted: bool = False,
) -> LLMStreamEvent:
    """拼接 JSON 碎片；格式错误作为事件返回，不让流崩溃。"""

    tool_use_id, name, fragments = buffer
    raw_input = "".join(fragments) or "{}"
    error = "工具参数流在结束前中断" if interrupted else None
    try:
        value: object = json.loads(raw_input)
    except json.JSONDecodeError:
        value = {}
        error = error or "工具参数不是有效 JSON"
    if not isinstance(value, dict):
        value = {}
        error = error or "工具参数必须是 JSON 对象"

    return LLMStreamEvent(
        LLMEventType.TOOL_USE,
        tool_use_id=tool_use_id,
        tool_name=name,
        tool_input={str(key): item for key, item in value.items()},
        tool_error=error,
    )


def _translate_error(error: anthropic.APIError) -> LLMClientError:
    """SDK 异常不能离开适配器，也不能把请求内容暴露给 UI。"""

    if isinstance(error, anthropic.AuthenticationError):
        return LLMClientError("authentication_failed", "API Key 无效", retryable=False)
    if isinstance(error, anthropic.PermissionDeniedError):
        return LLMClientError("permission_denied", "API Key 没有模型权限", retryable=False)
    if isinstance(error, anthropic.NotFoundError):
        return LLMClientError(
            "model_or_endpoint_not_found", "模型或端点不存在", retryable=False
        )
    if isinstance(error, anthropic.BadRequestError):
        return LLMClientError("invalid_model_request", "模型请求格式错误", retryable=False)
    if isinstance(error, anthropic.RateLimitError):
        return LLMClientError("rate_limited", "请求过多，请稍后重试", retryable=True)
    if isinstance(error, anthropic.APITimeoutError):
        return LLMClientError("request_timeout", "模型响应超时", retryable=True)
    if isinstance(error, anthropic.APIConnectionError):
        return LLMClientError("connection_failed", "无法连接模型服务", retryable=True)
    if isinstance(error, anthropic.APIStatusError) and error.status_code >= 500:
        return LLMClientError("provider_unavailable", "模型服务暂时不可用", retryable=True)
    return LLMClientError("provider_error", "模型服务返回未知错误", retryable=False)
