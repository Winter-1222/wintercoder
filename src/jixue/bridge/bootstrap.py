"""根据项目 `.env` 组装 Python Bridge 使用的 LLM 客户端。

这个文件是“配置对象”和“BridgeServer”之间的组装层，也叫 bootstrap（启动装配）。
它只决定本次进程使用 FakeLLM 还是模型目录中的正式客户端，不读取 stdin、不写 stdout，
也不处理聊天业务。把选择集中在这里后，server.py 继续只负责进程运输。

当前支持两个启动变量：

- `JIXUE_LLM_MODE`：`fake` 或 `configured`，缺省为 `fake`。
- `JIXUE_MODEL_ID`：configured 模式下选择目录 ID；缺省使用 models.yaml 的默认模型。

正式运行时会读取“项目根目录/.env”。文件里的同名变量优先于 Python 进程继承到的系统
环境变量；系统环境只作为兜底。这样无论从哪个终端启动 Electron，项目配置都保持一致。
这些值只留在 Python Bridge 内部，不会暴露给 Renderer。
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path

from dotenv import dotenv_values

from jixue.llm.base import LLMClient
from jixue.llm.config import ModelCatalogError, load_model_catalog
from jixue.llm.factory import create_llm_client
from jixue.llm.fake import FakeLLMClient

# 常量统一保存环境变量名，避免不同文件手写字符串时出现拼写不一致。
LLM_MODE_ENV = "JIXUE_LLM_MODE"
MODEL_ID_ENV = "JIXUE_MODEL_ID"
# 文件名也用常量集中保存，后续若支持多环境文件，只改装配层即可。
DOTENV_FILENAME = ".env"


class LLMRuntimeMode(StrEnum):
    """Bridge 进程当前允许的两种模型启动模式。"""

    # 默认离线模式：不读 Key、不访问网络，适合教学、UI 开发和自动化测试。
    FAKE = "fake"
    # 配置模式：从 config/models.yaml 选择模型，再交给 LLM 工厂创建正式客户端。
    CONFIGURED = "configured"


class BridgeBootstrapError(ValueError):
    """表示 Bridge 无法根据启动配置选择一个可用的 LLM 客户端。

    消息可以写到 stderr 给开发者查看，但不能包含 API Key。模型目录自己的安全错误会在
    本层包装成这个统一类型，让 server.py 不需要理解 YAML 或模型目录细节。
    """


def load_project_environment(
    project_root: Path,
    *,
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """合并系统环境与项目 `.env`，并返回本次启动使用的普通字典。

    可以把这个函数理解为“准备配置原料”：

    1. 先复制 `environ`；正式运行未传时复制 `os.environ`。
    2. 再读取 `project_root/.env`。
    3. 用 `.env` 的值覆盖同名系统变量，因此项目配置优先。

    测试显式传入普通字典，既能验证优先级，也不会读取测试进程中的真实 Key。这里使用
    `dotenv_values` 而不是 `load_dotenv`，是因为后者会修改全局 `os.environ`；返回
    一份局部字典更容易推理，也不会让一次测试污染另一次测试。
    """

    # 先复制，不能直接修改调用者传入的字典或操作系统维护的 os.environ。
    merged_environment = dict(os.environ if environ is None else environ)
    dotenv_path = project_root / DOTENV_FILENAME
    if not dotenv_path.is_file():
        # .env 是可选文件；没有它时保留离线 fake 默认值，方便首次启动和自动化测试。
        return merged_environment

    try:
        # 关闭 ${VAR} 插值：models.yaml 已经统一负责变量展开；这里按原样读取配置更直观。
        dotenv_environment = dotenv_values(
            dotenv_path=dotenv_path,
            encoding="utf-8",
            interpolate=False,
        )
    except OSError as error:
        # 路径和异常信息可以帮助定位权限/编码问题，但绝不能把文件内容或 Key 写进错误。
        raise BridgeBootstrapError(f"无法读取项目配置 {dotenv_path}：{error}") from error

    for name, value in dotenv_environment.items():
        # python-dotenv 把只有变量名、没有等号的行解析为 None。把它统一成空字符串，
        # 后续的 mode/key 校验就能给出领域错误，而不需要到处处理 Optional[str]。
        merged_environment[name] = "" if value is None else value
    return merged_environment


def create_runtime_llm(
    project_root: Path,
    *,
    environ: Mapping[str, str] | None = None,
) -> LLMClient:
    """根据项目配置返回本进程唯一的 `LLMClient`。

    调用者是 `bridge.server.main()`。`project_root` 决定从哪里找 `.env` 和
    `config/models.yaml`。测试可以显式传普通字典作为系统环境替身；即使传了字典，
    临时项目中的 `.env` 仍会按正式规则覆盖它。
    """

    environment = load_project_environment(project_root, environ=environ)
    raw_mode = environment.get(LLM_MODE_ENV, LLMRuntimeMode.FAKE.value)
    # 空字符串和未设置都按默认 fake 处理，确保没有有效配置时绝不会意外请求付费模型。
    normalized_mode = raw_mode.strip().lower() or LLMRuntimeMode.FAKE.value

    if normalized_mode == LLMRuntimeMode.FAKE:
        # FakeLLM 不需要读取模型目录；即使用户尚未配置 Key，桌面应用也能正常教学演示。
        return FakeLLMClient()

    if normalized_mode != LLMRuntimeMode.CONFIGURED:
        choices = "、".join(mode.value for mode in LLMRuntimeMode)
        raise BridgeBootstrapError(
            f"{LLM_MODE_ENV}={raw_mode!r} 无效，可选值：{choices}"
        )

    # Electron 启动 Python 时把 cwd 固定为项目根目录；.env 和模型目录使用同一个根。
    catalog_path = project_root / "config" / "models.yaml"
    try:
        # 显式传入合并后的 environment，模型目录才能展开 .env 中的 API Key。
        catalog = load_model_catalog(catalog_path, environ=environment)
        requested_model_id = environment.get(MODEL_ID_ENV, "").strip()
        model_id = requested_model_id or catalog.default_model
        definition = catalog.get(model_id)
    except ModelCatalogError as error:
        # ModelCatalogError 的文案经过脱敏设计；仍统一包装，隔离 server 与配置模块。
        raise BridgeBootstrapError(f"模型启动配置无效：{error}") from error

    # 工厂只接收严格四字段 LLMConfig。Key 缺失不会在这里崩溃，而会在首次发送时由
    # AnthropicLLMClient 返回 credentials_missing，让 UI 能给出可操作提示。
    return create_llm_client(definition.llm)
