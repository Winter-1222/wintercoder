"""Bridge 应用核心测试，不启动真实子进程。"""

import asyncio
from collections.abc import AsyncIterator

from jixue.bridge.application import BridgeApplication
from jixue.domain.events import Envelope
from jixue.llm.fake import FakeLLMClient


async def collect(events: AsyncIterator[Envelope]) -> list[Envelope]:
    return [event async for event in events]


def test_hello_returns_ready_capabilities() -> None:
    application = BridgeApplication(FakeLLMClient(chunk_delay=0))
    command = Envelope.create(
        "bridge.hello",
        "req_hello",
        0,
        {"client_version": "0.1.0"},
    )

    events = asyncio.run(collect(application.handle(command)))

    assert [event.type for event in events] == ["bridge.ready"]
    assert events[0].request_id == "req_hello"
    assert events[0].payload["protocol_version"] == 1
    assert "stream_text" in events[0].payload["capabilities"]


def test_fake_chat_stream_has_ordered_terminal_event() -> None:
    application = BridgeApplication(FakeLLMClient(chunk_delay=0))
    command = Envelope.create(
        "chat.send",
        "req_chat",
        0,
        {
            "session_id": "session_test",
            "text": "请介绍一下你自己",
            "model_id": "fake",
        },
    )

    events = asyncio.run(collect(application.handle(command)))
    types = [event.type for event in events]
    text = "".join(
        str(event.payload["text"]) for event in events if event.type == "stream_text"
    )

    assert types[-2:] == ["usage", "turn_complete"]
    assert "霁雪已经醒来" in text
    assert "FakeLLM" in text
    assert [event.sequence for event in events] == list(range(len(events)))
    assert events[-1].payload["stop_reason"] == "end_turn"
    assert events[-1].payload["model"] == "fake-jixue"


def test_blank_chat_returns_recoverable_error_event() -> None:
    application = BridgeApplication(FakeLLMClient(chunk_delay=0))
    command = Envelope.create("chat.send", "req_blank", 0, {"text": "   "})

    events = asyncio.run(collect(application.handle(command)))

    assert [event.type for event in events] == ["error"]
    assert events[0].payload["code"] == "invalid_input"
    assert events[0].payload["scope"] == "request"


def test_unknown_command_does_not_raise_program_error() -> None:
    application = BridgeApplication(FakeLLMClient(chunk_delay=0))
    command = Envelope.create("unknown", "req_unknown", 0, {})

    events = asyncio.run(collect(application.handle(command)))

    assert [event.type for event in events] == ["error"]
    assert events[0].payload["code"] == "unknown_command"

