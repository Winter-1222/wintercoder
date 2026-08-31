"""Electron 与 Python 共用的一行 JSON 信封。"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime

PROTOCOL_VERSION = 1


class ProtocolError(ValueError):
    """输入不是合法协议消息。"""


@dataclass(frozen=True, slots=True)
class Envelope:
    version: int
    type: str
    request_id: str
    sequence: int
    timestamp: str
    payload: Mapping[str, object] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        event_type: str,
        request_id: str,
        sequence: int,
        payload: Mapping[str, object] | None = None,
    ) -> Envelope:
        return cls(
            PROTOCOL_VERSION,
            event_type,
            request_id,
            sequence,
            datetime.now(UTC).isoformat(),
            dict(payload or {}),
        )

    @classmethod
    def from_json_line(cls, line: str) -> Envelope:
        try:
            value: object = json.loads(line)
        except json.JSONDecodeError as error:
            raise ProtocolError(f"无法解析 JSON：{error.msg}") from error
        if not isinstance(value, dict):
            raise ProtocolError("协议消息必须是 JSON 对象")

        required = {"version", "type", "request_id", "sequence", "timestamp", "payload"}
        missing = required - value.keys()
        if missing:
            raise ProtocolError(f"协议消息缺少字段：{', '.join(sorted(missing))}")

        version = value["version"]
        event_type = value["type"]
        request_id = value["request_id"]
        sequence = value["sequence"]
        timestamp = value["timestamp"]
        payload = value["payload"]
        if version != PROTOCOL_VERSION:
            raise ProtocolError(f"不支持的协议版本：{version}")
        if not isinstance(event_type, str) or not event_type:
            raise ProtocolError("type 必须是非空字符串")
        if not isinstance(request_id, str) or not request_id:
            raise ProtocolError("request_id 必须是非空字符串")
        if not isinstance(sequence, int) or sequence < 0:
            raise ProtocolError("sequence 必须是非负整数")
        if not isinstance(timestamp, str) or not timestamp:
            raise ProtocolError("timestamp 必须是非空字符串")
        if not isinstance(payload, dict):
            raise ProtocolError("payload 必须是 JSON 对象")

        clean_payload = {str(key): item for key, item in payload.items()}
        return cls(version, event_type, request_id, sequence, timestamp, clean_payload)

    def to_json_line(self) -> str:
        return json.dumps(
            {
                "version": self.version,
                "type": self.type,
                "request_id": self.request_id,
                "sequence": self.sequence,
                "timestamp": self.timestamp,
                "payload": dict(self.payload),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
