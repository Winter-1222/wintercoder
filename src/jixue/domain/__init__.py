"""不依赖外部 SDK 的领域模型。"""

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

