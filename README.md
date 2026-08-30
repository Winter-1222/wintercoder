# 霁雪（Jixue）

霁雪是一个以教学和可复盘开发为目标的轻量 Agent Harness。名字取自“雪后初晴”：内核保持清晰，外部模型、工具和界面都通过边界明确的适配器接入。

所有章节教程默认面向几乎零基础的读者：先解释术语和推荐阅读顺序，再用一条真实运行链路把目录、文件和函数串起来，不要求读者预先掌握 Electron、React、Python 异步或 LLM SDK。

当前已完成工程基线和第一章的第一小步：无需 API Key 的 `FakeLLM` 可以经过 Python NDJSON Bridge，把流式事件送到 Electron 界面。界面会在流式阶段显示原始文本，收到完成事件后再渲染 Markdown，并展示模型名、Token 和耗时。

真实 DeepSeek 适配器、配置加载、对话管理器和模型切换仍是第一章后续内容；现在的版本不冒充完整第一章。

## 开发基线

- Python：Conda 环境 `mycoder`，当前为 Python 3.12.13
- 桌面端：Electron + TypeScript
- LLM SDK：Python `anthropic`，仅允许出现在 LLM 适配器内
- 默认模型：`deepseek-v4-flash`
- 默认协议端点：Anthropic 协议，`https://api.deepseek.com/anthropic`
- 版本管理：Git，主分支 `main`

## 文档入口

- [总体开发路线](docs/ROADMAP.md)
- [总体架构](docs/ARCHITECTURE.md)
- [目录与文件职责](docs/PROJECT_STRUCTURE.md)
- [开发与复盘约定](docs/DEVELOPMENT_GUIDE.md)
- [第 0 章：工程基线](docs/chapters/00-foundation/README.md)
- [第 1 章：FakeLLM 流式界面](docs/chapters/01-llm-ui/README.md)

## 首次运行

```powershell
conda run --no-capture-output -n mycoder python -m pip install --index-url https://pypi.org/simple -e ".[dev]"
npm install
npm run dev
```

打开窗口并等到右上角出现“FakeLLM / Bridge 在线”，即可发送消息。当前链路只使用确定性的 FakeLLM，不访问网络，也不会产生模型费用。

## 验证命令

```powershell
npm run test:all
npm run typecheck
conda run --no-capture-output -n mycoder ruff check .
conda run --no-capture-output -n mycoder mypy src
npm run test:electron
```

`test:electron` 会构建并短暂启动一个真实 Electron 窗口，自动发送消息后关闭。每章仍会保留独立的教学、开发日志和手动测试记录，便于日后复盘。

测试源码仅保留在当前开发机，已通过 `.gitignore` 排除，不进入 Git。上面的测试命令在当前工作区仍可直接执行；从全新克隆恢复测试夹具需要另行准备本地测试文件。
