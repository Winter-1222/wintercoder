"""把协议命令转换成领域调用与业务事件。

BridgeApplication 是“协议运输”和“模型领域”之间的应用层：它不直接读写 stdin/stdout，
也不知道 Electron 窗口。这样可以用普通 Envelope 测试业务流程，而不必每次启动真实进程。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from time import perf_counter
from uuid import uuid4

from jixue import __version__
from jixue.domain.conversation import ConversationError, ConversationManager
from jixue.domain.events import Envelope
from jixue.domain.messages import Message, MessageStatus, Usage
from jixue.llm.base import LLMClient, LLMClientError, LLMEventType


class BridgeApplication:
    """不依赖标准输入输出的 Bridge 应用核心，便于单元测试。"""

    def __init__(
        self,
        llm: LLMClient,
        conversation: ConversationManager | None = None,
    ) -> None:
        """注入模型和可选历史；不传历史时创建一份当前进程内的会话。"""

        self._llm = llm
        self._conversation = conversation or ConversationManager()
        # 当前 UI 一次只发一条；锁也保护协议调用者，避免两个并发请求交叉改写同一历史。
        self._chat_lock = asyncio.Lock()

    @property
    def messages(self) -> tuple[Message, ...]:
        """返回内部历史快照，供测试和未来会话持久化读取。"""

        return self._conversation.messages

    async def handle(self, command: Envelope) -> AsyncIterator[Envelope]:
        """处理一条命令并产生零到多个事件。"""

        if command.type == "bridge.hello":
            # 握手只确认协议与能力可用，不触发模型，也不产生聊天消息。
            yield Envelope.create(
                "bridge.ready",
                command.request_id,
                0,
                {
                    "protocol_version": command.version,
                    "backend_version": __version__,
                    # model 让 Electron 显示真正被注入的客户端，不再把 FakeLLM 写死在 UI。
                    "model": self._llm.model_name,
                    # 能力描述保持供应商无关；Fake 与正式适配器都支持这两类事件。
                    "capabilities": ["stream_text", "usage"],
                },
            )
            return

        if command.type == "chat.send":
            # async for 把 _handle_chat 产生的每个流式事件立即向上游转交。
            async for event in self._handle_chat(command):
                yield event
            return

        # 未知命令属于可恢复协议反馈：返回 error 信封，BridgeServer 继续运行。
        yield self._error(
            command.request_id,
            "unknown_command",
            f"未知命令：{command.type}",
            scope="command",
        )

    async def _handle_chat(self, command: Envelope) -> AsyncIterator[Envelope]:
        """校验 chat.send，并串行处理当前单会话中的一轮聊天。"""

        text = command.payload.get("text")
        if not isinstance(text, str) or not text.strip():
            # 输入错误不应抛成系统异常，否则一次空消息可能结束整个 Bridge 任务。
            yield self._error(
                command.request_id,
                "invalid_input",
                "消息文本不能为空",
                scope="request",
            )
            return

        async with self._chat_lock:
            # 锁覆盖“加入用户消息 → 模型完成 → 保存助手消息”，保证轮次不会交叉。
            async for event in self._run_chat(command, text.strip()):
                yield event

    async def _run_chat(
        self,
        command: Envelope,
        text: str,
    ) -> AsyncIterator[Envelope]:
        """把一轮消息写入历史、调用 LLM，并保存完整助手回复。"""

        # sequence 在同一个 request_id 内单调递增，帮助接收方识别事件先后顺序。
        sequence = 0
        # 所有文本分片共享同一个 message_id，表示它们属于同一条 assistant 消息。
        message_id = f"msg_{uuid4().hex}"
        # perf_counter 适合计算耗时，不受系统时钟手动调整影响。
        started_at = perf_counter()
        assistant_chunks: list[str] = []
        turn_usage = Usage()

        # 先加入本轮 user，再转换；因此每次 API 请求都以当前问题结尾。
        self._conversation.add_user(text)
        try:
            api_messages = self._conversation.to_api_format()
        except ConversationError as error:
            yield self._error(
                command.request_id,
                "conversation_invalid",
                str(error),
                scope="request",
            )
            return

        try:
            async for llm_event in self._llm.stream(api_messages):
                if llm_event.type == LLMEventType.TEXT:
                    assistant_chunks.append(llm_event.text)
                    # 只放新增片段，不重复发送之前已经输出的完整内容。
                    yield Envelope.create(
                        "stream_text",
                        command.request_id,
                        sequence,
                        {"text": llm_event.text, "message_id": message_id},
                    )
                    sequence += 1
                elif llm_event.type == LLMEventType.USAGE:
                    turn_usage = llm_event.usage
                    cumulative = self._add_usage(
                        self._conversation.total_usage,
                        turn_usage,
                    )
                    yield Envelope.create(
                        "usage",
                        command.request_id,
                        sequence,
                        {
                            "turn": self._usage_payload(llm_event.usage),
                            "cumulative": self._usage_payload(cumulative),
                        },
                    )
                    sequence += 1
                elif llm_event.type == LLMEventType.COMPLETE:
                    # COMPLETE 通常重复携带最终 usage；以它为最终事实，兼容没有单独 USAGE 的实现。
                    turn_usage = llm_event.usage
                    turn_index = 1 + sum(
                        message.role == "assistant"
                        and message.status is MessageStatus.COMPLETE
                        for message in self._conversation.messages
                    )
                    assistant_text = "".join(assistant_chunks)
                    if assistant_text:
                        self._conversation.add_assistant(
                            assistant_text,
                            usage=turn_usage,
                        )
                    # 完成事件是 UI 的收口信号：停止计时、解锁发送并渲染 Markdown。
                    yield Envelope.create(
                        "turn_complete",
                        command.request_id,
                        sequence,
                        {
                            "turn_index": turn_index,
                            "stop_reason": llm_event.stop_reason or "end_turn",
                            "duration_ms": round((perf_counter() - started_at) * 1000),
                            "model": self._llm.model_name,
                            "message_id": message_id,
                        },
                    )
        except LLMClientError as error:
            self._remember_failed_assistant(assistant_chunks)
            # 适配器已经把 SDK 异常翻译成安全的领域错误，Bridge 只负责协议包装。
            yield self._error(
                command.request_id,
                error.code,
                str(error),
                scope="request",
                retryable=error.retryable,
                sequence=sequence,
            )
        except Exception:
            self._remember_failed_assistant(assistant_chunks)
            # 未预期异常不把 repr 或请求内容回显给 UI，避免意外泄露敏感数据。
            yield self._error(
                command.request_id,
                "llm_internal_error",
                "模型客户端发生未预期错误，请查看后端日志",
                scope="request",
                retryable=False,
                sequence=sequence,
            )

    def _remember_failed_assistant(self, chunks: list[str]) -> None:
        """保留 UI 已看到的半截回复，但标记 failed，使它不会进入下一轮 API 历史。"""

        partial_text = "".join(chunks)
        if partial_text:
            self._conversation.add_assistant(
                partial_text,
                status=MessageStatus.FAILED,
            )

    @staticmethod
    def _add_usage(first: Usage, second: Usage) -> Usage:
        """相加两份不可变 Usage，返回新对象而不修改历史。"""

        return Usage(
            input_tokens=first.input_tokens + second.input_tokens,
            output_tokens=first.output_tokens + second.output_tokens,
        )

    @staticmethod
    def _usage_payload(usage: Usage) -> dict[str, int]:
        """把 Usage 转成适合 JSON 传输的两个普通整数。"""

        return {
            "input_tokens": int(usage.input_tokens),
            "output_tokens": int(usage.output_tokens),
        }

    @staticmethod
    def _error(
        request_id: str,
        code: str,
        message: str,
        *,
        scope: str,
        retryable: bool = False,
        sequence: int = 0,
    ) -> Envelope:
        """统一创建可公开给 UI 的错误信封，避免每个分支重复字段结构。"""

        return Envelope.create(
            "error",
            request_id,
            sequence,
            {
                "code": code,
                "message": message,
                "retryable": retryable,
                "scope": scope,
            },
        )
