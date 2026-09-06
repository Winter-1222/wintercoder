"""独立记忆文件是数据源，MEMORY.md 是程序生成的索引副本。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from threading import RLock

from jixue.memory_format import (
    MAX_ENTRY_CHARACTERS,
    MAX_INDEX_CHARACTERS,
    MAX_MEMORIES,
    MemoryMetadata,
    format_index,
    format_memory,
    read_metadata,
    validate_content,
    validate_index,
    validate_metadata,
    validate_name,
)

# 取消 asyncio 等待不会终止写盘线程；同一进程的记忆操作仍需串行提交。
_MEMORY_LOCK = RLock()


class MemoryStore:
    def __init__(self, project_root: Path) -> None:
        self.root = project_root.resolve()

    def _path(self, filename: str) -> Path:
        directory = self.root / ".jixue" / "memory"
        target = directory / filename
        # 固定数据目录拒绝链接，防止记忆名映射到别的项目文件。
        for path in (self.root / ".jixue", directory, target):
            if path.is_symlink() or path.is_junction():
                raise ValueError("记忆目录及文件不能使用符号链接或目录联接")
        if not target.resolve().is_relative_to(self.root):
            raise ValueError("记忆路径越过项目目录")
        return target

    def _check_legacy_index(self) -> None:
        path = self._path("MEMORY.md")
        if path.exists():
            with path.open(encoding="utf-8-sig") as stream:
                if stream.readline(128).strip() == "# 项目记忆":
                    raise ValueError(
                        "检测到旧版 MEMORY.md，未覆盖原文件；"
                        "请先备份并按四种类型整理成独立记忆文件"
                    )

    def _metadata(self, excluding: str | None = None) -> list[MemoryMetadata]:
        entries = []
        for path in sorted(self._path("MEMORY.md").parent.glob("*.md")):
            if path.name == "MEMORY.md" or path.stem == excluding:
                continue
            validate_name(path.stem)
            with self._path(path.name).open(encoding="utf-8-sig") as stream:
                entries.append(read_metadata(stream, path.stem))
            if len(entries) > MAX_MEMORIES:
                raise ValueError("项目记忆超过 100 条，请先整理目录")
        return entries

    def index(self) -> str:
        """任务开始只读磁盘索引；缺失或损坏时不隐式扫描、修复。"""
        with _MEMORY_LOCK:
            path = self._path("MEMORY.md")
            if not path.parent.exists():
                return format_index([])
            if not path.exists():
                raise ValueError("MEMORY.md 缺失，请使用 rebuild_index 重建索引")
            self._check_legacy_index()
            with path.open(encoding="utf-8-sig") as stream:
                index = stream.read(MAX_INDEX_CHARACTERS + 1)
            validate_index(index)
            return index

    def rebuild_index(self) -> str:
        """手工编辑后显式扫描文件头，重新生成派生索引。"""
        with _MEMORY_LOCK:
            self._check_legacy_index()
            index = format_index(self._metadata())
            self._write(self._path("MEMORY.md"), index)
            return "已从独立记忆文件的 YAML 头重建 MEMORY.md。"

    def read(self, name: str) -> str:
        """模型选定名称后才读取该条正文；文件头和正文分别限制大小。"""
        validate_name(name)
        with _MEMORY_LOCK:
            with self._path(f"{name}.md").open(encoding="utf-8-sig") as stream:
                metadata = read_metadata(stream, name)
                raw_content = stream.read(MAX_ENTRY_CHARACTERS + 3)
            if len(raw_content) > MAX_ENTRY_CHARACTERS + 2:
                raise ValueError("记忆文件正文超过 4000 字符，请先整理")
            content = raw_content.strip()
            validate_content(content)
            return format_memory(metadata, content)

    def remember(self, name: str, description: str, memory_type: str, content: str) -> str:
        validate_metadata(name, description, memory_type)
        validate_content(content)
        metadata = MemoryMetadata(
            name, description.strip(), memory_type, datetime.now(UTC).isoformat()
        )
        with _MEMORY_LOCK:
            self._check_legacy_index()
            entries = self._metadata(excluding=name) + [metadata]
            index = format_index(entries)
            path = self._path(f"{name}.md")
            if path.exists():
                # 不能借同名更新悄悄覆盖损坏或不属于本格式的文件。
                with path.open(encoding="utf-8-sig") as stream:
                    read_metadata(stream, name)
            self._invalidate_index()
            self._write(path, format_memory(metadata, content))
            return self._save_index(index, f"已记住 {name}（{memory_type}）：{description.strip()}")

    def forget(self, name: str) -> str:
        validate_name(name)
        with _MEMORY_LOCK:
            self._check_legacy_index()
            path = self._path(f"{name}.md")
            index = format_index(self._metadata(excluding=name))
            existed = path.exists()
            self._invalidate_index()
            path.unlink(missing_ok=True)
            message = f"已忘记 {name}。" if existed else f"没有名为 {name} 的记忆。"
            return self._save_index(index, message)

    def _invalidate_index(self) -> None:
        # 正文和索引无法一起原子替换：先撤下旧索引，防止中断后仍加载过期内容。
        path = self._path("MEMORY.md")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.unlink(missing_ok=True)

    def _save_index(self, index: str, message: str) -> str:
        try:
            self._write(self._path("MEMORY.md"), index)
        except OSError as error:
            raise ValueError(
                message + " 正文变更已完成，但索引写入失败；请使用 rebuild_index 重建索引。"
            ) from error
        return message

    def _write(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path(path.name + ".tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)


def read_memory_context(project_root: Path) -> str:
    try:
        return MemoryStore(project_root).index()
    except (OSError, ValueError) as error:
        return f"项目记忆索引暂不可用：{error}"
