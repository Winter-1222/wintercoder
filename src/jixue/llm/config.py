"""读取并校验霁雪的模型目录。

这个文件位于“配置文件”和“真实 LLM 适配器”之间：

1. 上游是 `config/models.yaml`、可选的本地覆盖文件和进程环境变量。
2. 本文件把不可信的 YAML 数据检查成有明确类型的 Python 对象。
3. 下游的 LLM 工厂和 Anthropic 适配器只接收 `LLMConfig`，不再理解 YAML。

把加载和校验集中在这里有两个好处。第一，配置错误会在靠近文件的位置得到清楚提示；
第二，将来换供应商时，上层仍然只认识四个领域字段，不会依赖某个 SDK 的配置对象。
"""

from __future__ import annotations

import argparse
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import cast
from urllib.parse import urlparse

import yaml

# 当前只实现 Anthropic 协议适配器。以后增加协议时，由适配器工厂同步扩展这里。
SUPPORTED_PROTOCOLS = frozenset({"anthropic"})

# 环境变量必须占满整个字段，例如 `${DEEPSEEK_API_KEY}`。
# 首版不支持 "https://${HOST}/api" 这种字符串内插，避免难以理解的半替换结果。
ENV_REFERENCE_PATTERN = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")

# 模型目录 ID 会被 UI 和会话状态引用，因此限制为稳定、易读的英文小写形式。
MODEL_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


class ModelCatalogError(ValueError):
    """表示模型目录无法安全使用。

    这类错误属于用户可修复的配置问题，例如字段写错、默认模型不存在或 YAML 损坏。
    错误消息可以展示给用户，但绝不能把已经展开的 API Key 拼进消息。
    """


class CredentialStatus(StrEnum):
    """模型认证信息是否已经准备好。

    `CREDENTIALS_MISSING` 不是目录结构错误。应用仍可启动和展示模型，只是在用户真正
    发送请求前应提示设置环境变量。
    """

    READY = "ready"
    CREDENTIALS_MISSING = "credentials_missing"


@dataclass(frozen=True, slots=True)
class LLMConfig:
    """供应商无关的最小 LLM 配置。

    这四个字段就是下游适配器能够看到的全部配置：

    - `protocol`：决定使用哪一种 API 协议适配器。
    - `model`：实际发送给供应商的模型名称。
    - `base_url`：请求发往的服务端点。
    - `api_key`：认证信息；环境变量缺失时为 `None`。

    `repr=False` 很重要：调试器打印对象时不会顺手把真实 Key 写进终端或日志。
    """

    protocol: str
    model: str
    base_url: str
    api_key: str | None = field(repr=False)

    @property
    def credential_status(self) -> CredentialStatus:
        """返回给应用层可公开展示的凭据状态，不返回 Key 本身。"""

        if self.api_key is None:
            return CredentialStatus.CREDENTIALS_MISSING
        return CredentialStatus.READY


@dataclass(frozen=True, slots=True)
class ModelDefinition:
    """模型选择器中的一条完整目录记录。

    展示名称、实验标记、能力和上下文窗口属于应用元数据，不属于 `LLMConfig`。
    这样切换 SDK 时，界面和模型目录不需要跟着某个供应商的类型一起变化。
    """

    model_id: str
    label: str
    experimental: bool
    capabilities: tuple[str, ...]
    context_window: int
    llm: LLMConfig


@dataclass(frozen=True, slots=True)
class ModelCatalog:
    """经过合并、展开和校验后可供应用使用的模型目录。"""

    schema_version: int
    default_model: str
    models: Mapping[str, ModelDefinition]

    def get(self, model_id: str) -> ModelDefinition:
        """按稳定目录 ID 取模型；找不到时给出包含可选项的领域错误。"""

        try:
            return self.models[model_id]
        except KeyError as error:
            choices = "、".join(self.models)
            raise ModelCatalogError(
                f"模型目录中不存在 {model_id!r}，可选值：{choices}"
            ) from error

    @property
    def default(self) -> ModelDefinition:
        """返回默认模型记录，调用方无需重复用 ID 查询。"""

        return self.get(self.default_model)


def load_model_catalog(
    default_path: Path,
    *,
    local_path: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> ModelCatalog:
    """读取默认目录和本地覆盖，返回不可随意修改的 `ModelCatalog`。

    调用者通常只传 `config/models.yaml`。如果没有显式传 `local_path`，函数会自动在
    同一目录寻找 `models.local.yaml`；文件不存在就跳过。本地文件中的同名模型会整体
    替换默认条目，不做字段级深合并，因此覆盖条目也必须写完整。

    `environ` 默认使用当前 Python 进程环境。测试可以传入普通字典，从而不读取开发者
    电脑上的真实 Key。
    """

    environment = os.environ if environ is None else environ
    resolved_local_path = (
        default_path.with_name(f"{default_path.stem}.local{default_path.suffix}")
        if local_path is None
        else local_path
    )

    default_document = _read_yaml_mapping(default_path)
    merged_document = _merge_local_document(
        default_document,
        resolved_local_path,
    )
    return _parse_catalog(merged_document, environment)


def format_catalog_summary(catalog: ModelCatalog) -> str:
    """生成不含 API Key 的终端摘要，供初学者手动检查配置。"""

    lines = [
        "模型目录加载成功",
        f"默认模型：{catalog.default_model}",
        f"模型数量：{len(catalog.models)}",
    ]
    for model_id, definition in catalog.models.items():
        experiment = "，实验模型" if definition.experimental else ""
        lines.append(
            f"- {model_id}: {definition.label} → {definition.llm.model}"
            f"（{definition.llm.credential_status.value}{experiment}）"
        )
    return "\n".join(lines)


def _read_yaml_mapping(path: Path) -> dict[str, object]:
    """读取一个 YAML 文件，并保证最外层是字符串键对象。"""

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise ModelCatalogError(f"无法读取模型配置 {path}：{error}") from error

    try:
        # safe_load 不会执行 YAML 中的 Python 对象标签，适合读取外部配置。
        loaded: object = yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise ModelCatalogError(f"模型配置 {path} 不是合法 YAML：{error}") from error

    return _require_mapping(loaded, str(path))


def _merge_local_document(
    default_document: dict[str, object],
    local_path: Path,
) -> dict[str, object]:
    """按“同名模型整体替换”的规则合并可选本地文件。"""

    if not local_path.exists():
        # 本地覆盖是可选项；没有文件是最常见、也是完全正常的情况。
        return dict(default_document)

    local_document = _read_yaml_mapping(local_path)
    _reject_unknown_fields(
        local_document,
        {"schema_version", "default_model", "models"},
        str(local_path),
    )

    merged = dict(default_document)
    if "schema_version" in local_document:
        merged["schema_version"] = local_document["schema_version"]
    if "default_model" in local_document:
        merged["default_model"] = local_document["default_model"]
    if "models" in local_document:
        default_models = _require_mapping(merged.get("models"), "models")
        local_models = _require_mapping(local_document["models"], f"{local_path}.models")
        # 字典展开顺序决定右侧 local_models 覆盖同名键，未覆盖的默认模型仍然保留。
        merged["models"] = {**default_models, **local_models}
    return merged


def _parse_catalog(
    document: dict[str, object],
    environment: Mapping[str, str],
) -> ModelCatalog:
    """把合并后的普通字典逐层转换成强类型领域对象。"""

    _require_exact_fields(
        document,
        {"schema_version", "default_model", "models"},
        "模型目录",
    )

    schema_version = _require_positive_int(
        document["schema_version"],
        "schema_version",
    )
    if schema_version != 1:
        raise ModelCatalogError(
            f"不支持模型目录 schema_version={schema_version}，当前只支持 1"
        )

    default_model = _require_non_empty_string(
        document["default_model"],
        "default_model",
    )
    raw_models = _require_mapping(document["models"], "models")
    if not raw_models:
        raise ModelCatalogError("models 至少要包含一个模型")

    parsed_models: dict[str, ModelDefinition] = {}
    for model_id, raw_definition in raw_models.items():
        parsed_models[model_id] = _parse_model_definition(
            model_id,
            raw_definition,
            environment,
        )

    if default_model not in parsed_models:
        choices = "、".join(parsed_models)
        raise ModelCatalogError(
            f"default_model={default_model!r} 不存在，可选值：{choices}"
        )

    # MappingProxyType 提供只读视图，防止运行中无意改掉全局模型目录。
    readonly_models = MappingProxyType(parsed_models)
    return ModelCatalog(schema_version, default_model, readonly_models)


def _parse_model_definition(
    model_id: str,
    value: object,
    environment: Mapping[str, str],
) -> ModelDefinition:
    """校验一个模型条目，并把嵌套的 llm 字段转换成 `LLMConfig`。"""

    location = f"models.{model_id}"
    if not MODEL_ID_PATTERN.fullmatch(model_id):
        raise ModelCatalogError(
            f"{location} 的 ID 只能使用小写字母、数字和下划线，且必须以字母开头"
        )

    raw = _require_mapping(value, location)
    _require_exact_fields(
        raw,
        {
            "label",
            "experimental",
            "capabilities",
            "context_window",
            "llm",
        },
        location,
    )

    label = _require_non_empty_string(raw["label"], f"{location}.label")
    experimental = _require_bool(raw["experimental"], f"{location}.experimental")
    capabilities = _require_string_tuple(
        raw["capabilities"],
        f"{location}.capabilities",
    )
    context_window = _require_positive_int(
        raw["context_window"],
        f"{location}.context_window",
    )
    llm = _parse_llm_config(raw["llm"], f"{location}.llm", environment)

    return ModelDefinition(
        model_id=model_id,
        label=label,
        experimental=experimental,
        capabilities=capabilities,
        context_window=context_window,
        llm=llm,
    )


def _parse_llm_config(
    value: object,
    location: str,
    environment: Mapping[str, str],
) -> LLMConfig:
    """严格读取四字段配置；多字段和少字段都会被拒绝。"""

    raw = _require_mapping(value, location)
    _require_exact_fields(
        raw,
        {"protocol", "model", "base_url", "api_key"},
        location,
    )

    protocol = _expand_required_string(
        raw["protocol"],
        f"{location}.protocol",
        environment,
    )
    if protocol not in SUPPORTED_PROTOCOLS:
        supported = "、".join(sorted(SUPPORTED_PROTOCOLS))
        raise ModelCatalogError(
            f"{location}.protocol={protocol!r} 尚未安装适配器，当前支持：{supported}"
        )

    model = _expand_required_string(
        raw["model"],
        f"{location}.model",
        environment,
    )
    base_url = _expand_required_string(
        raw["base_url"],
        f"{location}.base_url",
        environment,
    )
    _validate_base_url(base_url, f"{location}.base_url")

    api_key = _expand_optional_secret(
        raw["api_key"],
        f"{location}.api_key",
        environment,
    )
    return LLMConfig(protocol, model, base_url, api_key)


def _expand_required_string(
    value: object,
    location: str,
    environment: Mapping[str, str],
) -> str:
    """读取必填字符串；若引用环境变量，该变量必须存在且非空。"""

    raw = _require_non_empty_string(value, location)
    variable = _extract_environment_reference(raw, location)
    if variable is None:
        return raw

    expanded = environment.get(variable, "").strip()
    if not expanded:
        raise ModelCatalogError(f"{location} 需要环境变量 {variable}，但它尚未设置")
    return expanded


def _expand_optional_secret(
    value: object,
    location: str,
    environment: Mapping[str, str],
) -> str | None:
    """展开认证字段；缺失环境变量时返回 `None`，不把它当成致命错误。"""

    if not isinstance(value, str):
        raise ModelCatalogError(f"{location} 必须是字符串")

    raw = value.strip()
    if not raw:
        return None

    variable = _extract_environment_reference(raw, location)
    if variable is None:
        # 本地忽略文件允许直接写 Key；提交文件应始终使用环境变量占位符。
        return raw

    expanded = environment.get(variable, "").strip()
    return expanded or None


def _extract_environment_reference(value: str, location: str) -> str | None:
    """识别完整的 `${VAR}` 引用，并拒绝首版不支持的字符串内插。"""

    match = ENV_REFERENCE_PATTERN.fullmatch(value)
    if match is not None:
        return match.group(1)
    if "${" in value:
        raise ModelCatalogError(
            f"{location} 只支持整个字段写成 ${{VAR}}，暂不支持字符串内插"
        )
    return None


def _validate_base_url(value: str, location: str) -> None:
    """确认端点至少具有 HTTP(S) 协议和主机名。"""

    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ModelCatalogError(
            f"{location} 必须是完整的 http:// 或 https:// 地址"
        )


def _require_mapping(value: object, location: str) -> dict[str, object]:
    """把 YAML 对象收窄为字符串键字典，给后续代码稳定类型。"""

    if not isinstance(value, dict):
        raise ModelCatalogError(f"{location} 必须是键值对象")

    result: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise ModelCatalogError(f"{location} 的字段名必须是字符串")
        result[key] = item
    return result


def _require_exact_fields(
    value: Mapping[str, object],
    expected: set[str],
    location: str,
) -> None:
    """同时拒绝缺失字段和拼错后产生的多余字段。"""

    actual = set(value)
    missing = sorted(expected - actual)
    unknown = sorted(actual - expected)
    if missing:
        raise ModelCatalogError(f"{location} 缺少字段：{'、'.join(missing)}")
    if unknown:
        raise ModelCatalogError(f"{location} 包含未知字段：{'、'.join(unknown)}")


def _reject_unknown_fields(
    value: Mapping[str, object],
    allowed: set[str],
    location: str,
) -> None:
    """本地覆盖允许省略顶层字段，但不允许出现拼错的字段。"""

    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ModelCatalogError(f"{location} 包含未知字段：{'、'.join(unknown)}")


def _require_non_empty_string(value: object, location: str) -> str:
    """返回去除两端空白后的字符串，空值会得到定位清楚的错误。"""

    if not isinstance(value, str) or not value.strip():
        raise ModelCatalogError(f"{location} 必须是非空字符串")
    return value.strip()


def _require_bool(value: object, location: str) -> bool:
    """严格接收 YAML true/false，避免把 0、1 或文本误当布尔值。"""

    if type(value) is not bool:
        raise ModelCatalogError(f"{location} 必须是 true 或 false")
    # 经过上面的严格类型判断，Mypy 和 Python 都已知道这里一定是 bool。
    return value


def _require_positive_int(value: object, location: str) -> int:
    """严格接收正整数；Python 中 bool 是 int 子类，所以要排除布尔值。"""

    if type(value) is not int or value <= 0:
        raise ModelCatalogError(f"{location} 必须是正整数")
    # 这里已经排除了 bool 和其他类型，不需要再做多余的类型转换。
    return value


def _require_string_tuple(value: object, location: str) -> tuple[str, ...]:
    """把 YAML 字符串列表变成不可变元组，并拒绝空项和重复项。"""

    if not isinstance(value, list) or not value:
        raise ModelCatalogError(f"{location} 必须是至少含一项的字符串列表")

    items: list[str] = []
    for index, item in enumerate(value):
        items.append(_require_non_empty_string(item, f"{location}[{index}]"))
    if len(set(items)) != len(items):
        raise ModelCatalogError(f"{location} 不能包含重复能力")
    return tuple(items)


def _default_config_path() -> Path:
    """优先使用当前项目目录，找不到时再从源码位置反推仓库根目录。"""

    from_working_directory = Path.cwd() / "config" / "models.yaml"
    if from_working_directory.exists():
        return from_working_directory
    return Path(__file__).resolve().parents[3] / "config" / "models.yaml"


def main(argv: Sequence[str] | None = None) -> int:
    """提供一个只读诊断入口，方便不写 Python 代码也能手动检查配置。"""

    parser = argparse.ArgumentParser(description="检查霁雪模型目录，不会请求任何 LLM")
    parser.add_argument(
        "--config",
        type=Path,
        default=_default_config_path(),
        help="默认模型目录路径",
    )
    parser.add_argument(
        "--local",
        type=Path,
        default=None,
        help="可选的本地覆盖路径；不传时自动寻找 models.local.yaml",
    )
    arguments = parser.parse_args(argv)
    config_path = cast(Path, arguments.config)
    local_path = cast(Path | None, arguments.local)

    try:
        catalog = load_model_catalog(config_path, local_path=local_path)
    except ModelCatalogError as error:
        # 这里只输出经过设计的领域错误，不输出对象 dump，避免 Key 混入终端。
        parser.exit(1, f"模型目录加载失败：{error}\n")

    print(format_catalog_summary(catalog))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
