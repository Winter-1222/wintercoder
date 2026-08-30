"""使用官方 Anthropic Python SDK 实现霁雪的 `LLMClient`。

这是正式源码中唯一允许 `import anthropic` 的边界。SDK 的客户端、消息参数、流对象和
异常都不能离开本文件；上游只会收到霁雪自己的 `LLMStreamEvent` 或 `LLMClientError`。

当前小步只处理文本、Token 和停止原因。工具调用块会在第二章扩展，完整多轮消息会在
本章后续由 ConversationManager 提供。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable

import anthropic
from anthropic.types import MessageParam

from jixue.domain.messages import Usage
from jixue.llm.base import (
    LLMClientError,
    LLMEventType,
    LLMStreamEvent,
)
from jixue.llm.config import LLMConfig

# 流式请求不容易遇到普通 HTTP 的长响应超时，给模型保留足够输出空间。
DEFAULT_MAX_TOKENS = 64_000

# 构造器注入只用于本地测试：正式运行默认使用官方 AsyncAnthropic。
# 类型别名仍明确要求返回官方客户端，没有复制 SDK 自己的数据结构。
AnthropicClientFactory = Callable[..., anthropic.AsyncAnthropic]


class AnthropicLLMClient:
    """把 Anthropic 协议流转换成霁雪领域事件。"""

    def __init__(
        self,
        config: LLMConfig,
        *,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        client_factory: AnthropicClientFactory = anthropic.AsyncAnthropic,
    ) -> None:
        """保存四字段配置，但延迟到第一次请求才创建 SDK 客户端。

        延迟创建让“缺少 Key”保持为可恢复状态：应用可以先启动和展示配置，真正发送
        DeepSeek 请求时再得到明确的 `credentials_missing` 错误。
        """

        if config.protocol != "anthropic":
            raise LLMClientError(
                "unsupported_protocol",
                f"Anthropic 适配器不能处理协议：{config.protocol}",
                retryable=False,
            )
        if max_tokens <= 0:
            raise ValueError("max_tokens 必须是正整数")

        self._config = config
        self._max_tokens = max_tokens
        self._client_factory = client_factory
        self._client: anthropic.AsyncAnthropic | None = None

    @property
    def model_name(self) -> str:
        """返回实际发给 API 的模型名，Bridge 会把它显示在状态栏。"""

        return self._config.model

    async def stream(self, prompt: str) -> AsyncIterator[LLMStreamEvent]:
        """发送单条用户文本，并按“文本 → 用量 → 完成”顺序产生领域事件。

        `messages.stream()` 返回异步上下文管理器。进入 `async with` 后网络流才真正开始；
        `text_stream` 每次只给新增文字；完全消费流后，等待 `get_final_message()` 从
        SDK 已收集的流状态中组装最终 Token 和停止原因，不会再发第二次 API 请求。
        """

        client = self._get_client()
        messages: list[MessageParam] = [{"role": "user", "content": prompt}]

        try:
            async with client.messages.stream(
                model=self._config.model,
                max_tokens=self._max_tokens,
                messages=messages,
                # 顶层自动缓存会把最后一个可缓存块设为断点。当前单轮短消息通常达不到
                # 最小缓存长度；ConversationManager 加入稳定历史后，前缀才能产生读命中。
                cache_control={"type": "ephemeral"},
            ) as stream:
                async for text in stream.text_stream:
                    # SDK 理论上只产生非空增量；这里忽略空片段，避免 UI 做无意义渲染。
                    if text:
                        yield LLMStreamEvent(LLMEventType.TEXT, text=text)

                # 异步版本返回协程；await 等待本地流收口，不代表重新请求一次模型。
                final_message = await stream.get_final_message()
        except anthropic.APIError as error:
            # 只捕获 SDK 的公共异常基类；取消信号等非 SDK 异常继续向外传播。
            raise _translate_anthropic_error(error) from error

        usage = Usage(
            input_tokens=final_message.usage.input_tokens,
            output_tokens=final_message.usage.output_tokens,
        )
        yield LLMStreamEvent(LLMEventType.USAGE, usage=usage)
        yield LLMStreamEvent(
            LLMEventType.COMPLETE,
            usage=usage,
            stop_reason=final_message.stop_reason or "end_turn",
        )

    def _get_client(self) -> anthropic.AsyncAnthropic:
        """返回已缓存的 SDK 客户端；第一次调用时才创建。"""

        if self._config.api_key is None:
            raise LLMClientError(
                "credentials_missing",
                "当前模型没有配置 API Key，请先设置对应环境变量",
                retryable=False,
            )

        if self._client is None:
            # 显式传入 Key，不能让 SDK 回退读取其他 Anthropic 环境变量。
            self._client = self._client_factory(
                api_key=self._config.api_key,
                base_url=self._config.base_url,
                # 官方 SDK 默认会对 429 和 5xx 自动重试两次，无需在适配器重复造轮子。
                max_retries=2,
            )
        return self._client


def _translate_anthropic_error(error: anthropic.APIError) -> LLMClientError:
    """把 SDK 类型化异常翻译成稳定、安全、可供 UI 判断的领域错误。

    判断顺序必须从具体到一般，因为认证、限流等异常也属于 `APIStatusError`。
    公共消息不拼接 `str(error)`，避免供应商响应或请求信息意外进入界面和日志。
    """

    if isinstance(error, anthropic.AuthenticationError):
        return LLMClientError(
            "authentication_failed",
            "API Key 无效或已经失效，请检查模型认证配置",
            retryable=False,
        )
    if isinstance(error, anthropic.PermissionDeniedError):
        return LLMClientError(
            "permission_denied",
            "API Key 没有调用当前模型或端点的权限",
            retryable=False,
        )
    if isinstance(error, anthropic.NotFoundError):
        return LLMClientError(
            "model_or_endpoint_not_found",
            "模型名称或 API 端点不存在，请检查 models.yaml",
            retryable=False,
        )
    if isinstance(error, anthropic.BadRequestError):
        return LLMClientError(
            "invalid_model_request",
            "模型拒绝了请求格式，请检查消息和模型参数",
            retryable=False,
        )
    if isinstance(error, anthropic.RateLimitError):
        return LLMClientError(
            "rate_limited",
            "模型服务当前请求过多，请稍后重试",
            retryable=True,
        )
    if isinstance(error, anthropic.APITimeoutError):
        return LLMClientError(
            "request_timeout",
            "等待模型响应超时，请检查网络后重试",
            retryable=True,
        )
    if isinstance(error, anthropic.APIConnectionError):
        return LLMClientError(
            "connection_failed",
            "无法连接模型服务，请检查网络和 base_url",
            retryable=True,
        )
    if isinstance(error, anthropic.APIStatusError) and error.status_code >= 500:
        return LLMClientError(
            "provider_unavailable",
            "模型服务暂时不可用，请稍后重试",
            retryable=True,
        )
    return LLMClientError(
        "provider_error",
        "模型服务返回了未识别错误",
        retryable=False,
    )
