"""管理内部消息，并转换成供应商无关的 API 消息。

这个文件位于“界面/持久化使用的完整消息”和“LLM 客户端使用的干净消息”之间。它不
导入 Anthropic SDK，也不知道 Electron。以后更换模型供应商时，消息历史仍由这里管理。
"""

from __future__ import annotations

from dataclasses import dataclass

from jixue.domain.messages import Message, MessageStatus, Role


class ConversationError(ValueError):
    """表示消息历史无法安全转换，而不是模型服务或程序崩溃。"""


@dataclass(frozen=True, slots=True)
class APIMessage:
    """只保留模型 API 需要的两个字段。

    `Message` 有 ID、状态、时间和 Token；这些元数据用于 UI 与持久化，不应重复发送给
    模型。API 层因此只暴露 `role + content`，也不包含任何供应商 SDK 类型。
    """

    # 说话方只能是 user 或 assistant。
    role: Role
    # 已完成、非空、可以发送给模型的纯文本。
    content: str


class ConversationManager:
    """保存一份有顺序的内部消息列表，并负责生成干净 API 历史。"""

    def __init__(self, messages: list[Message] | None = None) -> None:
        """创建对话；传入列表时逐条走 `add()`，保证 ID 不重复。"""

        self._messages: list[Message] = []
        for message in messages or []:
            self.add(message)

    @property
    def messages(self) -> tuple[Message, ...]:
        """返回不可修改的快照，避免调用方绕过 `add()` 直接篡改内部列表。"""

        return tuple(self._messages)

    def add(self, message: Message) -> None:
        """按时间顺序加入一条内部消息，并拒绝重复 ID。

        同一个 ID 通常表示同一条 UI 消息。若重复加入，后续持久化和流式更新会分不清
        目标，因此应在最靠近入口的位置明确拒绝。
        """

        if any(existing.id == message.id for existing in self._messages):
            raise ConversationError(f"消息 ID 重复：{message.id}")
        self._messages.append(message)

    def add_user(self, content: str) -> Message:
        """创建一条已完成的用户消息、加入历史并返回它。"""

        message = Message(role="user", content=content)
        self.add(message)
        return message

    def add_assistant(
        self,
        content: str,
        *,
        status: MessageStatus = MessageStatus.COMPLETE,
    ) -> Message:
        """创建助手消息；流式阶段可以显式传入 `STREAMING`。"""

        message = Message(role="assistant", content=content, status=status)
        self.add(message)
        return message

    def clear(self) -> None:
        """清空当前会话；只影响内存，不删除未来的磁盘会话文件。"""

        self._messages.clear()

    def to_api_format(self) -> list[APIMessage]:
        """按“过滤 → 清理 → 合并 → 校验”生成新的 API 消息列表。

        转换不会修改 `self._messages`：

        1. 过滤 streaming、failed、cancelled 和纯空白消息。
        2. 去除正文两端无意义空白。
        3. 用两个换行合并相邻同角色消息。
        4. 校验首条必须来自 user，且角色严格交替。

        若过滤后没有可发送内容，或历史以 assistant 开头，就抛出可理解的领域错误。调用
        方可以把它显示给用户，而不应该把它当成 Python 内部崩溃。
        """

        cleaned: list[APIMessage] = []
        for message in self._messages:
            if message.status is not MessageStatus.COMPLETE:
                # 半截回复和失败/取消文本可以留给 UI 看，但不能冒充完整历史发给模型。
                continue

            content = message.content.strip()
            if not content:
                # 空内容既浪费请求位置，也可能破坏角色交替，因此在转换阶段统一忽略。
                continue

            if cleaned and cleaned[-1].role == message.role:
                # 相邻同角色消息合成一条。两个换行保留原消息之间清楚的段落边界。
                previous = cleaned[-1]
                cleaned[-1] = APIMessage(
                    role=previous.role,
                    content=f"{previous.content}\n\n{content}",
                )
            else:
                cleaned.append(APIMessage(role=message.role, content=content))

        self._validate_api_messages(cleaned)
        return cleaned

    @staticmethod
    def _validate_api_messages(messages: list[APIMessage]) -> None:
        """确认转换结果能作为一段正常对话发送，错误消息不含用户正文。"""

        if not messages:
            raise ConversationError("对话中没有已完成且非空的消息")
        if messages[0].role != "user":
            raise ConversationError("API 对话必须从 user 消息开始")

        for previous, current in zip(messages, messages[1:], strict=False):
            if previous.role == current.role:
                # 正常情况下前一步已经合并；保留校验是为了守住最终输出不变量。
                raise ConversationError("API 消息的 user 与 assistant 必须交替出现")
