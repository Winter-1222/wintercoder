# 霁雪（Jixue）

霁雪是一个从零学习 Agent Harness 的小项目。名字来自“雪后初晴”。

第 0 至第 6 章已经完成。第 7 章正在进行：大工具结果已经可以落盘并按需读取，旧上下文清理和自动压缩尚未实现。

## 现在能做什么

- Electron 聊天界面，支持多轮对话。
- 回复过程中显示纯文本，结束后渲染 Markdown。
- 状态栏显示模型、累计 Token 和本轮耗时。
- Agent Loop 支持多轮工具调用、并发只读工具、取消和 Plan/Do 模式。
- 内置 read、write、edit、grep、glob、bash 工具，并带五层权限保护。
- 支持 stdio/Streamable HTTP MCP；连接成功后，MCP 工具立即注册并供下一次模型请求使用。
- 超过 50,000 字符的工具结果会保存到本地，模型通过 `read_artifact` 按需取回片段。
- 未配置真实模型时使用 `FakeLLM`，方便本地自动化测试。
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
- [第 2 章：工具系统](docs/chapters/02-tools/README.md)
- [第 3 章：Agent Loop](docs/chapters/03-agent-loop/README.md)
- [第 4 章：System Prompt](docs/chapters/04-system-prompt/README.md)
- [第 5 章：权限](docs/chapters/05-permissions/README.md)
- [第 6 章：MCP](docs/chapters/06-mcp/README.md)
- [第 7 章：上下文管理](docs/chapters/07-context/README.md)

每章只保留一个 README，并随着该章的小步骤继续更新。
