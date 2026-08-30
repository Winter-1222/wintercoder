"""根据 `LLMConfig.protocol` 创建对应的霁雪 LLM 客户端。

应用层只调用这个工厂，不直接 import 任何供应商 SDK。增加新协议时，在适配器目录新增
实现，并在这里增加一个明确分支；现有 Bridge、Agent Loop 和 UI 都不需要改变。
"""

from jixue.llm.adapters.anthropic_client import AnthropicLLMClient
from jixue.llm.base import LLMClient, LLMClientError
from jixue.llm.config import LLMConfig


def create_llm_client(config: LLMConfig) -> LLMClient:
    """按协议创建客户端；未知协议不会静默回退到错误实现。"""

    if config.protocol == "anthropic":
        return AnthropicLLMClient(config)
    raise LLMClientError(
        "unsupported_protocol",
        f"尚未安装协议适配器：{config.protocol}",
        retryable=False,
    )
