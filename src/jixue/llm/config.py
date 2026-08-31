"""读取最小模型配置，并生成四字段 LLMConfig。"""

from __future__ import annotations

import argparse
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import yaml


class LLMConfigError(ValueError):
    """配置文件缺字段、模型名错误等可修复问题。"""


@dataclass(frozen=True, slots=True)
class LLMConfig:
    """适配器唯一需要的配置；Key 不参与 repr，避免日志泄露。"""

    protocol: str
    model: str
    base_url: str
    api_key: str | None = field(repr=False)


def load_llm_config(
    path: Path,
    *,
    model_id: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> LLMConfig:
    """读取三模型 YAML，选择一个模型并返回四字段配置。"""

    environment = os.environ if environ is None else environ
    try:
        document: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise LLMConfigError(f"无法读取模型配置 {path}：{error}") from error

    root = _mapping(document, "模型配置")
    protocol = _text(root.get("protocol"), "protocol")
    if protocol != "anthropic":
        raise LLMConfigError("当前只支持 anthropic 协议")

    base_url = _text(root.get("base_url"), "base_url")
    if not base_url.startswith(("http://", "https://")):
        raise LLMConfigError("base_url 必须以 http:// 或 https:// 开头")

    models = _mapping(root.get("models"), "models")
    default_model = _text(root.get("default_model"), "default_model")
    selected = (model_id or environment.get("JIXUE_MODEL_ID") or default_model).strip()
    if selected not in models:
        raise LLMConfigError(f"未知模型 {selected!r}，可选值：{'、'.join(models)}")

    model = _text(models[selected], f"models.{selected}")
    api_key = environment.get("DEEPSEEK_API_KEY", "").strip() or None
    return LLMConfig(protocol, model, base_url, api_key)


def _mapping(value: object, name: str) -> dict[str, object]:
    """把 YAML 对象收窄成字符串键字典。"""

    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise LLMConfigError(f"{name} 必须是键值对象")
    return {str(key): item for key, item in value.items()}


def _text(value: object, name: str) -> str:
    """读取必填非空字符串。"""

    if not isinstance(value, str) or not value.strip():
        raise LLMConfigError(f"{name} 必须是非空字符串")
    return value.strip()


def main(argv: Sequence[str] | None = None) -> int:
    """只读诊断入口，不请求模型，也不打印 Key。"""

    parser = argparse.ArgumentParser(description="检查霁雪模型配置")
    parser.add_argument("--config", type=Path, default=Path("config/models.yaml"))
    parser.add_argument("--model", default=None)
    arguments = parser.parse_args(argv)
    try:
        config = load_llm_config(arguments.config, model_id=arguments.model)
    except LLMConfigError as error:
        parser.exit(1, f"模型配置错误：{error}\n")

    key_status = "ready" if config.api_key else "credentials_missing"
    print(f"protocol={config.protocol}")
    print(f"model={config.model}")
    print(f"base_url={config.base_url}")
    print(f"credentials={key_status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
