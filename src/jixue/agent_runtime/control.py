"""任务生命周期、停止信号和一次性权限回复；不执行模型或工具。"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Any

from jixue.agent_runtime.events import AgentMode
from jixue.permission import PermissionMode


class RunControl:
    """所有组件共享同一个取消信号，权限等待也能立即被停止唤醒。"""

    def __init__(self) -> None:
        self.cancel_event = asyncio.Event()
        self.is_running = False
        self._accepts_cancel = False
        self.mode = AgentMode.DO
        self.permission_mode = PermissionMode.CONFIRM_EDITS
        self._permission_tool_use_id: str | None = None
        self._permission_future: asyncio.Future[bool] | None = None

    def begin(self) -> None:
        """每个任务新建 Event，允许下一次任务运行在新的事件循环里。"""

        if self.is_running:
            raise RuntimeError("已有任务正在运行")
        self.cancel_event = asyncio.Event()
        self.is_running = True
        self._accepts_cancel = True

    def finish(self) -> None:
        self.clear_permission()
        self.is_running = False
        self._accepts_cancel = False

    def close_cancellation(self) -> None:
        """任务结束状态已确定时关闭停止入口，收尾事件发完前仍占用当前任务。"""

        self._accepts_cancel = False

    def set_mode(self, mode: AgentMode) -> None:
        """切换工作模式；任务运行中不允许改变本轮规则。"""

        if self.is_running:
            raise RuntimeError("任务运行中不能切换模式")
        self.mode = mode

    def set_permission_mode(self, mode: PermissionMode) -> None:
        """切换权限策略；任务运行中继续沿用本轮开始时的策略。"""

        if self.is_running:
            raise RuntimeError("任务运行中不能切换权限模式")
        self.permission_mode = mode

    def cancel(self) -> bool:
        """请求停止当前任务；返回 False 表示此刻没有正在运行的任务。"""

        if not self.is_running or not self._accepts_cancel:
            return False
        self.cancel_event.set()
        # 如果 Agent 正停在权限确认处，只设置取消标记还不够：还要唤醒等待中的 Future。
        if self._permission_future is not None and not self._permission_future.done():
            self._permission_future.set_result(False)
        return True

    def respond_permission(self, tool_use_id: str, allow: bool) -> bool:
        """接收 UI 的权限决定；ID 不匹配表示这个确认已经过期。"""

        future = self._permission_future
        if future is None or future.done() or tool_use_id != self._permission_tool_use_id:
            return False
        future.set_result(allow)
        return True

    def begin_permission(self, tool_use_id: str) -> asyncio.Future[bool]:
        """先建立等待对象再发事件，避免 UI 很快回复时丢失决定。"""

        if self._permission_future is not None:
            raise RuntimeError("已有工具正在等待权限确认")
        self._permission_tool_use_id = tool_use_id
        self._permission_future = asyncio.get_running_loop().create_future()
        return self._permission_future

    def clear_permission(self) -> None:
        """清掉一次性确认状态，旧按钮再次点击时就会被拒绝。"""

        self._permission_tool_use_id = None
        self._permission_future = None


def cancel_in_background(task: asyncio.Task[Any]) -> None:
    """请求取消但不阻塞 Agent；任务稍后结束时取走异常，避免控制台警告。"""

    task.cancel()

    def consume_result(done: asyncio.Task[Any]) -> None:
        # 这里处理的是已被我们主动取消的后台任务，异常不能再影响新一轮聊天。
        with suppress(BaseException):
            done.result()

    task.add_done_callback(consume_result)
