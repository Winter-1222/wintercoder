"""读取项目 .env，并为 Bridge 创建 Fake 或真实 LLM 客户端。"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from dotenv import dotenv_values

from jixue.llm.adapters.anthropic_client import AnthropicLLMClient
from jixue.llm.base import LLMClient
from jixue.llm.config import LLMConfigError, load_llm_config
from jixue.llm.fake import FakeLLMClient


class BridgeBootstrapError(ValueError):
    """启动配置无效，消息可安全写入 stderr。"""


def load_project_environment(
    project_root: Path,
    *,
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """系统环境作兜底，项目根目录 .env 覆盖同名值。"""

    result = dict(os.environ if environ is None else environ)
    dotenv_path = project_root / ".env"
    if not dotenv_path.is_file():
        return result
    try:
        values = dotenv_values(dotenv_path, encoding="utf-8", interpolate=False)
    except OSError as error:
        raise BridgeBootstrapError(f"无法读取 {dotenv_path}：{error}") from error
    result.update({name: value or "" for name, value in values.items()})
    return result


def create_runtime_llm(
    project_root: Path,
    *,
    environ: Mapping[str, str] | None = None,
    model_id: str | None = None,
) -> LLMClient:
    """根据运行模式和可选模型别名创建客户端，由上层负责共享。"""

    environment = load_project_environment(project_root, environ=environ)
    raw_mode = environment.get("JIXUE_LLM_MODE", "fake")
    mode = raw_mode.strip().lower() or "fake"
    if mode == "fake":
        return FakeLLMClient()
    if mode != "configured":
        raise BridgeBootstrapError(f"JIXUE_LLM_MODE={raw_mode!r} 无效，可选值：fake、configured")

    try:
        config = load_llm_config(
            project_root / "config" / "models.yaml",
            model_id=model_id or environment.get("JIXUE_MODEL_ID"),
            environ=environment,
        )
    except LLMConfigError as error:
        raise BridgeBootstrapError(f"模型启动配置无效：{error}") from error
    return AnthropicLLMClient(config)
