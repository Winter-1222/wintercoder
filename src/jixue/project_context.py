"""从项目根目录读取有界的指令；每个用户任务刷新一次。"""

from pathlib import Path

MAX_INSTRUCTIONS_CHARACTERS = 20_000


def read_project_instructions(project_root: Path) -> str:
    root = project_root.resolve()
    path = (root / "AGENTS.md").resolve()
    if not path.is_relative_to(root):
        return "AGENTS.md 指向项目外部，未加载。"
    try:
        with path.open(encoding="utf-8-sig") as stream:
            content = stream.read(MAX_INSTRUCTIONS_CHARACTERS + 1)
    except FileNotFoundError:
        return ""
    except (OSError, UnicodeError) as error:
        return f"AGENTS.md 无法读取：{type(error).__name__}"
    if len(content) > MAX_INSTRUCTIONS_CHARACTERS:
        content = content[:MAX_INSTRUCTIONS_CHARACTERS] + "\n（项目指令超出 20000 字符，已截断）"
    return content.strip()
