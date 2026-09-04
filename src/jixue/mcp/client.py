"""MCP transport 合同、stdio 客户端和本地配置读取。"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, cast

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class MCPError(RuntimeError):
    """MCP 配置、连接或调用失败；错误消息可以显示给用户。"""


@dataclass(frozen=True, slots=True)
class StdioServerConfig:
    """启动一个本地 MCP 子进程所需的最小配置。"""

    name: str
    command: str
    args: tuple[str, ...] = ()


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
    """所有 transport 都遵守这个小接口；后续 HTTP 不会改工具包装器。"""

    @property
    def server_name(self) -> str: ...

    async def connect(self) -> tuple[MCPToolDefinition, ...]: ...

    async def call_tool(self, name: str, tool_input: Mapping[str, object]) -> MCPCallResult: ...

    async def close(self) -> None: ...


class StdioMCPClient:
    """用官方 MCP SDK 管理一个 stdio Server 的完整生命周期。"""

    def __init__(self, config: StdioServerConfig, project_root: Path) -> None:
        self._config = config
        self._project_root = project_root.resolve()
        self._stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None

    @property
    def server_name(self) -> str:
        return self._config.name

    async def connect(self) -> tuple[MCPToolDefinition, ...]:
        """启动子进程、完成握手，再读取全部分页工具。"""

        if self._session is not None:
            raise MCPError(f"MCP Server 已连接：{self.server_name}")

        stack = AsyncExitStack()
        try:
            # SDK 负责 NDJSON 收发和子进程退出；霁雪只关心“连接”这个领域动作。
            read_stream, write_stream = await stack.enter_async_context(
                stdio_client(
                    StdioServerParameters(
                        command=self._config.command,
                        args=list(self._config.args),
                        cwd=self._project_root,
                    )
                )
            )
            session = await stack.enter_async_context(ClientSession(read_stream, write_stream))
            # 官方 initialize() 内部还会自动发送 notifications/initialized。
            await session.initialize()
            self._stack = stack
            self._session = session
            return await self._list_tools()
        except BaseException as error:
            await stack.aclose()
            self._stack = None
            self._session = None
            if isinstance(error, (MemoryError, SystemExit, KeyboardInterrupt)):
                raise
            if isinstance(error, Exception):
                raise MCPError(f"MCP Server {self.server_name} 连接失败：{error}") from error
            raise

    async def _list_tools(self) -> tuple[MCPToolDefinition, ...]:
        """跟随 nextCursor，避免只拿到 MCP Server 的第一页工具。"""

        session = self._require_session()
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
                        description=tool.description or f"调用 {self.server_name} 的 {tool.name}",
                        input_schema=cast(Mapping[str, object], tool.inputSchema),
                        read_only=read_only,
                        # Server 没声明时保守处理：未知工具不默认当成安全工具。
                        destructive=(
                            not read_only if destructive_hint is None else destructive_hint
                        ),
                    )
                )
            cursor = result.nextCursor
            if not cursor:
                return tuple(definitions)

    async def call_tool(
        self,
        name: str,
        tool_input: Mapping[str, object],
    ) -> MCPCallResult:
        """调用远端工具，并把 SDK 内容块翻译成霁雪自己的结果。"""

        result = await self._require_session().call_tool(name, dict(tool_input))
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

        # 有些 Server 只返回 structuredContent；转成 JSON 后模型仍能读懂。
        if not parts and result.structuredContent is not None:
            parts.append(json.dumps(result.structuredContent, ensure_ascii=False))
        return MCPCallResult(
            content="\n".join(parts) or "MCP 工具执行完成，但没有返回文本内容。",
            is_error=result.isError,
            metadata={"server": self.server_name, "remote_tool": name},
        )

    async def close(self) -> None:
        """关闭 session 和子进程；重复调用也安全。"""

        stack = self._stack
        self._stack = None
        self._session = None
        if stack is not None:
            await stack.aclose()

    def _require_session(self) -> ClientSession:
        if self._session is None:
            raise MCPError(f"MCP Server 尚未连接：{self.server_name}")
        return self._session


def load_stdio_server_configs(project_root: Path) -> tuple[StdioServerConfig, ...]:
    """读取公共配置，再用 Git 忽略的本地配置覆盖同名 Server。"""

    servers: dict[str, Mapping[str, object]] = {}
    for filename in ("mcp.json", "mcp.local.json"):
        path = project_root / "config" / filename
        if path.is_file():
            servers.update(_read_servers(path))

    configs: list[StdioServerConfig] = []
    for name in sorted(servers):
        raw = servers[name]
        if raw.get("enabled", True) is False:
            continue
        if raw.get("transport", "stdio") != "stdio":
            raise MCPError(f"MCP Server {name}：第一步只支持 stdio transport")
        command = raw.get("command")
        args = raw.get("args", [])
        if not isinstance(command, str) or not command.strip():
            raise MCPError(f"MCP Server {name}：command 必须是非空字符串")
        if not isinstance(args, list) or not all(isinstance(item, str) for item in args):
            raise MCPError(f"MCP Server {name}：args 必须是字符串数组")
        configs.append(StdioServerConfig(name, command, tuple(args)))
    return tuple(configs)


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
