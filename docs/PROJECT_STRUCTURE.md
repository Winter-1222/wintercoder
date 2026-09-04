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
├─ .jixue/             工具大结果等运行数据，Git 忽略
├─ .env                本地密钥，Git 忽略
├─ .env.example        不含密钥的配置示例
├─ pyproject.toml      Python 项目和检查工具配置
└─ package.json        前端命令总入口
```

## Python 后端

| 文件 | 职责 |
| --- | --- |
| `src/jixue/agent.py` | Agent 核心：循环调用 LLM，校验并执行工具；所有结果先交给 `ToolResultStore.prepare` 保护，再按原顺序回传 |
| `src/jixue/context.py` | 上下文保护：超过 50,000 字符的工具结果落盘，并生成有界预览和安全档案编号 |
| `src/jixue/prompt.py` | 生成稳定的七段式 System Prompt，以及每轮动态的任务模式、权限模式、时间和 Git 提醒 |
| `src/jixue/permission.py` | 权限判断核心：危险命令、路径沙箱、精确安全规则、三种权限模式和 ALLOW/DENY/ASK 结果 |
| `src/jixue/domain/conversation.py` | 普通消息、工具内容块、多轮历史，以及完成/取消消息的 API 前清洗 |
| `src/jixue/domain/events.py` | Electron 与 Python 之间的一行 JSON 信封 |
| `src/jixue/llm/base.py` | 霁雪自己的 LLM 接口；统一接收 system、messages、tools 并输出流事件 |
| `src/jixue/llm/fake.py` | 离线模拟 LLM；/read、/loop 测读取，/write 只用于权限端到端测试 |
| `src/jixue/llm/config.py` | 从 `models.yaml` 和环境中生成四字段配置 |
| `src/jixue/llm/adapters/anthropic_client.py` | 唯一接触 Anthropic SDK，把 system、messages、tools 发给协议端点并翻译流事件 |
| `src/jixue/mcp/client.py` | MCP transport 合同、stdio/HTTP 连接、`.env` URL 占位替换、握手、工具发现、限时调用；MCP SDK 只在这里出现 |
| `src/jixue/mcp/tool.py` | 把 MCP 工具定义和调用结果包装成霁雪统一的 Tool/ToolResult |
| `src/jixue/mcp/__init__.py` | MCP 客户端层公开导入入口 |
| `src/jixue/bridge/bootstrap.py` | 读取项目根目录 `.env`，选择 Fake 或真实 LLM |
| `src/jixue/bridge/application.py` | 转发任务模式、权限模式、聊天、取消和权限回复，并为 Agent 事件包装信封 |
| `src/jixue/bridge/server.py` | 从 stdin 收 JSON、从 stdout 发 JSON；注册内置工具（含 `read_artifact`），并在后台连接和重试 MCP Server |
| `src/jixue/bridge/__main__.py` | 让 `python -m jixue.bridge` 能启动 |
| `src/jixue/tools/base.py` | 工具合同、ToolResult 和通用 BaseTool |
| `src/jixue/tools/registry.py` | 注册、启用、禁用、按名称执行工具，并可只导出只读工具定义 |
| `src/jixue/tools/read_file.py` | 读取项目内 UTF-8 文本文件，可指定起止行；拒绝读取密钥和 `.jixue` 运行数据 |
| `src/jixue/tools/read_artifact.py` | 用安全编号搜索或分段读取已经落盘的工具大结果；为控制内存暂时串行执行 |
| `src/jixue/tools/glob.py` | 按 glob 模式查找项目内文件路径，跳过密钥和 `.jixue` |
| `src/jixue/tools/grep.py` | 在项目文本文件中搜索字面内容并返回行号，跳过密钥和 `.jixue` |
| `src/jixue/tools/write_tools.py` | write_file 整体写入文件；edit_file 只替换唯一匹配的文字 |
| `src/jixue/tools/bash.py` | 在项目根目录执行 PowerShell/Bash，限制时长并移除常见密钥环境变量；完整输出交给统一的上下文保护 |
| `src/jixue/tools/__init__.py` | 工具层公开导入入口 |

`agent.py` 是现在最先阅读的核心；`domain` 不知道 Electron 和 Anthropic；`adapters` 藏住外部 SDK；`bridge` 只负责连接桌面端。

## Electron 桌面端

| 文件 | 职责 |
| --- | --- |
| `apps/desktop/src/main/index.ts` | 创建窗口，校验聊天、取消、任务模式、权限模式和确认回复 IPC |
| `apps/desktop/src/main/bridge-process.ts` | 启动 Conda 子进程，处理 NDJSON，并缓存 Bridge 与每个 MCP Server 的状态 |
| `apps/desktop/src/preload/index.ts` | 只向网页暴露白名单中的聊天、取消、模式、确认和订阅接口 |
| `apps/desktop/src/shared/protocol.ts` | 前后端共用的事件信封、桌面 API、BridgeState 和 MCP 状态类型 |
| `apps/desktop/src/renderer/src/App.tsx` | 聊天页面：展示模式、权限、工具、确认操作和 MCP Server 连接状态 |
| `apps/desktop/src/renderer/src/state.ts` | reducer：更新任务模式、权限模式、工具卡片、轮次、停止和完成状态 |
| `apps/desktop/src/renderer/src/styles.css` | Codex 风格的聊天、工具、权限卡片、MCP 状态和输入区样式 |
| `apps/desktop/src/renderer/src/main.tsx` | React 页面入口 |

其余 `electron.vite.config.ts`、`tsconfig.json` 和各级 `package.json` 是构建配置，不参与一条消息的业务处理。

## 配置和文档

| 文件 | 职责 |
| --- | --- |
| `config/models.yaml` | 三个模型的短名称和真实模型 ID |
| `config/mcp.json` | 可提交的 MCP Server 公共配置，目前为空 |
| `config/mcp.local.json` | 本机 MCP 配置，覆盖公共配置并由 Git 忽略 |
| `docs/ROADMAP.md` | 章节顺序和一周范围 |
| `docs/chapters/00-foundation/README.md` | 环境准备、启动和测试 |
| `docs/chapters/01-llm-ui/README.md` | 第一章代码和完整消息链路 |
| `docs/chapters/02-tools/README.md` | 第二章工具底座和执行链路 |
| `docs/chapters/03-agent-loop/README.md` | 第三章 Agent 核心、循环步骤和手测记录 |
| `docs/chapters/04-system-prompt/README.md` | 第四章提示词分层、完整请求链路和手测说明 |
| `docs/chapters/05-permissions/README.md` | 第五章权限防线、判断链路和分步进度 |
| `docs/chapters/06-mcp/README.md` | 第六章 MCP 连接、工具包装、完整调用链和手测说明 |
| `docs/chapters/07-context/README.md` | 第七章大结果落盘、按需读取和后续压缩路线 |
| `scripts/test-all.ps1` | 顺序执行本地自动化检查 |

本地 `tests/mcp/demo_server.py` 和 `demo_http_server.py` 分别模拟 stdio 与 HTTP
Server；对应测试覆盖两条真实闭环。每章目录只允许有一个 `README.md`；这些测试
文件由 `.gitignore` 排除，不会提交。`probe_amap.py` 只诊断高德连接和工具发现，
`probe_amap_agent.py` 用真实模型走一遍“模型 → 高德工具 → 最终回答”；二者都不会
打印 URL 或 Key。

`.jixue/tool-results/` 由程序运行时自动创建。里面保存工具完整大结果，界面和模型只接收预览；该目录不属于源码，也不会提交到 Git。
