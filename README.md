# 霁雪（Jixue）

霁雪是一个从零学习 Agent Harness 的小项目。名字来自“雪后初晴”。

当前只完成第一章：把用户消息从 Electron 界面交给 Python，再由 LLM 流式返回。工具系统、Agent Loop、权限、MCP 等功能都还没有开始。

## 现在能做什么

- Electron 聊天界面，支持多轮对话。
- 回复过程中显示纯文本，结束后渲染 Markdown。
- 状态栏显示模型、累计 Token 和本轮耗时。
- 默认使用免费的 `FakeLLM`，不联网也能走通整条链路。
- 在项目根目录 `.env` 中配置后，可调用 Anthropic 协议兼容的 DeepSeek 模型。
- Python 领域代码不依赖 Anthropic SDK，后续更换供应商只改适配器。

## 启动

第一次使用先安装依赖：

```powershell
conda run --no-capture-output -n mycoder python -m pip install -e ".[dev]"
npm install
```

启动桌面端：

```powershell
npm run dev
```

不配置 `.env` 时会使用 `FakeLLM`。调用真实模型时，在项目根目录创建 `.env`：

```dotenv
JIXUE_LLM_MODE=configured
JIXUE_MODEL_ID=flash
DEEPSEEK_API_KEY=你的Key
```

`.env` 已被 Git 忽略，不能把 Key 写进源码或提交记录。

## 测试

```powershell
conda run --no-capture-output -n mycoder pytest
conda run --no-capture-output -n mycoder ruff check .
conda run --no-capture-output -n mycoder mypy src
npm run test:frontend
npm run typecheck
npm run test:electron
```

测试源码在本机 `tests/` 中，通过 `.gitignore` 排除，不进入 Git。

## 文档

- [开发路线](docs/ROADMAP.md)
- [目录说明](docs/PROJECT_STRUCTURE.md)
- [第 0 章：工程准备](docs/chapters/00-foundation/README.md)
- [第 1 章：让 AI 开口说话](docs/chapters/01-llm-ui/README.md)

第一章 README 是理解当前代码的主入口。每章只保留一个 README，不再拆成许多文档。
