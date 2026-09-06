"""运行一个子任务，消费子事件并保存可续接的工作快照。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import dataclass
from time import perf_counter
from typing import Any
from uuid import uuid4

from jixue.agent import Agent
from jixue.domain.conversation import ConversationManager, Usage
from jixue.sessions.codec import encode_conversation
from jixue.subagents.store import SubagentStore


@dataclass(slots=True)
class SubagentRun:
    data: dict[str, Any]
    conversation: ConversationManager
    agent: Agent | None = None
    task: asyncio.Task[None] | None = None
    permission: tuple[str, str] | None = None

    def checkpoint(self) -> dict[str, Any]:
        """检查点计入已知费用，但不提前修改运行中的账本，避免收尾重复计费。"""
        snapshot = encode_conversation(self.conversation)
        settled = self.conversation.total_usage
        known = self.data["usage"]
        usage = {
            "input_tokens": max(settled.input_tokens, known["input_tokens"]),
            "output_tokens": max(settled.output_tokens, known["output_tokens"]),
        }
        snapshot["usage"] = usage
        self.data.update(conversation=snapshot, usage=usage)
        return deepcopy(self.data)

    def public(self) -> dict[str, Any]:
        keys = (
            "agent_id",
            "session_id",
            "parent_tool_use_id",
            "request_id",
            "role",
            "kind",
            "status",
            "prompt",
            "background",
            "report",
            "model",
            "usage",
            "duration_ms",
            "run_id",
            "trace",
        )
        result = {key: self.data[key] for key in keys}
        result["report"] = self.data["report"][:12_000]
        result["trace"] = self.data["trace"][-20:]
        result = deepcopy(result)
        result["prompt"] = result["prompt"][:2000]
        for row in result["trace"]:
            if len(str(row["input"])) > 2000:
                row["input"] = {"preview": str(row["input"])[:2000] + "（展示已截断）"}
        return result


type RunEmitter = Callable[[SubagentRun], Awaitable[None]]


async def drive_subagent(
    run: SubagentRun,
    store: SubagentStore,
    emit: RunEmitter,
    timeout: float,
) -> None:
    agent = run.agent
    assert agent is not None
    started = perf_counter()
    text_parts: list[str] = []
    try:
        async with asyncio.timeout(timeout):
            async for item in agent.run(run.data["injected_prompt"]):
                payload, kind = dict(item.payload), item.type.value
                if kind == "stream_text":
                    text_parts.append(str(payload.get("text", "")))
                    continue
                if kind == "tool_use":
                    run.data["trace"].append(
                        {
                            "id": str(payload["id"]),
                            "name": str(payload["name"]),
                            "input": payload.get("input", {}),
                            "content": "执行中…",
                            "status": "running",
                            "permission_token": "",
                        }
                    )
                    run.data["trace"] = run.data["trace"][-80:]
                elif kind in {"tool_result", "permission_request"}:
                    row = next(
                        (row for row in reversed(run.data["trace"]) if row["id"] == payload["id"]),
                        None,
                    )
                    if row is not None and kind == "permission_request":
                        token = uuid4().hex
                        run.permission = (token, str(payload["id"]))
                        row.update(
                            status="permission",
                            permission_token=token,
                            content=str(payload["reason"]),
                        )
                    elif row is not None:
                        row.update(
                            status="failed" if payload.get("is_error") else "completed",
                            content=str(payload.get("content", ""))[:8_000],
                            permission_token="",
                        )
                        run.permission = None
                elif kind == "usage":
                    run.data["usage"] = payload["cumulative"]
                elif kind == "error":
                    run.data.update(
                        status="failed", report=str(payload.get("message", "子任务失败"))
                    )
                elif kind == "loop_complete":
                    status = (
                        "cancelled"
                        if payload.get("cancelled")
                        else "incomplete"
                        if payload.get("is_error")
                        else "completed"
                    )
                    report = "".join(text_parts)
                    if status == "completed" and (
                        not report.strip() or payload.get("stop_reason") != "end_turn"
                    ):
                        status = "incomplete"
                    run.data.update(status=status, report=report or "子任务未返回完整报告。")
                if kind == "turn_complete":
                    if payload.get("tool_calls"):
                        text_parts.clear()
                    # 此刻快照仅包含已经完整配对的工具轮；不保存悬空调用。
                    await asyncio.to_thread(store.save, run.checkpoint())
                if kind not in {"error", "loop_complete"}:
                    await emit(run)
    except TimeoutError:
        run.data.update(status="timed_out", report="子任务超过执行时限，保留已完成的工作快照。")
    except asyncio.CancelledError:
        run.data.update(status="cancelled", report="子任务已停止。")
    except Exception as error:
        run.data.update(status="failed", report=f"子任务运行失败：{type(error).__name__}：{error}")
    finally:
        agent.cancel()
        run.permission = None
        if run.data["status"] == "running":
            run.data.update(status="incomplete", report="子任务没有产生完整结束事件。")
        for row in run.data["trace"]:
            if row["status"] in {"running", "permission"}:
                row.update(
                    status="cancelled", content="任务已结束，操作未完成。", permission_token=""
                )
        run.data["duration_ms"] += round((perf_counter() - started) * 1000)
        # Loop 正常收尾已记账；超时等路径只补入已收到、尚未结算的费用。
        known = run.data["usage"]
        previous = run.conversation.total_usage
        run.conversation.record_usage(
            Usage(
                max(0, known["input_tokens"] - previous.input_tokens),
                max(0, known["output_tokens"] - previous.output_tokens),
            )
        )
        run.data["notification_pending"] = bool(run.data["background"])
        try:
            await asyncio.to_thread(store.save, run.checkpoint())
        except (OSError, ValueError) as error:
            run.data.update(
                status="failed", report=run.data["report"] + f"\n子任务存档失败：{error}"
            )
        await emit(run)
