"""把协议命令转换成领域调用与业务事件。

BridgeApplication 是“协议运输”和“模型领域”之间的应用层：它不直接读写 stdin/stdout，
也不知道 Electron 窗口。这样可以用普通 Envelope 测试业务流程，而不必每次启动真实进程。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from time import perf_counter
from typing import Any
from uuid import uuid4

from jixue import __version__
from jixue.domain.events import Envelope
from jixue.llm.base import LLMClient, LLMClientError, LLMEventType


class BridgeApplication:
    """不依赖标准输入输出的 Bridge 应用核心，便于单元测试。"""

    def __init__(self, llm: LLMClient) -> None:
        """接收任何符合 LLMClient 合同的实现；当前传入 FakeLLMClient。"""

        self._llm = llm

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
                    "capabilities": ["fake_llm", "stream_text", "usage"],
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
        """校验 chat.send，调用 LLM 流，并把领域事件映射为 Bridge 信封。"""

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

        # sequence 在同一个 request_id 内单调递增，帮助接收方识别事件先后顺序。
        sequence = 0
        # 所有文本分片共享同一个 message_id，表示它们属于同一条 assistant 消息。
        message_id = f"msg_{uuid4().hex}"
        # perf_counter 适合计算耗时，不受系统时钟手动调整影响。
        started_at = perf_counter()

        try:
            async for llm_event in self._llm.stream(text):
                if llm_event.type == LLMEventType.TEXT:
                    # 只放新增片段，不重复发送之前已经输出的完整内容。
                    yield Envelope.create(
                        "stream_text",
                        command.request_id,
                        sequence,
                        {"text": llm_event.text, "message_id": message_id},
                    )
                    sequence += 1
                elif llm_event.type == LLMEventType.USAGE:
                    # 当前还没有多轮累计器，所以 turn 和 cumulative 暂时使用同一份用量。
                    yield Envelope.create(
                        "usage",
                        command.request_id,
                        sequence,
                        {
                            "turn": self._usage_payload(llm_event.usage),
                            "cumulative": self._usage_payload(llm_event.usage),
                        },
                    )
                    sequence += 1
                elif llm_event.type == LLMEventType.COMPLETE:
                    # 完成事件是 UI 的收口信号：停止计时、解锁发送并渲染 Markdown。
                    yield Envelope.create(
                        "turn_complete",
                        command.request_id,
                        sequence,
                        {
                            "turn_index": 1,
                            "stop_reason": llm_event.stop_reason or "end_turn",
                            "duration_ms": round((perf_counter() - started_at) * 1000),
                            "model": self._llm.model_name,
                            "message_id": message_id,
                        },
                    )
        except LLMClientError as error:
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
            # 未预期异常不把 repr 或请求内容回显给 UI，避免意外泄露敏感数据。
            yield self._error(
                command.request_id,
                "llm_internal_error",
                "模型客户端发生未预期错误，请查看后端日志",
                scope="request",
                retryable=False,
                sequence=sequence,
            )

    @staticmethod
    def _usage_payload(usage: Any) -> dict[str, int]:
        """把 Usage 一类对象收窄为适合 JSON 传输的两个普通整数。"""

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
