"""有界、可手工阅读的项目 Markdown 记忆；不同会话共享同一份文件。"""

from __future__ import annotations

import re
from pathlib import Path

MAX_MEMORY_CHARACTERS = 16_000
MAX_ENTRY_CHARACTERS = 1_000
HEADER = "# 项目记忆\n\n"
KEY_PATTERN = r"[a-zA-Z0-9_-]{1,64}"


class MemoryStore:
    def __init__(self, project_root: Path) -> None:
        self.root = project_root.resolve()

    def _path(self, name: str = "MEMORY.md") -> Path:
        path = (self.root / ".jixue" / "memory" / name).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("记忆路径越过项目目录")
        return path

    def read(self) -> dict[str, str]:
        try:
            with self._path().open(encoding="utf-8-sig") as stream:
                content = stream.read(MAX_MEMORY_CHARACTERS + 1)
        except FileNotFoundError:
            return {}
        if len(content) > MAX_MEMORY_CHARACTERS:
            raise ValueError("项目记忆超过 16000 字符，请整理后重试")
        if not content.startswith(HEADER):
            raise ValueError("记忆文件应以 '# 项目记忆' 和空行开头；未覆盖原文件")
        entries = {}
        for line in content[len(HEADER) :].splitlines():
            if not line.strip():
                continue
            match = re.fullmatch(rf"- \*\*({KEY_PATTERN})\*\*: (.+)", line)
            if not match or len(match[2]) > MAX_ENTRY_CHARACTERS or match[1] in entries:
                raise ValueError("记忆条目格式无效，应为 '- **key**: 内容' 且键不能重复")
            entries[match[1]] = match[2]
        return entries

    def text(self) -> str:
        entries = self.read()
        return _format(entries) if entries else "（尚无项目记忆）"

    def update(self, action: str, key: str, content: str = "") -> str:
        if not re.fullmatch(KEY_PATTERN, key):
            raise ValueError("记忆键只允许 1—64 位英文字母、数字、下划线或短横线")
        entries = self.read()
        if action == "remember":
            content = " ".join(content.split())
            if not content or len(content) > MAX_ENTRY_CHARACTERS:
                raise ValueError("每条记忆需要 1—1000 个字符")
            entries[key] = content
        elif action == "forget":
            if key not in entries:
                return f"没有名为 {key} 的记忆，无需删除。"
            del entries[key]
        else:
            raise ValueError("记忆操作只能是 remember 或 forget")
        text = _format(entries)
        if len(text) > MAX_MEMORY_CHARACTERS:
            raise ValueError("记忆已满，请先合并或忘记旧条目")
        path = self._path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path("MEMORY.tmp")
        temporary.write_text(text, encoding="utf-8")
        temporary.replace(path)
        return f"已记住 {key}：{entries[key]}" if action == "remember" else f"已忘记 {key}。"


def _format(entries: dict[str, str]) -> str:
    return HEADER + "".join(f"- **{key}**: {value}\n" for key, value in entries.items())


def read_memory_context(project_root: Path) -> str:
    try:
        return MemoryStore(project_root).text()
    except (OSError, ValueError, UnicodeError) as error:
        return f"项目记忆暂不可用：{error}"
