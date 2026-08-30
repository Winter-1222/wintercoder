"""协议事件的边界测试。"""

import json

import pytest

from jixue.domain.events import PROTOCOL_VERSION, Envelope, ProtocolError


def test_envelope_round_trip_preserves_chinese() -> None:
    event = Envelope.create(
        "stream_text",
        "req_test",
        3,
        {"text": "雪后初晴"},
    )

    parsed = Envelope.from_json_line(event.to_json_line())

    assert parsed == event
    assert "\\u96ea" not in event.to_json_line()


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ("not-json", "无法解析 JSON"),
        ("[]", "JSON 对象"),
        (
            json.dumps(
                {
                    "version": PROTOCOL_VERSION + 1,
                    "type": "bridge.hello",
                    "request_id": "req_test",
                    "sequence": 0,
                    "timestamp": "now",
                    "payload": {},
                }
            ),
            "协议版本",
        ),
        (
            json.dumps(
                {
                    "version": PROTOCOL_VERSION,
                    "type": "bridge.hello",
                    "request_id": "",
                    "sequence": 0,
                    "timestamp": "now",
                    "payload": {},
                }
            ),
            "request_id",
        ),
    ],
)
def test_invalid_envelope_is_rejected(raw: str, message: str) -> None:
    with pytest.raises(ProtocolError, match=message):
        Envelope.from_json_line(raw)


def test_json_line_does_not_contain_newline() -> None:
    event = Envelope.create("test", "req_test", 0, {"text": "a\nb"})

    line = event.to_json_line()

    assert "\n" not in line

