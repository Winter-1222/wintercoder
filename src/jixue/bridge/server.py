"""在 stdin/stdout 上运行一行一个 JSON 的 Bridge。"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Awaitable, Callable
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
    StdioServerConfig,
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
type EventEmitter = Callable[[Envelope], Awaitable[None]]


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

    async def emit(self, event: Envelope) -> None:
        """允许后台任务复用同一把写锁，避免两条 JSON 输出互相穿插。"""

        await self._write(event)


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
    """立即启动 Agent，同时在后台连接 MCP Server。"""

    clients: list[StdioMCPClient] = []
    agent = Agent(llm, tools=tools)
    server = BridgeServer(
        BridgeApplication(agent),
        sys.stdin,
        sys.stdout,
        sys.stderr,
    )
    connect_task = asyncio.create_task(
        _connect_mcp_servers(tools, project_root, clients, server.emit)
    )
    try:
        await server.run()
    finally:
        connect_task.cancel()
        await asyncio.gather(connect_task, return_exceptions=True)
        for client in reversed(clients):
            await client.close()


async def _connect_mcp_servers(
    tools: ToolRegistry,
    project_root: Path,
    clients: list[StdioMCPClient],
    emit: EventEmitter,
) -> None:
    """并行后台连接；一个 Server 失败不会影响 Bridge 和其他 Server。"""

    try:
        configs = load_stdio_server_configs(project_root)
    except MCPError as error:
        await emit(_mcp_status("config", "failed", str(error)))
        return

    # 每个 Server 都有自己的任务；一个连接慢，不会挡住其他 Server。
    await asyncio.gather(
        *(_connect_one_mcp_server(config, tools, project_root, clients, emit) for config in configs)
    )


async def _connect_one_mcp_server(
    config: StdioServerConfig,
    tools: ToolRegistry,
    project_root: Path,
    clients: list[StdioMCPClient],
    emit: EventEmitter,
) -> None:
    """连接一个 Server，并把成功或失败都转换成状态事件。"""

    await emit(_mcp_status(config.name, "connecting", "正在连接"))
    client = StdioMCPClient(config, project_root)
    try:
        definitions = await client.connect()
        wrappers = [MCPToolWrapper(client, definition) for definition in definitions]
        names = [tool.name() for tool in wrappers]
        if len(names) != len(set(names)) or any(tools.get(name) for name in names):
            raise MCPError(f"MCP Server {config.name} 产生了重复工具名")
        for tool in wrappers:
            tools.register(tool)
        clients.append(client)
    except (MCPError, ValueError) as error:
        await client.close()
        detail = str(error)[:500]
        sys.stderr.write(f"MCP Server 连接失败：{config.name}：{detail}\n")
        sys.stderr.flush()
        await emit(_mcp_status(config.name, "failed", detail))
        return

    detail = f"已连接，发现 {len(definitions)} 个工具"
    sys.stderr.write(f"MCP Server {config.name}：{detail}\n")
    sys.stderr.flush()
    await emit(_mcp_status(config.name, "connected", detail, len(definitions)))


def _mcp_status(
    name: str,
    status: str,
    detail: str,
    tool_count: int = 0,
) -> Envelope:
    """所有 MCP 状态使用相同事件形状，Electron 只需解析一次。"""

    return Envelope.create(
        "mcp.status",
        f"mcp_{name}",
        0,
        {"name": name, "status": status, "detail": detail, "tool_count": tool_count},
    )


if __name__ == "__main__":
    main()
