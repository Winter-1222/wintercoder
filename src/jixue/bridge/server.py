"""在 stdin/stdout 上运行一行一个 JSON 的 Bridge。"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import TextIO

from jixue.agent import Agent
from jixue.bridge.application import BridgeApplication
from jixue.bridge.bootstrap import BridgeBootstrapError, create_runtime_llm
from jixue.domain.events import Envelope, ProtocolError
from jixue.llm.base import LLMClient
from jixue.mcp import (
    MCPError,
    MCPToolWrapper,
    StdioMCPClient,
    load_stdio_server_configs,
)
from jixue.tools import (
    ToolRegistry,
    create_bash_tool,
    create_edit_file_tool,
    create_glob_tool,
    create_grep_tool,
    create_read_file_tool,
    create_write_file_tool,
)

MAX_LINE_BYTES = 1024 * 1024


class BridgeServer:
    """stdin 收命令，应用层处理，stdout 发事件。"""

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
        while line := await asyncio.to_thread(self._input.readline):
            if len(line.encode()) > MAX_LINE_BYTES:
                await self._write_error("line_too_large", "协议行超过 1 MiB")
                continue
            try:
                command = Envelope.from_json_line(line)
            except ProtocolError as error:
                await self._write_error("invalid_envelope", str(error))
                continue

            task = asyncio.create_task(self._dispatch(command))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    async def _dispatch(self, command: Envelope) -> None:
        try:
            async for event in self._application.handle(command):
                await self._write(event)
        except Exception as error:
            self._error.write(f"Bridge 处理命令失败：{error}\n")
            self._error.flush()

    async def _write(self, event: Envelope) -> None:
        async with self._write_lock:
            self._output.write(event.to_json_line() + "\n")
            self._output.flush()

    async def _write_error(self, code: str, message: str) -> None:
        await self._write(
            Envelope.create(
                "error",
                "bridge",
                0,
                {"code": code, "message": message, "retryable": False, "scope": "bridge"},
            )
        )


def main() -> None:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    project_root = Path.cwd().resolve()
    tools = ToolRegistry()
    tools.register(create_read_file_tool())
    tools.register(create_glob_tool())
    tools.register(create_grep_tool())
    # 写入和命令工具只有在权限确认链路就绪后才注册，避免模型绕过用户确认。
    tools.register(create_write_file_tool())
    tools.register(create_edit_file_tool())
    tools.register(create_bash_tool())
    try:
        llm = create_runtime_llm(project_root)
        asyncio.run(_run_bridge(llm, tools, project_root))
    except (BridgeBootstrapError, MCPError) as error:
        sys.stderr.write(f"Bridge 启动配置错误：{error}\n")
        raise SystemExit(2) from error


async def _run_bridge(
    llm: LLMClient,
    tools: ToolRegistry,
    project_root: Path,
) -> None:
    """先注册 MCP 工具，再启动 Agent；退出时统一关闭 MCP 子进程。"""

    clients: list[StdioMCPClient] = []
    try:
        for config in load_stdio_server_configs(project_root):
            client = StdioMCPClient(config, project_root)
            definitions = await client.connect()
            clients.append(client)
            for definition in definitions:
                tools.register(MCPToolWrapper(client, definition))
            sys.stderr.write(
                f"MCP Server 已连接：{config.name}（发现 {len(definitions)} 个工具）\n"
            )
            sys.stderr.flush()

        # MCP 包装后的工具已经在 Registry 中，Agent Loop 不需要知道它们的来源。
        agent = Agent(llm, tools=tools)
        server = BridgeServer(
            BridgeApplication(agent),
            sys.stdin,
            sys.stdout,
            sys.stderr,
        )
        await server.run()
    finally:
        for client in reversed(clients):
            await client.close()


if __name__ == "__main__":
    main()
