"""只解析项目内 Python 源码，输出有界 JSON 概览，不导入被检查模块。"""

from __future__ import annotations

import argparse
import ast
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

MAX_FILE_BYTES = 256 * 1024
EXCLUDED_DIRECTORIES = {"node_modules", "__pycache__", "build", "dist", "out"}


def excluded(path: Path) -> bool:
    return path.name.startswith(".") or path.name in EXCLUDED_DIRECTORIES


def checked_target(root: Path, raw_path: str) -> Path:
    target = root / raw_path
    resolved = target.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError("目标必须位于当前项目内")
    # 检查原路径的每一级，不能通过项目内链接绕过目录排除规则。
    absolute = target.absolute()
    if not absolute.is_relative_to(root):
        raise ValueError("目标必须位于当前项目内")
    for part in (absolute, *absolute.parents):
        if part == root:
            break
        if part.is_symlink() or part.is_junction() or excluded(part):
            raise ValueError("目标使用了链接或位于排除目录中")
    if not resolved.exists() or (resolved.is_file() and resolved.suffix != ".py"):
        raise ValueError("目标必须是 Python 文件或包含 Python 文件的目录")
    return resolved


def raise_walk_error(error: OSError) -> None:
    raise error


def inspect_file(path: Path, root: Path) -> dict[str, Any]:
    with path.open("rb") as stream:
        data = stream.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise ValueError("文件超过 256 KiB")
    # ast.parse 接受 bytes，按 Python 编码声明解析；不执行目标模块。
    tree = ast.parse(data, filename=path.name)
    functions = classes = 0
    definitions: list[dict[str, Any]] = []
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            is_class = isinstance(node, ast.ClassDef)
            classes += int(is_class)
            functions += int(not is_class)
            definitions.append(
                {
                    "name": node.name,
                    "kind": "class" if is_class else "function",
                    "line": node.lineno,
                }
            )
        elif isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.add("." * node.level + (node.module or ""))
    definitions.sort(key=lambda row: row["line"])
    return {
        "path": path.relative_to(root).as_posix(),
        "lines": len(data.splitlines()),
        "functions": functions,
        "classes": classes,
        "definitions": definitions[:20],
        "imports": sorted(imports)[:20],
        "details_truncated": len(definitions) > 20 or len(imports) > 20,
    }


def inspect_project(root: Path, raw_path: str, max_files: int = 100) -> dict[str, Any]:
    root = root.resolve()
    if not 1 <= max_files <= 500:
        raise ValueError("max-files 需要在 1—500 之间")
    target = checked_target(root, raw_path)
    files: list[dict[str, Any]] = []
    errors: list[dict[str, object]] = []
    scanned = 0
    truncated = False

    def candidates() -> Iterator[Path]:
        if target.is_file():
            yield target
            return
        for directory, directories, names in target.walk(on_error=raise_walk_error):
            directories[:] = sorted(
                name
                for name in directories
                if not excluded(directory / name)
                and not (directory / name).is_symlink()
                and not (directory / name).is_junction()
            )
            for name in sorted(names):
                path = directory / name
                if path.suffix == ".py" and not excluded(path) and not path.is_symlink():
                    yield path

    for path in candidates():
        if scanned >= max_files:
            truncated = True
            break
        scanned += 1
        try:
            # 在真正读取前再次检查路径，避免通过链接读取排除范围。
            checked_target(root, str(path))
            files.append(inspect_file(path, root))
        except (OSError, ValueError, SyntaxError, RecursionError) as error:
            errors.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "error": type(error).__name__,
                    "line": error.lineno if isinstance(error, SyntaxError) else None,
                }
            )
    if not scanned:
        raise ValueError("目标范围内没有可扫描的 Python 文件")
    return {
        "target": target.relative_to(root).as_posix(),
        "files_scanned": scanned,
        "files_parsed": len(files),
        "truncated": truncated,
        "complete": not truncated and not errors,
        "totals": {
            key: sum(row[key] for row in files) for key in ("lines", "functions", "classes")
        },
        "files": files,
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="静态统计当前项目内的 Python 源码")
    parser.add_argument("--path", default="src/jixue", help="项目相对路径")
    parser.add_argument("--max-files", type=int, default=100, help="最多尝试的文件数，1—500")
    args = parser.parse_args()
    try:
        report = inspect_project(Path.cwd(), args.path, args.max_files)
    except (OSError, ValueError, RuntimeError) as error:
        detail = str(error) if isinstance(error, ValueError) else "无法遍历目标目录"
        print(json.dumps({"error": detail}, ensure_ascii=False))
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
