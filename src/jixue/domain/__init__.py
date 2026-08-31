"""霁雪自己的领域类型，不依赖任何外部 SDK。"""

from jixue.domain.conversation import (
    APIMessage,
    ConversationError,
    ConversationManager,
    Message,
    MessageStatus,
    Role,
    Usage,
)
from jixue.domain.events import Envelope, ProtocolError

__all__ = [
    "APIMessage",
    "ConversationError",
    "ConversationManager",
    "Envelope",
    "Message",
    "MessageStatus",
    "ProtocolError",
    "Role",
    "Usage",
]
