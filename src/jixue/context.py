"""第七章上下文入口：保存大结果，并为每轮模型请求生成精简视图。"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING

from jixue.domain.conversation import (
    APIMessage,
    APITextBlock,
    APIToolResultBlock,
    APIToolUseBlock,
)

if TYPE_CHECKING:
    from jixue.tools.base import ToolResult

# 这个阈值只防止“单次工具结果”突然撑满上下文。
# 整段对话何时压缩会在本章后续按 Token 预算判断，两者不是同一件事。
MAX_INLINE_RESULT_CHARACTERS = 50_000
RESULT_PREVIEW_CHARACTERS = 8_000
DEFAULT_ARTIFACT_READ_CHARACTERS = 8_000
MAX_ARTIFACT_READ_CHARACTERS = 20_000
MAX_ARTIFACT_SEARCH_MATCHES = 100
# 工具结果总量超过触发线时，一次清到目标线附近，避免每轮只删一点、反复破坏缓存前缀。
ACTIVE_RESULT_TRIGGER_CHARACTERS = 200_000
ACTIVE_RESULT_TARGET_CHARACTERS = 120_000
KEEP_RECENT_TOOL_ROUNDS = 3
KEEP_RECENT_CONVERSATION_TURNS = 2
# 当前模型配置还没有声明精确上下文窗口，先用可测试的字符预算保护请求。
# 阈值留出模型输出和协议结构空间；后续可随模型配置一起改成更精确的 Token 预算。
AUTO_COMPACTION_TRIGGER_CHARACTERS = 160_000
ARTIFACT_DIRECTORY = Path(".jixue") / "tool-results"
_SAFE_ARTIFACT_ID = re.compile(r"[A-Za-z0-9_-]+")
_ARTIFACT_ID_IN_RESULT = re.compile(r"artifact_id[：:]\s*([A-Za-z0-9_-]+)")
_SUMMARY_BLOCK = re.compile(r"<summary>\s*(.*?)\s*</summary>", re.DOTALL)
_REQUIRED_SUMMARY_SECTIONS = (
    "主要请求和意图",
    "关键技术概念",
    "文件和代码段",
    "错误和修复",
    "问题解决过程",
    "所有用户消息",
    "待办任务",
    "当前工作",
    "可能的下一步",
)
MIN_COMPACTION_SUMMARY_CHARACTERS = 120

# 摘要是一次独立的模型任务，不沿用 Coding Agent 的角色，也完全不提供工具。
COMPACTION_SYSTEM_PROMPT = """你是霁雪的上下文摘要器。
你的唯一任务是忠实压缩给定对话，不能调用工具，不能继续执行原任务。
先在 <analysis> 中整理草稿，再在 <summary> 中给出最终摘要。
最终摘要必须比原对话短，并保留继续工作所需的事实；不要输出其他 XML 标签。
"""

COMPACTION_REQUEST = """<jixue-compaction-request>
禁止调用任何工具。只总结上面的较早对话，不要回答其中的问题或继续执行任务。
请先输出 <analysis> 草稿，再输出 <summary> 正文。正文必须简洁且包含以下九部分：
1. 主要请求和意图
2. 关键技术概念
3. 文件和代码段
4. 错误和修复
5. 问题解决过程
6. 所有用户消息（尽量保留原文）
7. 待办任务
8. 当前工作（最详细）
9. 可能的下一步
</jixue-compaction-request>"""


def with_compaction_request(history: Sequence[APIMessage]) -> list[APIMessage]:
    """在待摘要历史末尾追加指令，并维持 API 要求的角色交替。"""

    result = list(history)
    if result and result[-1].role == "user" and isinstance(result[-1].content, str):
        result[-1] = APIMessage(
            "user",
            f"{result[-1].content}\n\n{COMPACTION_REQUEST}",
        )
    else:
        result.append(APIMessage("user", COMPACTION_REQUEST))
    return result


def extract_compaction_summary(response: str) -> str:
    """只接受完整且包含九部分的 summary，防止一句“无”覆盖旧历史。"""

    match = _SUMMARY_BLOCK.search(response)
    summary = match.group(1).strip() if match else ""
    if not summary:
        raise ValueError("模型没有返回完整的 <summary> 摘要")
    missing = [title for title in _REQUIRED_SUMMARY_SECTIONS if title not in summary]
    if missing:
        raise ValueError(f"压缩摘要缺少必要章节：{'、'.join(missing)}")
    if len(summary) < MIN_COMPACTION_SUMMARY_CHARACTERS:
        raise ValueError("压缩摘要过短，无法安全替代旧历史")
    return summary


def api_text_characters(history: Sequence[APIMessage]) -> int:
    """估算摘要前的文本长度，用来拒绝越压越长的无效摘要。"""

    total = 0
    for message in history:
        if isinstance(message.content, str):
            total += len(message.content)
            continue
        for block in message.content:
            if isinstance(block, APITextBlock):
                total += len(block.text)
            elif isinstance(block, APIToolResultBlock):
                total += len(block.content)
    return total


def api_history_characters(history: Sequence[APIMessage]) -> int:
    """估算即将发送的活动历史大小，包含文本、工具参数和工具结果。"""

    total = 0
    for message in history:
        total += len(message.role)
        if isinstance(message.content, str):
            total += len(message.content)
            continue
        for block in message.content:
            if isinstance(block, APITextBlock):
                total += len(block.text)
            elif isinstance(block, APIToolUseBlock):
                total += len(block.id) + len(block.name) + len(str(block.input))
            elif isinstance(block, APIToolResultBlock):
                total += len(block.tool_use_id) + len(block.content)
    return total


def needs_auto_compaction(
    active_history: Sequence[APIMessage],
    trigger_characters: int = AUTO_COMPACTION_TRIGGER_CHARACTERS,
) -> bool:
    """只根据本次即将发送的活动历史判断是否需要自动压缩。"""

    if trigger_characters < 1:
        raise ValueError("自动压缩触发线必须大于 0")
    return api_history_characters(active_history) > trigger_characters


@dataclass(slots=True)
class ActiveContext:
    """保留完整历史，只为下一次模型请求生成较小的临时视图。"""

    trigger_characters: int = ACTIVE_RESULT_TRIGGER_CHARACTERS
    target_characters: int = ACTIVE_RESULT_TARGET_CHARACTERS
    keep_recent_tool_rounds: int = KEEP_RECENT_TOOL_ROUNDS
    _cleared_tool_use_ids: set[str] = field(default_factory=set, init=False)

    def __post_init__(self) -> None:
        if self.keep_recent_tool_rounds < 1:
            raise ValueError("至少保留 1 个最近工具轮")
        if not 0 <= self.target_characters < self.trigger_characters:
            raise ValueError("活动上下文目标线必须大于等于 0 且小于触发线")

    def build(self, full_history: Sequence[APIMessage]) -> list[APIMessage]:
        """达到触发线后，成轮清理最旧结果；原消息和块永远不被修改。"""

        # 标准工具 ID 应全程唯一。发现复用时整份原样返回，避免清掉尚未看过的新结果。
        if _has_duplicate_tool_ids(full_history):
            return list(full_history)
        rounds = _completed_tool_rounds(full_history)
        active_characters = _active_result_characters(
            rounds,
            self._cleared_tool_use_ids,
        )
        if active_characters > self.trigger_characters:
            # 最近几轮很可能仍与当前决策直接相关，尤其最新结果还没被模型看过。
            older_rounds = rounds[: max(0, len(rounds) - self.keep_recent_tool_rounds)]
            for blocks in older_rounds:
                uncleared = [
                    block
                    for block in blocks
                    if block.tool_use_id not in self._cleared_tool_use_ids
                ]
                reduction = sum(
                    len(block.content) - len(_cleared_result_content(block))
                    for block in uncleared
                )
                if reduction <= 0:
                    continue
                # 同一轮的并行结果一起进入清理集合，消息结构和 ID 顺序完全保留。
                self._cleared_tool_use_ids.update(
                    block.tool_use_id for block in uncleared
                )
                active_characters -= reduction
                if active_characters <= self.target_characters:
                    break

        return _replace_cleared_results(full_history, self._cleared_tool_use_ids)


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


def _completed_tool_rounds(
    history: Sequence[APIMessage],
) -> list[tuple[APIToolResultBlock, ...]]:
    """只接受 ID 完整配对的相邻 tool_use/tool_result，异常结构宁可不清理。"""

    rounds: list[tuple[APIToolResultBlock, ...]] = []
    for index in range(1, len(history)):
        assistant = history[index - 1]
        result = history[index]
        if assistant.role != "assistant" or result.role != "user":
            continue
        if not isinstance(assistant.content, tuple) or not isinstance(result.content, tuple):
            continue
        if any(
            not isinstance(block, (APITextBlock, APIToolUseBlock))
            for block in assistant.content
        ):
            continue
        uses = tuple(
            block for block in assistant.content if isinstance(block, APIToolUseBlock)
        )
        results = tuple(
            block for block in result.content if isinstance(block, APIToolResultBlock)
        )
        if (
            uses
            and len(results) == len(result.content)
            and [block.id for block in uses]
            == [block.tool_use_id for block in results]
        ):
            rounds.append(results)
    return rounds


def _has_duplicate_tool_ids(history: Sequence[APIMessage]) -> bool:
    """ID 一旦复用，全局字符串替换就不再安全，因此本轮放弃清理。"""

    use_ids: list[str] = []
    result_ids: list[str] = []
    for message in history:
        if not isinstance(message.content, tuple):
            continue
        for block in message.content:
            if isinstance(block, APIToolUseBlock):
                use_ids.append(block.id)
            elif isinstance(block, APIToolResultBlock):
                result_ids.append(block.tool_use_id)
    return len(use_ids) != len(set(use_ids)) or len(result_ids) != len(set(result_ids))


def _active_result_characters(
    rounds: Sequence[Sequence[APIToolResultBlock]],
    cleared_ids: set[str],
) -> int:
    """统计当前模型视图中的工具结果字符数，不把完整历史误当成活动大小。"""

    return sum(
        len(_cleared_result_content(block))
        if block.tool_use_id in cleared_ids
        else len(block.content)
        for blocks in rounds
        for block in blocks
    )


def _replace_cleared_results(
    history: Sequence[APIMessage],
    cleared_ids: set[str],
) -> list[APIMessage]:
    """复制消息列表，仅替换选中 result 的 content，绝不删除任何块。"""

    active: list[APIMessage] = []
    for message in history:
        if not isinstance(message.content, tuple):
            active.append(message)
            continue
        blocks = tuple(
            APIToolResultBlock(
                block.tool_use_id,
                _cleared_result_content(block),
                block.is_error,
            )
            if isinstance(block, APIToolResultBlock)
            and block.tool_use_id in cleared_ids
            else block
            for block in message.content
        )
        active.append(APIMessage(message.role, blocks))
    return active


def _cleared_result_content(block: APIToolResultBlock) -> str:
    """大结果若已有 artifact，保留其编号；否则提示模型按需重新调用工具。"""

    match = _ARTIFACT_ID_IN_RESULT.search(block.content)
    if match:
        return (
            "[较早的工具结果已从活动上下文移除。"
            f"完整结果仍可用 read_artifact 读取，artifact_id：{match.group(1)}]"
        )
    return "[较早的工具结果已从活动上下文移除；如仍需细节，请重新调用对应工具。]"
