"""协议无关的 LLM 接口与当前实现公共出口。

上层可从 jixue.llm 导入自己的接口和 FakeLLM，不需要知道它们分别放在哪个文件。
"""

from jixue.llm.base import LLMClient, LLMEventType, LLMStreamEvent
from jixue.llm.fake import FakeLLMClient

__all__ = ["FakeLLMClient", "LLMClient", "LLMEventType", "LLMStreamEvent"]
