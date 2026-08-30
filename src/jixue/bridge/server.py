"""使用标准输入输出运行 NDJSON Bridge。"""

from __future__ import annotations

import asyncio
import sys
from typing import TextIO

from jixue.bridge.application import BridgeApplication
from jixue.domain.events import Envelope, ProtocolError
from jixue.llm.fake import FakeLLMClient

MAX_LINE_BYTES = 1024 * 1024


class BridgeServer:
    """读取命令、并发分发请求，并串行写出协议事件。"""

    def __init__(
        self,
        application: BridgeApplication,
        input_stream: TextIO,
        output_stream: TextIO,
        error_stream: TextIO,
    ) -> None:
        self._application = application
        self._input = input_stream
        self._output = output_stream
        self._error = error_stream
        self._write_lock = asyncio.Lock()
        self._tasks: set[asyncio.Task[None]] = set()

    async def run(self) -> None:
        """持续读取命令，直到 stdin 关闭。"""

        while True:
            line = await asyncio.to_thread(self._input.readline)
            if line == "":
                break

            if len(line.encode("utf-8")) > MAX_LINE_BYTES:
                await self._write_event(
                    Envelope.create(
                        "error",
                        "bridge",
                        0,
                        {
                            "code": "line_too_large",
                            "message": "协议行超过 1 MiB 限制",
                            "retryable": False,
                            "scope": "bridge",
                        },
                    )
                )
                continue

            try:
                command = Envelope.from_json_line(line)
            except ProtocolError as exc:
                await self._write_event(
                    Envelope.create(
                        "error",
                        "bridge",
                        0,
                        {
                            "code": "invalid_envelope",
                            "message": str(exc),
                            "retryable": False,
                            "scope": "bridge",
                        },
                    )
                )
                continue

            task = asyncio.create_task(self._dispatch(command))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

        await self._cancel_pending()

    async def _dispatch(self, command: Envelope) -> None:
        try:
            async for event in self._application.handle(command):
                await self._write_event(event)
        except Exception as exc:
            # stderr 只用于开发诊断，stdout 始终保持纯 NDJSON。
            self._error.write(f"Bridge 处理命令失败：{exc}\n")
            self._error.flush()

    async def _write_event(self, event: Envelope) -> None:
        async with self._write_lock:
            self._output.write(event.to_json_line() + "\n")
            self._output.flush()

    async def _cancel_pending(self) -> None:
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)


def main() -> None:
    """命令行入口。"""

    # Windows 终端默认编码不稳定，协议通道统一使用 UTF-8 和 LF。
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", newline="\n")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", newline="\n")

    application = BridgeApplication(FakeLLMClient())
    server = BridgeServer(application, sys.stdin, sys.stdout, sys.stderr)
    asyncio.run(server.run())


if __name__ == "__main__":
    main()
