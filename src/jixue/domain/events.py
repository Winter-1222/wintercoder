"""Electron 与 Python 之间共享的协议事件。

跨进程不能直接传 Python 对象，所以所有命令和事件先放入 Envelope，再序列化为
一行 JSON。固定外层字段负责版本、路由和排序，payload 保存每种事件的业务内容。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

# 协议结构发生不兼容变化时才提升主版本；两端不一致就明确拒绝。
PROTOCOL_VERSION = 1


class ProtocolError(ValueError):
    """表示调用方输入不符合协议，可转换为 error 信封而不是让服务崩溃。"""


@dataclass(frozen=True, slots=True)
class Envelope:
    """跨进程传输的最小信封。

    payload 保持为普通映射，避免让 Electron、LLM SDK 或未来 MCP SDK 的类型
    泄漏进领域层。
    """

    version: int
    type: str
    request_id: str
    sequence: int
    timestamp: str
    payload: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        event_type: str,
        request_id: str,
        sequence: int,
        payload: Mapping[str, Any] | None = None,
    ) -> Envelope:
        """创建带 UTC 时间戳的新事件，并复制 payload 防止外部随后修改。"""

        return cls(
            version=PROTOCOL_VERSION,
            type=event_type,
            request_id=request_id,
            sequence=sequence,
            timestamp=datetime.now(UTC).isoformat(),
            # dict() 创建普通副本；调用方传入的 Mapping 不会成为内部可变共享状态。
            payload=dict(payload or {}),
        )

    @classmethod
    def from_json_line(cls, line: str) -> Envelope:
        """从一行 JSON 解析并完成边界校验，成功后返回不可变 Envelope。"""

        try:
            # json.loads 只负责语法解析，不会检查业务字段是否齐全。
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ProtocolError(f"无法解析 JSON：{exc.msg}") from exc

        if not isinstance(raw, dict):
            raise ProtocolError("协议消息必须是 JSON 对象")

        # 所有命令和事件都必须具备相同外层字段，消费方才能统一路由和诊断。
        required = ("version", "type", "request_id", "sequence", "timestamp", "payload")
        missing = [key for key in required if key not in raw]
        if missing:
            raise ProtocolError(f"协议消息缺少字段：{', '.join(missing)}")

        # 下面逐字段做运行时检查。类型注解无法保护来自 stdin 的外部 JSON。
        if raw["version"] != PROTOCOL_VERSION:
            raise ProtocolError(f"不支持的协议版本：{raw['version']}")
        if not isinstance(raw["type"], str) or not raw["type"].strip():
            raise ProtocolError("type 必须是非空字符串")
        if not isinstance(raw["request_id"], str) or not raw["request_id"].strip():
            raise ProtocolError("request_id 必须是非空字符串")
        if not isinstance(raw["sequence"], int) or raw["sequence"] < 0:
            raise ProtocolError("sequence 必须是非负整数")
        if not isinstance(raw["timestamp"], str) or not raw["timestamp"].strip():
            raise ProtocolError("timestamp 必须是非空字符串")
        if not isinstance(raw["payload"], dict):
            raise ProtocolError("payload 必须是 JSON 对象")

        # 校验通过后才构造领域对象，后续应用层可以信任这些外层字段。
        return cls(
            version=raw["version"],
            type=raw["type"],
            request_id=raw["request_id"],
            sequence=raw["sequence"],
            timestamp=raw["timestamp"],
            payload=raw["payload"],
        )

    def to_json_line(self) -> str:
        """序列化为不含换行符的一行 JSON；换行边界由 BridgeServer 添加。"""

        return json.dumps(
            {
                "version": self.version,
                "type": self.type,
                "request_id": self.request_id,
                "sequence": self.sequence,
                "timestamp": self.timestamp,
                "payload": dict(self.payload),
            },
            # 中文保持可读，不转成 \uXXXX；紧凑 separators 减少协议开销。
            ensure_ascii=False,
            separators=(",", ":"),
        )
