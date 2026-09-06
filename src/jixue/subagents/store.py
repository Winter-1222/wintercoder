"""每个子任务一个原子替换的 JSON 快照，运行数据不进入 Git。"""

import json
import re
from pathlib import Path
from threading import RLock
from typing import Any


class SubagentStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self._lock = RLock()

    def path(self, session_id: str, agent_id: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{32}", session_id):
            raise ValueError("子任务会话编号无效")
        if not re.fullmatch(r"sub_[0-9a-f]{32}", agent_id):
            raise ValueError("子任务编号无效")
        path = self.root / ".jixue" / "subagents" / session_id / f"{agent_id}.json"
        for item in (path, *path.parents):
            if item == self.root:
                break
            if item.is_symlink() or item.is_junction():
                raise ValueError("子任务存储路径不能使用链接")
        if not path.resolve().is_relative_to(self.root):
            raise ValueError("子任务路径越界")
        return path

    def save(self, data: dict[str, Any]) -> None:
        with self._lock:
            self._save(data)

    def _save(self, data: dict[str, Any]) -> None:
        path = self.path(data["session_id"], data["agent_id"])
        temporary = path.with_suffix(".tmp")
        if temporary.is_symlink():
            raise ValueError("子任务临时文件不能是链接")
        content = json.dumps(data, ensure_ascii=False)
        if len(content.encode("utf-8")) > 16 * 1024 * 1024:
            raise ValueError("子任务快照超过 16 MiB")
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)

    def load_session(self, session_id: str) -> list[dict[str, Any]]:
        directory = self.path(session_id, "sub_" + "0" * 32).parent
        items = []
        for path in sorted(directory.glob("sub_*.json")):
            checked = self.path(session_id, path.stem)
            if checked.stat().st_size > 16 * 1024 * 1024:
                raise ValueError("子任务存档过大")
            data = json.loads(checked.read_text(encoding="utf-8"))
            if (
                not isinstance(data, dict)
                or data.get("session_id") != session_id
                or data.get("agent_id") != path.stem
            ):
                raise ValueError("子任务存档身份不匹配")
            _validate_snapshot(data)
            items.append(data)
        return items


def _validate_snapshot(data: dict[str, Any]) -> None:
    """磁盘文件是外部输入，恢复前先验证执行上限和集合形状。"""
    if not isinstance(data.get("conversation"), dict):
        raise ValueError("子任务存档缺少工作对话")
    texts = ("role", "system", "model_id", "model", "prompt", "parent_tool_use_id",
             "request_id", "run_id", "report")
    if any(not isinstance(data.get(key), str) for key in texts):
        raise ValueError("子任务存档文字字段无效")
    choices = {
        "status": {"running", "completed", "cancelled", "failed", "incomplete",
                   "interrupted", "timed_out"},
        "kind": {"defined", "fork"}, "mode": {"plan", "do"},
        "permission_mode": {"ask_all", "confirm_edits", "auto_allow"},
    }
    for key, allowed in choices.items():
        if not isinstance(data.get(key), str) or data[key] not in allowed:
            raise ValueError(f"子任务存档 {key} 无效")
    if type(data.get("max_turns")) is not int or not 1 <= data["max_turns"] <= 16:
        raise ValueError("子任务存档轮数无效")
    for key in ("background", "notification_pending"):
        if type(data.get(key)) is not bool:
            raise ValueError("子任务存档布尔字段无效")
    usage = data.get("usage")
    if not isinstance(usage, dict) or any(type(usage.get(key)) is not int or usage[key] < 0
                                        for key in ("input_tokens", "output_tokens")):
        raise ValueError("子任务存档用量无效")
    if type(data.get("duration_ms")) is not int or data["duration_ms"] < 0:
        raise ValueError("子任务存档耗时无效")
    tools, trace = data.get("tools"), data.get("trace")
    if not isinstance(tools, list) or not all(isinstance(t, dict)
        and isinstance(t.get("name"), str) and isinstance(t.get("description"), str)
        and isinstance(t.get("input_schema"), dict) for t in tools):
        raise ValueError("子任务存档工具定义无效")
    if not isinstance(trace, list) or len(trace) > 80:
        raise ValueError("子任务存档工具轨迹无效")
    for row in trace:
        if not isinstance(row, dict) or not isinstance(row.get("input"), dict) or any(
            not isinstance(row.get(key), str)
            for key in ("id", "name", "content", "status", "permission_token")
        ):
            raise ValueError("子任务存档工具轨迹字段无效")
