"""对首章最重要的依赖边界做回归检查。"""

import ast
from pathlib import Path


def test_domain_does_not_import_external_sdks() -> None:
    forbidden_roots = {"anthropic", "electron", "mcp"}
    domain_root = Path("src/jixue/domain")

    violations: list[str] = []
    for path in domain_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = {alias.name.split(".", 1)[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                roots = {node.module.split(".", 1)[0]}
            else:
                continue
            if roots & forbidden_roots:
                violations.append(str(path))

    assert violations == []

