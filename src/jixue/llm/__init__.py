"""协议无关的 LLM 接口与实现。"""

from jixue.llm.base import LLMClient, LLMEventType, LLMStreamEvent
from jixue.llm.fake import FakeLLMClient

__all__ = ["FakeLLMClient", "LLMClient", "LLMEventType", "LLMStreamEvent"]

