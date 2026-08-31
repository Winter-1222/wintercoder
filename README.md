# 霁雪（Jixue）

霁雪是一个以教学和可复盘开发为目标的轻量 Agent Harness。名字取自“雪后初晴”：内核保持清晰，外部模型、工具和界面都通过边界明确的适配器接入。

所有章节教程默认面向几乎零基础的读者：先解释术语和推荐阅读顺序，再用一条真实运行链路把目录、文件和函数串起来，不要求读者预先掌握 Electron、React、Python 异步或 LLM SDK。

当前已完成工程基线和第一章聊天闭环：无需 API Key 的 `FakeLLM` 可以经过 Python NDJSON Bridge，把流式事件送到 Electron 界面；模型目录加载器可以读取默认 YAML、本地覆盖和环境变量，并生成严格的四字段 `LLMConfig`；Anthropic 协议适配器可以使用官方异步 SDK，把供应商文本流、Token 与停止原因转换成霁雪事件；Bridge 启动入口可以在默认 FakeLLM 和三个 DeepSeek 目录模型之间显式选择；`ConversationManager` 会清洗内部消息，并由 Bridge、FakeLLM 与 Anthropic 适配器按轮次传递完整历史。界面在流式阶段显示原始文本，收到完成事件后再渲染 Markdown，并展示实际模型名、累计 Token 和耗时。

Python Bridge 会主动读取项目根目录的 `.env`：文件里的同名值优先于系统环境变量。没有 `.env`，或其中选择 `JIXUE_LLM_MODE=fake` 时，继续使用免费、离线、确定性的 FakeLLM；选择 `configured` 时才从模型目录创建正式客户端。项目已经验证当前 `.env` 能装配出默认 `deepseek-v4-flash`，并完成十轮历史、失败流隔离和退出回归；自动化不会替用户发送真实付费请求。Flash、Pro、Vision Exp 目前通过修改 `JIXUE_MODEL_ID` 并重启来选择，界面内热切换属于后续体验增强。

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

Python 会从运行项目的当前目录读取 `.env`，不是只读操作系统的全局环境变量。若文件不存在或模式为 `fake`，窗口会显示“fake-jixue / Bridge 在线”，发送不会访问网络；若 `.env` 中是 `configured` 且 Key 有效，窗口会显示所选真实模型，发送消息会访问模型服务并可能产生费用。

第一次配置真实模型时，把 `.env.example` 复制为 `.env`，只在 `.env` 中填写 Key：

```powershell
Copy-Item .env.example .env
```

`.env` 已被 Git 忽略；不要把真实 Key 写入 `.env.example`、`models.yaml`、测试或聊天消息。

不启动 UI、只检查三模型配置：

```powershell
conda run --no-capture-output -n mycoder python -m jixue.llm.config
```

命令只显示模型名和凭据状态，不会打印 API Key，也不会发起网络请求。

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
