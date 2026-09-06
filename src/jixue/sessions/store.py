"""项目内追加式 JSONL：界面记录用于回放，最新快照用于继续推理。"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from jixue.domain.conversation import ConversationManager
from jixue.domain.events import Envelope
from jixue.sessions.codec import decode_conversation, encode_conversation


@dataclass
class RestoredSession:
    session_id: str
    conversation: ConversationManager
    events: list[Envelope]


class SessionStore:
    """只在任务开始和结束写盘；不为每个流式字符创建磁盘记录。"""

    def __init__(self, project_root: Path) -> None:
        self.root = project_root.resolve()
        self.directory = self._safe(self.root / ".jixue" / "sessions")
        self.directory.mkdir(parents=True, exist_ok=True)

    def _safe(self, path: Path) -> Path:
        target = path.resolve()
        if not target.is_relative_to(self.root):
            raise ValueError("会话路径越过项目目录")
        return target

    def _path(self, session_id: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{32}", session_id):
            raise ValueError("会话编号无效")
        return self._safe(self.directory / f"{session_id}.jsonl")

    def create(self) -> RestoredSession:
        session_id = uuid4().hex
        self.append(
            session_id,
            {"type": "session", "version": 1, "created_at": datetime.now(UTC).isoformat()},
        )
        self.select(session_id)
        return RestoredSession(session_id, ConversationManager(), [])

    def select(self, session_id: str) -> None:
        if not self._path(session_id).is_file():
            raise ValueError("会话不存在")
        pointer = self._safe(self.directory / "current.txt")
        temporary = self._safe(self.directory / "current.tmp")
        temporary.write_text(session_id, encoding="utf-8")
        temporary.replace(pointer)

    def current(self) -> RestoredSession:
        pointer = self._safe(self.directory / "current.txt")
        if not pointer.exists():
            return self.create()
        return self.load(pointer.read_text(encoding="utf-8").strip())

    def records(self, session_id: str) -> Iterator[dict[str, Any]]:
        with self._path(session_id).open("rb") as stream:
            for line in stream:
                # 没有换行的末尾属于断电留下的半条记录，不能当成已提交快照。
                if not line.endswith(b"\n"):
                    break
                record = json.loads(line)
                if not isinstance(record, dict):
                    raise ValueError("会话记录必须是对象")
                yield record

    def append(self, session_id: str, record: dict[str, object]) -> None:
        path = self._path(session_id)
        payload = (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")
        with path.open("a+b") as stream:
            # 先移除未提交的末尾，避免下一次追加把两条 JSON 粘在一起。
            stream.seek(0, os.SEEK_END)
            end = stream.tell()
            while end:
                start = max(0, end - 8192)
                stream.seek(start)
                chunk = stream.read(end - start)
                newline = chunk.rfind(b"\n")
                if newline >= 0:
                    end = start + newline + 1
                    break
                end = start
            stream.truncate(end)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())

    def start_turn(self, session_id: str, request_id: str, text: str) -> None:
        self.append(
            session_id,
            {
                "type": "turn_started",
                "request_id": request_id,
                "text": text,
                "timestamp": datetime.now(UTC).isoformat(),
            },
        )

    def finish_turn(
        self, session_id: str, conversation: ConversationManager, events: list[Envelope]
    ) -> None:
        self.append(
            session_id,
            {
                "type": "turn_finished",
                "snapshot": encode_conversation(conversation),
                "events": [json.loads(event.to_json_line()) for event in events],
            },
        )

    def load(self, session_id: str) -> RestoredSession:
        records = iter(self.records(session_id))
        header = next(records, {})
        if header.get("type") != "session" or header.get("version") != 1:
            raise ValueError("会话文件格式或版本无效")
        snapshot = None
        events: list[Envelope] = []
        pending: str | None = None
        for record in records:
            if record["type"] == "turn_started":
                if pending:
                    events.append(_interrupted(pending))
                pending = record["request_id"]
                events.append(Envelope.create("session.turn", pending, 0, {"text": record["text"]}))
            elif record["type"] == "turn_finished":
                snapshot = record["snapshot"]
                events.extend(
                    Envelope.from_json_line(json.dumps(item)) for item in record["events"]
                )
                pending = None
            else:
                raise ValueError("会话记录类型无效")
        if pending:
            events.append(_interrupted(pending))
        conversation = (
            decode_conversation(snapshot) if snapshot is not None else ConversationManager()
        )
        return RestoredSession(session_id, conversation, events)

    def list_sessions(self) -> list[dict[str, object]]:
        result = []
        for path in self.directory.glob("*.jsonl"):
            try:
                records = iter(self.records(path.stem))
                header = next(records)
                first_turn = next(records, {})
                title = str(first_turn.get("text", "新会话")).replace("\n", " ")[:40]
                result.append(
                    {
                        "id": path.stem,
                        "title": title,
                        "created_at": header["created_at"],
                        "updated_at": path.stat().st_mtime,
                    }
                )
            except (OSError, ValueError, KeyError, StopIteration):
                continue
        return sorted(result, key=lambda item: float(str(item["updated_at"])), reverse=True)


def _interrupted(request_id: str) -> Envelope:
    return Envelope.create(
        "error", request_id, 0, {"message": "上次任务因进程退出而中断；未自动重跑工具。"}
    )


def collect_event(events: list[Envelope], event: Envelope) -> None:
    """合并相邻文字分片，保留工具事件顺序，并限制单条回放信封大小。"""
    if (
        events
        and event.type == "stream_text"
        and events[-1].type == "stream_text"
        and events[-1].payload.get("message_id") == event.payload.get("message_id")
        and len(str(events[-1].payload.get("text", ""))) < 8000
    ):
        previous = events[-1]
        events[-1] = Envelope.create(
            event.type,
            event.request_id,
            previous.sequence,
            {**event.payload, "text": str(previous.payload["text"]) + str(event.payload["text"])},
        )
    else:
        events.append(event)
