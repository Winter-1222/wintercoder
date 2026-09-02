# 目录与文件职责

这份文件只回答一个问题：当前每个目录和源码文件是干什么的。新增、删除文件时同步更新这里。

## 总览

```text
myAgent/
├─ apps/desktop/       Electron + React 桌面端
├─ config/             模型选择配置
├─ docs/               路线、目录说明和每章唯一的 README
├─ scripts/            本地检查脚本
├─ src/jixue/          Python 后端核心
├─ tests/              本地测试，Git 忽略
├─ .env                本地密钥，Git 忽略
├─ .env.example        不含密钥的配置示例
├─ pyproject.toml      Python 项目和检查工具配置
└─ package.json        前端命令总入口
```

## Python 后端

| 文件 | 职责 |
| --- | --- |
| `src/jixue/agent.py` | Agent 核心：循环调用 LLM、执行工具、判断停止并响应取消信号 |
| `src/jixue/domain/conversation.py` | 普通消息、工具内容块、多轮历史，以及完成/取消消息的 API 前清洗 |
| `src/jixue/domain/events.py` | Electron 与 Python 之间的一行 JSON 信封 |
| `src/jixue/llm/base.py` | 霁雪自己的 LLM 接口和流式事件 |
| `src/jixue/llm/fake.py` | 离线模拟 LLM；`/read` 测一次工具，`/loop` 测两次工具循环 |
| `src/jixue/llm/config.py` | 从 `models.yaml` 和环境中生成四字段配置 |
| `src/jixue/llm/adapters/anthropic_client.py` | 唯一接触 Anthropic SDK，解析流并转换工具内容块 |
| `src/jixue/bridge/bootstrap.py` | 读取项目根目录 `.env`，选择 Fake 或真实 LLM |
| `src/jixue/bridge/application.py` | 转发 chat.send/chat.cancel，并为 Agent 事件包装信封 |
| `src/jixue/bridge/server.py` | 从 stdin 收 JSON，从 stdout 发 JSON |
| `src/jixue/bridge/__main__.py` | 让 `python -m jixue.bridge` 能启动 |
| `src/jixue/tools/base.py` | 工具合同、ToolResult 和通用 BaseTool |
| `src/jixue/tools/registry.py` | 注册、启用、禁用、导出定义和按名称执行工具 |
| `src/jixue/tools/read_file.py` | 第一个只读文件工具工厂 |
| `src/jixue/tools/__init__.py` | 工具层公开导入入口 |

`agent.py` 是现在最先阅读的核心；`domain` 不知道 Electron 和 Anthropic；`adapters` 藏住外部 SDK；`bridge` 只负责连接桌面端。

## Electron 桌面端

| 文件 | 职责 |
| --- | --- |
| `apps/desktop/src/main/index.ts` | 创建窗口、校验聊天/取消 IPC，并启动或关闭 Python |
| `apps/desktop/src/main/bridge-process.ts` | 启动 Conda 子进程，发送聊天/取消命令并处理 NDJSON |
| `apps/desktop/src/preload/index.ts` | 只向网页暴露安全的聊天、取消和事件订阅接口 |
| `apps/desktop/src/shared/protocol.ts` | 前后端共用的事件信封和桌面 API 类型 |
| `apps/desktop/src/renderer/src/App.tsx` | 聊天页面，接收循环事件并在运行时展示停止按钮 |
| `apps/desktop/src/renderer/src/state.ts` | reducer：更新工具、轮次、正在停止和已停止状态 |
| `apps/desktop/src/renderer/src/styles.css` | Codex 风格的聊天、工具卡片和停止按钮样式 |
| `apps/desktop/src/renderer/src/main.tsx` | React 页面入口 |

其余 `electron.vite.config.ts`、`tsconfig.json` 和各级 `package.json` 是构建配置，不参与一条消息的业务处理。

## 配置和文档

| 文件 | 职责 |
| --- | --- |
| `config/models.yaml` | 三个模型的短名称和真实模型 ID |
| `docs/ROADMAP.md` | 章节顺序和一周范围 |
| `docs/chapters/00-foundation/README.md` | 环境准备、启动和测试 |
| `docs/chapters/01-llm-ui/README.md` | 第一章代码和完整消息链路 |
| `docs/chapters/02-tools/README.md` | 第二章工具底座和执行链路 |
| `docs/chapters/03-agent-loop/README.md` | 第三章 Agent 核心、循环步骤和手测记录 |
| `scripts/test-all.ps1` | 顺序执行本地自动化检查 |

每章目录只允许有一个 `README.md`。测试文件虽然存在于本机，但由 `.gitignore` 排除，不会提交。
