"""记忆文件合同：有界 frontmatter、四种类型、正文与索引格式。"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import TextIO

import yaml

MEMORY_TYPES = ("user", "feedback", "project", "reference")
NAME_PATTERN = r"[a-z][a-z0-9_-]{0,63}"
MAX_DESCRIPTION_CHARACTERS = 160
MAX_ENTRY_CHARACTERS = 4_000
MAX_HEADER_CHARACTERS = 4_096
MAX_INDEX_CHARACTERS = 16_000
MAX_MEMORIES = 100
INDEX_HEADER = "# 记忆索引\n\n"
_RESERVED_NAMES = {"memory", "con", "prn", "aux", "nul"} | {
    f"{prefix}{number}" for prefix in ("com", "lpt") for number in range(1, 10)
}


@dataclass(frozen=True, slots=True)
class MemoryMetadata:
    name: str
    description: str
    type: str
    updated_at: str


def validate_name(name: str) -> None:
    if not re.fullmatch(NAME_PATTERN, name) or name in _RESERVED_NAMES:
        raise ValueError(
            "name 需以小写字母开头，只含小写字母、数字、下划线或短横线，"
            "最多 64 位，且不能使用 MEMORY 或系统保留名"
        )


def validate_metadata(name: str, description: str, memory_type: str) -> None:
    validate_name(name)
    if memory_type not in MEMORY_TYPES:
        raise ValueError("type 只允许 user、feedback、project、reference")
    if (
        not description.strip()
        or len(description) > MAX_DESCRIPTION_CHARACTERS
        or any(ord(char) < 32 for char in description)
    ):
        raise ValueError("description 需要 1—160 个字符的单行适用场景说明")


def validate_content(content: str) -> None:
    if not content.strip() or len(content) > MAX_ENTRY_CHARACTERS or "\x00" in content:
        raise ValueError("记忆正文需要 1—4000 个字符，不能含空字符")


def read_metadata(stream: TextIO, expected_name: str) -> MemoryMetadata:
    """读取到第二个 --- 为止；不扫描正文，也不假设头部一定少于 30 行。"""
    if stream.readline(5).strip() != "---":
        raise ValueError(f"{expected_name}.md 缺少 YAML frontmatter")
    lines: list[str] = []
    size = 0
    while True:
        line = stream.readline(MAX_HEADER_CHARACTERS - size + 1)
        size += len(line)
        if not line or size > MAX_HEADER_CHARACTERS:
            raise ValueError(f"{expected_name}.md 的 frontmatter 未闭合或超过大小限制")
        if line.strip() == "---":
            break
        lines.append(line)
    try:
        fields = yaml.safe_load("".join(lines))
    except yaml.YAMLError as error:
        raise ValueError(f"{expected_name}.md 的 YAML 无效") from error
    if not isinstance(fields, dict):
        raise ValueError("记忆 frontmatter 必须是字段对象")
    values = [fields.get(key) for key in ("name", "description", "type")]
    if not all(isinstance(value, str) for value in values):
        raise ValueError("记忆必须包含字符串 name、description、type")
    name, description, memory_type = (str(value) for value in values)
    if name != expected_name:
        raise ValueError("记忆 name 必须与文件名一致")
    validate_metadata(name, description, memory_type)
    updated = fields.get("updated_at")
    try:
        date = updated if isinstance(updated, datetime) else datetime.fromisoformat(str(updated))
        if date.tzinfo is None:
            raise ValueError("缺少时区")
    except ValueError as error:
        raise ValueError("记忆 updated_at 必须是含时区的 ISO 时间") from error
    return MemoryMetadata(name, description, memory_type, date.isoformat())


def format_memory(metadata: MemoryMetadata, content: str) -> str:
    header = yaml.safe_dump(asdict(metadata), allow_unicode=True, sort_keys=False).strip()
    return f"---\n{header}\n---\n\n{content.strip()}\n"


def format_index(entries: list[MemoryMetadata]) -> str:
    if len(entries) > MAX_MEMORIES:
        raise ValueError("项目记忆最多 100 条，请先合并或忘记旧条目")
    lines = [INDEX_HEADER]
    for item in sorted(entries, key=lambda entry: entry.name):
        # description 可以含 Markdown 符号，转义后不能伪造额外索引或链接。
        description = re.sub(r"([\\`*{}\[\]()<>#!|])", r"\\\1", item.description)
        lines.append(
            f"- [{item.name}]({item.name}.md) · {item.type}：{description}"
            f"（更新于 {item.updated_at}）\n"
        )
    if not entries:
        lines.append("（尚无项目记忆）\n")
    result = "".join(lines)
    if len(result) > MAX_INDEX_CHARACTERS:
        raise ValueError("记忆索引超过 16000 字符，请缩短描述或合并旧条目")
    return result
