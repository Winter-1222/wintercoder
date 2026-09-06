# 霁雪（Jixue）

霁雪是一个从零学习 Agent Harness 的小项目。名字来自“雪后初晴”。

第 0 至第 8 章已经完成。第 7 章包含统一消息序列、大结果落盘、旧工具结果清理、手动/自动压缩、超长请求单次重试和连续失败暂停。第 8 章加入会话保存/恢复/切换、项目指令和项目记忆。

## 现在能做什么

- Electron 聊天界面，支持多轮对话、新建和切换会话；重启恢复上次记录与工作上下文。
- 每个任务加载根目录 AGENTS.md 和记忆索引；动态记忆分四种类型独立存储，正文按需读取，记住/忘记通过工具与权限链执行。
- 回复过程中显示纯文本，结束后渲染 Markdown。
- 状态栏显示模型、累计 Token 和本轮耗时。
- Agent Loop 支持多轮工具调用、并发只读工具、取消和 Plan/Do 模式。
- 内置 read、write、edit、grep、glob、bash 工具，并带五层权限保护。
- 支持 stdio/Streamable HTTP MCP；连接成功后，MCP 工具立即注册并供下一次模型请求使用。
- 超过 50,000 字符的工具结果会保存到本地，模型通过 `read_artifact` 按需取回片段。
- 输入 `/compact` 可将较早普通对话整理成摘要，同时保留最近 2 个完整对话轮。
- 本次即将发送的工作消息超过 160,000 字符时，会复用同一摘要事务自动压缩并重建请求。
- 供应商报告上下文超长时会压缩、重建并重试一次；自动摘要连续失败 3 次后暂停，手动压缩成功即可恢复。
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
- [第 8 章：会话与项目记忆](docs/chapters/08-memory/README.md)

每章只保留一个 README，并随着该章的小步骤继续更新。
