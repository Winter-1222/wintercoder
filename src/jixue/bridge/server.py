"""使用标准输入输出运行 NDJSON Bridge。

BridgeServer 只负责运输：从 stdin 读一行、解析为 Envelope、交给应用层，再把应用层
事件逐行写到 stdout。业务日志只能进入 stderr，不能混入 NDJSON 协议通道。
"""

from __future__ import annotations

import asyncio
import sys
from typing import TextIO

from jixue.bridge.application import BridgeApplication
from jixue.domain.events import Envelope, ProtocolError
from jixue.llm.fake import FakeLLMClient

# 单条命令最多 1 MiB，避免没有换行的恶意或错误输入持续占用内存。
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
        """注入应用层和三个文本流，真实运行用 sys.stdin/out/err，测试可传内存流。"""

        self._application = application
        self._input = input_stream
        self._output = output_stream
        self._error = error_stream
        # 多个请求可以并发处理，但 stdout 必须逐行串行写，避免 JSON 相互穿插。
        self._write_lock = asyncio.Lock()
        # 保存仍在运行的请求任务，stdin 关闭时统一取消并等待清理。
        self._tasks: set[asyncio.Task[None]] = set()

    async def run(self) -> None:
        """持续读取命令，直到 stdin 关闭。"""

        while True:
            # 标准 TextIO.readline 是同步函数，用 to_thread 避免堵塞 asyncio 事件循环。
            line = await asyncio.to_thread(self._input.readline)
            if line == "":
                # 空字符串表示 EOF，通常来自 Electron 关闭 stdin 或父进程退出。
                break

            if len(line.encode("utf-8")) > MAX_LINE_BYTES:
                # 超限只拒绝当前行，返回错误后继续读取下一条命令。
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
                # JSON 或外层字段错误属于调用方问题，用 error 信封反馈，不让服务退出。
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

            # 每条合法命令成为独立 task，长回复不会阻止 Server 继续读取 stdin。
            task = asyncio.create_task(self._dispatch(command))
            self._tasks.add(task)
            # 任务结束后自动从集合删除，避免集合永久持有已完成任务。
            task.add_done_callback(self._tasks.discard)

        # stdin 已关闭，不再接受工作；清理仍未完成的请求后结束进程。
        await self._cancel_pending()

    async def _dispatch(self, command: Envelope) -> None:
        """消费应用层的异步事件流，并逐条写入 stdout。"""

        try:
            async for event in self._application.handle(command):
                await self._write_event(event)
        except Exception as exc:
            # stderr 只用于开发诊断，stdout 始终保持纯 NDJSON。
            self._error.write(f"Bridge 处理命令失败：{exc}\n")
            self._error.flush()

    async def _write_event(self, event: Envelope) -> None:
        """在写锁保护下输出一条完整 NDJSON，并立即 flush。"""

        async with self._write_lock:
            # to_json_line 不包含换行；这里统一添加消息边界。
            self._output.write(event.to_json_line() + "\n")
            # 不等待缓冲区攒满，保证文本片段能尽快到达 Electron。
            self._output.flush()

    async def _cancel_pending(self) -> None:
        """取消并等待所有未完成任务，避免关闭事件循环时留下悬挂协程。"""

        for task in self._tasks:
            task.cancel()
        if self._tasks:
            # return_exceptions=True 把取消视为清理结果，不因一个任务再次中断清理。
            await asyncio.gather(*self._tasks, return_exceptions=True)


def main() -> None:
    """组装默认 FakeLLM 应用，并阻塞运行 BridgeServer 直到 stdin 关闭。"""

    # Windows 终端默认编码不稳定，协议通道统一使用 UTF-8 和 LF。
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", newline="\n")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", newline="\n")

    # 依赖在最外层组装：应用层依赖 LLMClient，当前选择无需 Key 的 FakeLLM。
    application = BridgeApplication(FakeLLMClient())
    server = BridgeServer(application, sys.stdin, sys.stdout, sys.stderr)
    # asyncio.run 创建事件循环、运行协程，并在结束后负责关闭事件循环。
    asyncio.run(server.run())


if __name__ == "__main__":
    main()
