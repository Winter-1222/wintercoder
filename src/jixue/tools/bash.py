"""在项目目录中执行当前操作系统的命令。"""

from __future__ import annotations

import asyncio
import os
import subprocess
from contextlib import suppress

from jixue.tools.base import BaseTool, ToolContext, ToolInput, ToolResult

COMMAND_TIMEOUT_SECONDS = 30
SENSITIVE_ENV_MARKERS = ("API_KEY", "TOKEN", "SECRET", "PASSWORD")


def create_bash_tool() -> BaseTool:
    """创建命令工具；Windows 使用 PowerShell，其他系统使用 Bash。"""

    def validate(tool_input: ToolInput) -> str | None:
        command = tool_input.get("command")
        if not isinstance(command, str) or not command.strip():
            return "command 必须是非空字符串"
        if len(command) > 20_000:
            return "command 不能超过 20000 个字符"
        return None

    async def run_command(context: ToolContext, tool_input: ToolInput) -> ToolResult:
        command = str(tool_input["command"]).strip()
        program = (
            ("powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command)
            if os.name == "nt"
            else ("bash", "-lc", command)
        )
        # 子进程不继承常见密钥变量，避免模型通过 env 一类命令直接读出凭据。
        environment = {
            name: value
            for name, value in os.environ.items()
            if not any(marker in name.upper() for marker in SENSITIVE_ENV_MARKERS)
        }
        process = await asyncio.create_subprocess_exec(
            *program,
            cwd=context.project_root.resolve(),
            env=environment,
            # Bridge 的 stdin 是桌面命令管道，子命令不能继承或争抢它。
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(),
                timeout=COMMAND_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            process.kill()
            await process.communicate()
            return ToolResult(
                f"命令超过 {COMMAND_TIMEOUT_SECONDS} 秒，已停止",
                is_error=True,
                metadata={"exit_code": None, "timed_out": True},
            )
        except asyncio.CancelledError:
            # 只结束 Agent 等待还不够；必须同时结束系统子进程，避免它在后台继续改文件。
            if process.returncode is None:
                # 进程可能刚好自行结束；这种竞态不应覆盖原本的取消信号。
                with suppress(ProcessLookupError):
                    process.kill()
                await process.communicate()
            raise

        stdout = stdout_bytes.decode("utf-8", errors="replace").strip()
        stderr = stderr_bytes.decode("utf-8", errors="replace").strip()
        content = _format_output(stdout, stderr)
        return ToolResult(
            content,
            is_error=process.returncode != 0,
            metadata={
                "exit_code": process.returncode,
                "characters": len(content),
                # 真正的截断和落盘由 Agent 的统一 ToolResultStore 处理。
                "truncated": False,
            },
        )

    return BaseTool(
        tool_name="bash",
        tool_description=(
            "在项目目录执行命令；Windows 使用 PowerShell，macOS/Linux 使用 Bash。"
            "涉及项目事实或修改时使用，不要用它代替已有的专用文件工具。"
        ),
        schema={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "要执行的一条 shell 命令"}
            },
            "required": ["command"],
            "additionalProperties": False,
        },
        handler=run_command,
        destructive=True,
        tool_category="shell",
        validator=validate,
    )


def _format_output(stdout: str, stderr: str) -> str:
    """合并标准输出和错误输出；这里必须保留原文，之后才能完整落盘。"""

    parts = []
    if stdout:
        parts.append(stdout)
    if stderr:
        parts.append(f"[stderr]\n{stderr}")
    return "\n\n".join(parts) or "命令执行完成，没有输出"
