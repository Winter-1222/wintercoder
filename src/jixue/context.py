"""第七章的上下文入口：保存过大的工具结果，并只让模型看到短预览。"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jixue.tools.base import ToolResult

# 这个阈值只防止“单次工具结果”突然撑满上下文。
# 整段对话何时压缩会在本章后续按 Token 预算判断，两者不是同一件事。
MAX_INLINE_RESULT_CHARACTERS = 50_000
RESULT_PREVIEW_CHARACTERS = 8_000
DEFAULT_ARTIFACT_READ_CHARACTERS = 8_000
MAX_ARTIFACT_READ_CHARACTERS = 20_000
MAX_ARTIFACT_SEARCH_MATCHES = 100
ARTIFACT_DIRECTORY = Path(".jixue") / "tool-results"
_SAFE_ARTIFACT_ID = re.compile(r"[A-Za-z0-9_-]+")


@dataclass(slots=True)
class ToolResultStore:
    """保存工具原始结果，并生成真正写入模型历史的有界版本。"""

    project_root: Path

    def __post_init__(self) -> None:
        self.project_root = self.project_root.resolve()

    async def prepare(
        self,
        tool_use_id: str,
        tool_name: str,
        result: ToolResult,
    ) -> ToolResult:
        """小结果原样返回；大结果落盘后返回头尾预览。"""

        if len(result.content) <= MAX_INLINE_RESULT_CHARACTERS:
            return result

        # 延迟导入避免 context 模块和 tools 包在程序启动时互相等待。
        from jixue.tools.base import ToolResult

        artifact_id = _make_artifact_id(tool_use_id)
        metadata = {
            **dict(result.metadata),
            "artifact_id": artifact_id,
            "original_characters": len(result.content),
            "truncated": True,
        }
        try:
            target = self._path_for(artifact_id)
            metadata["artifact_path"] = str(target)
            # 大结果写盘会阻塞一小段时间，放到线程里避免卡住 Agent 事件循环。
            await asyncio.to_thread(_write_once, target, result.content)
        except (OSError, ValueError) as error:
            # 若落盘失败，既不能把几万字符重新塞给模型，也不应让整个 Loop 崩溃。
            return ToolResult(
                f"{tool_name} 的结果过长，但保存完整结果失败：{error}",
                is_error=True,
                metadata=metadata,
            )

        preview = _head_and_tail(result.content)
        visible = (
            f"{tool_name} 返回了 {len(result.content)} 个字符，完整结果已保存。\n"
            f"artifact_id：{artifact_id}\n"
            "需要更多内容时，请使用 read_artifact 按关键字搜索，或按 offset/limit 分段读取。\n\n"
            f"--- 头部预览 ---\n{preview[0]}\n\n"
            f"--- 尾部预览 ---\n{preview[1]}"
        )
        return ToolResult(visible, is_error=result.is_error, metadata=metadata)

    async def read_text(self, artifact_id: str) -> tuple[str, Path]:
        """读取指定 artifact；ID 只能对应固定结果目录中的直接子文件。"""

        target = self._path_for(artifact_id)
        if not target.is_file():
            raise FileNotFoundError(f"artifact 不存在：{artifact_id}")
        content = await asyncio.to_thread(_read_exact, target)
        return content, target

    def _path_for(self, artifact_id: str) -> Path:
        if not _SAFE_ARTIFACT_ID.fullmatch(artifact_id):
            raise ValueError("artifact_id 格式不正确")
        storage_root = self.project_root / ARTIFACT_DIRECTORY
        resolved_root = storage_root.resolve()
        target = storage_root / f"{artifact_id}.txt"
        resolved_target = target.resolve()
        try:
            # resolve 后再校验，可以阻止符号链接把读取引到项目目录之外。
            resolved_root.relative_to(self.project_root)
            resolved_target.relative_to(resolved_root)
        except ValueError as error:
            raise ValueError("artifact 路径超出项目目录") from error
        return target


def _make_artifact_id(tool_use_id: str) -> str:
    """把模型给出的调用 ID 变成安全且稳定的文件名，避免路径穿越和碰撞。"""

    readable = re.sub(r"[^A-Za-z0-9_-]", "_", tool_use_id).strip("_")[:48] or "tool"
    digest = sha256(tool_use_id.encode("utf-8")).hexdigest()[:12]
    return f"{readable}_{digest}"


def _write_once(target: Path, content: str) -> None:
    """相同工具调用只写一次；文件已经存在时不覆盖原始证据。"""

    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with target.open("x", encoding="utf-8", newline="") as stream:
            stream.write(content)
    except FileExistsError:
        if target.is_symlink() or not target.is_file():
            raise OSError("artifact 目标不是普通文件") from None


def _read_exact(target: Path) -> str:
    """禁用通用换行转换，保证落盘前后的字符数和 offset 完全一致。"""

    with target.open("r", encoding="utf-8", newline="") as stream:
        return stream.read()


def _head_and_tail(content: str) -> tuple[str, str]:
    """头部帮助识别数据，尾部通常保留命令报错和总结。"""

    half = RESULT_PREVIEW_CHARACTERS // 2
    return content[:half], content[-half:]
