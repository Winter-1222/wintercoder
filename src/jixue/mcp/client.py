"""MCP transport 合同、stdio/HTTP 客户端和本地配置读取。"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Awaitable, Callable, Mapping
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any, Protocol, cast
from urllib.parse import urlsplit

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

MCP_TOOL_TIMEOUT_SECONDS = 60
ENV_REFERENCE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

# SDK 会为一次普通网络失败打印整页 post_writer 堆栈；霁雪已经会给 UI 输出安全错误。
logging.getLogger("mcp.client.streamable_http").setLevel(logging.CRITICAL)


class MCPError(RuntimeError):
    """MCP 配置、连接或调用失败；错误消息可以显示给用户。"""


@dataclass(frozen=True, slots=True)
class StdioServerConfig:
    """启动一个本地 MCP 子进程所需的最小配置。"""

    name: str
    command: str
    args: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class StreamableHTTPServerConfig:
    """连接远程 Streamable HTTP Server 只需要名称和端点 URL。"""

    name: str
    url: str


type MCPServerConfig = StdioServerConfig | StreamableHTTPServerConfig


@dataclass(frozen=True, slots=True)
class MCPToolDefinition:
    """与 MCP SDK 解耦的工具定义，供霁雪工具层使用。"""

    name: str
    description: str
    input_schema: Mapping[str, object]
    read_only: bool
    destructive: bool


@dataclass(frozen=True, slots=True)
class MCPCallResult:
    """与 MCP SDK 解耦的调用结果。"""

    content: str
    is_error: bool = False
    metadata: Mapping[str, object] = field(default_factory=dict)


class MCPTransport(Protocol):
    """stdio 和 HTTP 都遵守这个小接口，工具包装器不关心连接方式。"""

    @property
    def server_name(self) -> str: ...

    async def connect(self) -> tuple[MCPToolDefinition, ...]: ...

    async def call_tool(self, name: str, tool_input: Mapping[str, object]) -> MCPCallResult: ...

    async def close(self) -> None: ...


class _SessionMCPClient:
    """两种 transport 共用的 session、调用和关闭逻辑。"""

    def __init__(self, server_name: str, *, hide_error_detail: bool = False) -> None:
        self._server_name = server_name
        self._hide_error_detail = hide_error_detail
        self._session: ClientSession | None = None
        self._runner: asyncio.Task[None] | None = None
        self._stop_event: asyncio.Event | None = None

    @property
    def server_name(self) -> str:
        return self._server_name

    def _ensure_disconnected(self) -> None:
        if self._runner is not None:
            raise MCPError(f"MCP Server 已连接：{self.server_name}")

    async def _start(
        self,
        open_streams: Callable[[AsyncExitStack], Awaitable[tuple[Any, Any]]],
        *,
        hide_connect_detail: bool = False,
    ) -> tuple[MCPToolDefinition, ...]:
        """让一个长期 Task 同时负责打开和关闭 AnyIO 连接上下文。"""

        self._ensure_disconnected()
        ready: asyncio.Future[tuple[MCPToolDefinition, ...]] = (
            asyncio.get_running_loop().create_future()
        )
        stop_event = asyncio.Event()
        self._stop_event = stop_event
        self._runner = asyncio.create_task(
            self._run_connection(open_streams, ready, stop_event, hide_connect_detail)
        )
        try:
            return await ready
        except BaseException:
            stop_event.set()
            self._runner.cancel()
            await asyncio.gather(self._runner, return_exceptions=True)
            self._runner = None
            self._stop_event = None
            raise

    async def _run_connection(
        self,
        open_streams: Callable[[AsyncExitStack], Awaitable[tuple[Any, Any]]],
        ready: asyncio.Future[tuple[MCPToolDefinition, ...]],
        stop_event: asyncio.Event,
        hide_connect_detail: bool,
    ) -> None:
        stack = AsyncExitStack()
        try:
            read_stream, write_stream = await open_streams(stack)
            session = await stack.enter_async_context(ClientSession(read_stream, write_stream))
            # 官方 initialize() 内部还会自动发送 notifications/initialized。
            await session.initialize()
            self._session = session
            ready.set_result(await _list_tools(session, self.server_name))
            await stop_event.wait()
        except BaseException as error:
            if not ready.done():
                if isinstance(error, (MemoryError, SystemExit, KeyboardInterrupt)):
                    ready.set_exception(error)
                else:
                    detail = type(error).__name__ if hide_connect_detail else str(error)
                    ready.set_exception(
                        MCPError(f"MCP Server {self.server_name} 连接失败：{detail}")
                    )
            elif not isinstance(error, asyncio.CancelledError):
                raise
        finally:
            self._session = None
            await stack.aclose()

    async def call_tool(
        self,
        name: str,
        tool_input: Mapping[str, object],
    ) -> MCPCallResult:
        try:
            return await _call_tool(self._require_session(), self.server_name, name, tool_input)
        except Exception as error:
            if not self._hide_error_detail:
                raise
            raise MCPError(
                f"MCP Server {self.server_name} HTTP 调用失败：{type(error).__name__}"
            ) from error

    async def close(self) -> None:
        """通知连接 Task 自己退出上下文，避免跨 Task 关闭 AnyIO cancel scope。"""

        runner = self._runner
        stop_event = self._stop_event
        self._runner = None
        self._stop_event = None
        if runner is not None and stop_event is not None:
            stop_event.set()
            await runner

    def _require_session(self) -> ClientSession:
        if self._session is None:
            raise MCPError(f"MCP Server 尚未连接：{self.server_name}")
        return self._session


class StdioMCPClient(_SessionMCPClient):
    """启动本地子进程，并把 stdio 交给公共 MCP session。"""

    def __init__(self, config: StdioServerConfig, project_root: Path) -> None:
        super().__init__(config.name)
        self._config = config
        self._project_root = project_root.resolve()

    async def connect(self) -> tuple[MCPToolDefinition, ...]:
        async def open_streams(stack: AsyncExitStack) -> tuple[Any, Any]:
            return await stack.enter_async_context(
                stdio_client(
                    StdioServerParameters(
                        command=self._config.command,
                        args=list(self._config.args),
                        cwd=self._project_root,
                    )
                )
            )

        return await self._start(open_streams)


class StreamableHTTPMCPClient(_SessionMCPClient):
    """连接远程 HTTP 端点，并把网络流交给公共 MCP session。"""

    def __init__(self, config: StreamableHTTPServerConfig) -> None:
        super().__init__(config.name, hide_error_detail=True)
        self._config = config

    async def connect(self) -> tuple[MCPToolDefinition, ...]:
        async def open_streams(stack: AsyncExitStack) -> tuple[Any, Any]:
            read_stream, write_stream, _get_session_id = await stack.enter_async_context(
                streamable_http_client(self._config.url)
            )
            return read_stream, write_stream

        # HTTP 异常可能包含带 Key 的 URL，所以不向上层传原始错误文本。
        return await self._start(open_streams, hide_connect_detail=True)


async def _list_tools(
    session: ClientSession,
    server_name: str,
) -> tuple[MCPToolDefinition, ...]:
    """HTTP 与 stdio 共用同一种分页工具定义转换。"""

    definitions: list[MCPToolDefinition] = []
    cursor: str | None = None
    while True:
        result = await session.list_tools(cursor)
        for tool in result.tools:
            annotations = tool.annotations
            read_only = annotations is not None and annotations.readOnlyHint is True
            destructive_hint = annotations.destructiveHint if annotations is not None else None
            definitions.append(
                MCPToolDefinition(
                    name=tool.name,
                    description=tool.description or f"调用 {server_name} 的 {tool.name}",
                    input_schema=cast(Mapping[str, object], tool.inputSchema),
                    read_only=read_only,
                    destructive=not read_only if destructive_hint is None else destructive_hint,
                )
            )
        cursor = result.nextCursor
        if not cursor:
            return tuple(definitions)


async def _call_tool(
    session: ClientSession,
    server_name: str,
    name: str,
    tool_input: Mapping[str, object],
) -> MCPCallResult:
    """HTTP 与 stdio 共用同一种 MCP 结果转换。"""

    # 远程 Server 失联不能永久占住 Agent；超时会由 Wrapper 变成可供模型处理的错误结果。
    result = await session.call_tool(
        name,
        dict(tool_input),
        read_timeout_seconds=timedelta(seconds=MCP_TOOL_TIMEOUT_SECONDS),
    )
    parts: list[str] = []
    for block in result.content:
        if block.type == "text":
            parts.append(block.text)
        elif block.type == "resource_link":
            parts.append(f"[资源链接] {block.name}: {block.uri}")
        elif block.type == "resource":
            resource_text = getattr(block.resource, "text", None)
            parts.append(resource_text or "[二进制资源未展开]")
        elif block.type == "image":
            parts.append(f"[图片内容：{block.mimeType}，暂未在文本结果中展开]")
        elif block.type == "audio":
            parts.append(f"[音频内容：{block.mimeType}，暂未在文本结果中展开]")

    if not parts and result.structuredContent is not None:
        parts.append(json.dumps(result.structuredContent, ensure_ascii=False))
    return MCPCallResult(
        content="\n".join(parts) or "MCP 工具执行完成，但没有返回文本内容。",
        is_error=result.isError,
        metadata={"server": server_name, "remote_tool": name},
    )


def create_mcp_client(config: MCPServerConfig, project_root: Path) -> MCPTransport:
    """配置决定 transport；Bridge 不需要了解两种客户端的构造差异。"""

    if isinstance(config, StdioServerConfig):
        return StdioMCPClient(config, project_root)
    return StreamableHTTPMCPClient(config)


def load_server_configs(
    project_root: Path,
    *,
    environ: Mapping[str, str] | None = None,
) -> tuple[MCPServerConfig, ...]:
    """读取配置并替换 ${变量名}；真实 Key 只需要保存在项目 .env。"""

    environment = environ or {}
    servers: dict[str, Mapping[str, object]] = {}
    for filename in ("mcp.json", "mcp.local.json"):
        path = project_root / "config" / filename
        if path.is_file():
            servers.update(_read_servers(path))

    configs: list[MCPServerConfig] = []
    for name in sorted(servers):
        raw = servers[name]
        if raw.get("enabled", True) is False:
            continue
        transport = raw.get("transport", "stdio")
        if transport == "stdio":
            command = raw.get("command")
            args = raw.get("args", [])
            if not isinstance(command, str) or not command.strip():
                raise MCPError(f"MCP Server {name}：command 必须是非空字符串")
            if not isinstance(args, list) or not all(isinstance(item, str) for item in args):
                raise MCPError(f"MCP Server {name}：args 必须是字符串数组")
            configs.append(StdioServerConfig(name, command, tuple(args)))
            continue
        if transport == "streamable_http":
            url = raw.get("url")
            if not isinstance(url, str):
                raise MCPError(f"MCP Server {name}：url 必须是完整的 http 或 https 地址")
            url = _expand_environment(url, environment, name)
            if not _is_http_url(url):
                raise MCPError(f"MCP Server {name}：url 必须是完整的 http 或 https 地址")
            configs.append(StreamableHTTPServerConfig(name, url))
            continue
        raise MCPError(f"MCP Server {name}：transport 只支持 stdio 或 streamable_http")
    return tuple(configs)


def _expand_environment(value: str, environ: Mapping[str, str], server_name: str) -> str:
    """把 URL 中的 ${NAME} 换成环境值；错误只显示变量名，不泄露 URL。"""

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        replacement = environ.get(name, "")
        if not replacement:
            raise MCPError(f"MCP Server {server_name}：.env 缺少变量 {name}")
        return replacement

    return ENV_REFERENCE.sub(replace, value)


def _is_http_url(value: str) -> bool:
    """只做最小格式检查；不在错误中回显可能含有 Key 的完整 URL。"""

    parsed = urlsplit(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _read_servers(path: Path) -> dict[str, Mapping[str, object]]:
    """读取一个配置文件，并在启动前给出容易理解的格式错误。"""

    try:
        data: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise MCPError(f"无法读取 MCP 配置 {path}：{error}") from error
    if not isinstance(data, dict) or not isinstance(data.get("servers"), dict):
        raise MCPError(f"MCP 配置 {path} 必须包含 servers 对象")

    result: dict[str, Mapping[str, object]] = {}
    for name, value in data["servers"].items():
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", name):
            raise MCPError(f"MCP Server 名称只能包含英文、数字、下划线和短横线：{name}")
        if not isinstance(value, dict):
            raise MCPError(f"MCP Server {name} 的配置必须是对象")
        result[name] = cast(Mapping[str, object], value)
    return result
