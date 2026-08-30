"""不依赖外部 SDK 的领域模型公共出口。

__all__ 明确告诉阅读者和工具：从 jixue.domain 导入时，哪些名称属于稳定公共接口。
"""

from jixue.domain.events import Envelope, ProtocolError
from jixue.domain.messages import Message, MessageStatus, Role, Usage

__all__ = [
    "Envelope",
    "Message",
    "MessageStatus",
    "ProtocolError",
    "Role",
    "Usage",
]
