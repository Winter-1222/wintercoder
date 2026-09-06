# 目录与文件职责

这份文件只回答一个问题：当前每个目录和源码文件是干什么的。新增、删除文件时同步更新这里。

## 总览

```text
myAgent/
├─ apps/desktop/       Electron + React 桌面端
├─ assert/             README 使用的项目演示截图
├─ config/             模型、MCP 和 Markdown 子角色配置
├─ docs/               路线、目录说明和每章唯一的 README
├─ scripts/            本地检查脚本
├─ src/jixue/          Python 后端核心（agent.py 组装，agent_runtime/ 执行）
├─ tests/              本地测试，Git 忽略
├─ .jixue/             会话、项目记忆和工具大结果等运行数据，Git 忽略
├─ .env                本地密钥，Git 忽略
├─ .env.example        不含密钥的配置示例
├─ pyproject.toml      Python 项目和检查工具配置
└─ package.json        前端命令总入口
```

## Python 后端

| 文件 | 职责 |
| --- | --- |
| `src/jixue/agent.py` | 唯一组装与对外入口：显式连接运行控制、模型流、工具执行、压缩器和循环，分发普通任务与 `/compact` |
| `src/jixue/agent_runtime/__init__.py` | Agent 内部运行组件包；不反向导入组装入口 |
| `src/jixue/agent_runtime/loop.py` | 普通任务循环：请求模型、执行工具、成对写回会话、任务收尾；控制自动摘要和超长单次重试 |
| `src/jixue/agent_runtime/model.py` | 可取消模型流，逐次响应收集与正文、工具、用量事件转换；不执行工具或决定压缩 |
| `src/jixue/agent_runtime/execution.py` | 工具参数与权限检查、确认等待、安全分批并发、结果落盘及连续异常工具保护 |
| `src/jixue/agent_runtime/compaction.py` | 请求消息整理、动态提醒、手动/自动摘要共用事务，以及连续失败暂停 |
| `src/jixue/agent_runtime/control.py` | 运行状态、Plan/Do、权限模式、统一取消信号及一次性权限回复 |
| `src/jixue/agent_runtime/events.py` | Agent 事件合同与公共事件构造，不持有会话或执行组件 |
| `src/jixue/context.py` | 三层上下文策略：大结果落盘、成轮清理旧工具正文、摘要触发与资料文字转换；无独立活动状态 |
| `src/jixue/prompt.py` | 拼装基础 System Prompt、项目指令和记忆，以及每轮模式、时间和 Git 动态提醒 |
| `src/jixue/project_context.py` | 有界读取根目录 AGENTS.md，拒绝指向项目外的路径 |
| `src/jixue/memory.py` | 独立记忆存储、直接读磁盘索引、按需读正文、变更后更新索引与显式重建、进程内锁 |
| `src/jixue/memory_format.py` | 四种记忆类型、frontmatter 合同、名称与大小校验、正文和索引格式、磁盘索引文本校验 |
| `src/jixue/sessions/__init__.py` | 会话持久化包 |
| `src/jixue/sessions/codec.py` | 工作消息、工具块、摘要、用量及累计任务数的快照编解码 |
| `src/jixue/sessions/store.py` | JSONL 追加、最新快照恢复、UI 回放记录、半条尾部处理及会话列表 |
| `src/jixue/subagents/__init__.py` | 子任务模块入口 |
| `src/jixue/subagents/definitions.py` | 内置角色、Markdown/YAML 角色校验和提示词目录 |
| `src/jixue/subagents/manager.py` | 两种创建路径、模型与能力选择、前后台管理、权限、通知和续接 |
| `src/jixue/subagents/runner.py` | 消费子任务事件，维护最终状态、报告、工具轨迹、用量及存档 |
| `src/jixue/subagents/store.py` | 安全路径、有界快照、原子写入和恢复校验 |
| `src/jixue/tools/subagent.py` | 唯一 Agent 工具的稳定 Schema 与参数校验 |
| `src/jixue/permission.py` | 权限判断核心：危险命令、路径沙箱、精确安全规则、三种权限模式和 ALLOW/DENY/ASK 结果 |
| `src/jixue/domain/conversation.py` | 唯一工作消息序列，包含文字、工具块和摘要；负责协议转换、大小估算、原子替换及独立用量和轮次计数 |
| `src/jixue/domain/events.py` | Electron 与 Python 之间的一行 JSON 信封 |
| `src/jixue/llm/base.py` | 霁雪自己的 LLM 接口；统一接收 system、messages、tools 并输出流事件 |
| `src/jixue/llm/fake.py` | 离线模拟 LLM；支持工具闭环、权限和摘要测试，工具 ID 在跨任务时保持唯一 |
| `src/jixue/llm/config.py` | 从 `models.yaml` 和环境中生成四字段配置 |
| `src/jixue/llm/adapters/anthropic_client.py` | 唯一接触 Anthropic SDK，翻译流事件；省略空 `tools`，并把供应商超长错误映射为稳定领域错误码 |
| `src/jixue/mcp/client.py` | MCP transport 合同、stdio/HTTP 连接、`.env` URL 占位替换、握手、工具发现、限时调用；MCP SDK 只在这里出现 |
| `src/jixue/mcp/tool.py` | 把 MCP 工具定义和调用结果包装成霁雪统一的 Tool/ToolResult |
| `src/jixue/mcp/__init__.py` | MCP 客户端层公开导入入口 |
| `src/jixue/bridge/bootstrap.py` | 读取项目根目录 `.env`，选择 Fake 或真实 LLM |
| `src/jixue/bridge/application.py` | 命令互斥、会话命令、聊天事件转发和任务开始/结束存盘 |
| `src/jixue/bridge/sessions.py` | 新建/切换/恢复会话并重新组装 Agent；共享工具注册表 |
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
| `src/jixue/tools/memory.py` | read_memory 按需读索引或指定正文、update_memory 四类记忆更新/忘记及 rebuild_index 重建索引；沿用模式和权限链 |
| `src/jixue/tools/__init__.py` | 工具层公开导入入口 |

`agent.py` 是最先阅读的组装图，接着读 `agent_runtime/loop.py` 的 `run()` 看完整任务链；`domain` 不知道 Electron 和 Anthropic；`adapters` 藏住外部 SDK；`bridge` 只负责连接桌面端。

## Electron 桌面端

| 文件 | 职责 |
| --- | --- |
| `apps/desktop/src/main/index.ts` | 创建窗口，校验聊天、取消、任务模式、权限模式和确认回复 IPC |
| `apps/desktop/src/main/bridge-process.ts` | 启动 Conda 子进程，处理 NDJSON，并缓存 Bridge 与每个 MCP Server 的状态 |
| `apps/desktop/src/preload/index.ts` | 只向网页暴露白名单中的聊天、取消、模式、确认和订阅接口 |
| `apps/desktop/src/shared/protocol.ts` | 前后端共用的事件信封、桌面 API、BridgeState 和 MCP 状态类型 |
| `apps/desktop/src/renderer/src/App.tsx` | 聊天页面：会话侧栏与回放、模式、权限、工具确认、完成后折叠和 MCP 状态 |
| `apps/desktop/src/renderer/src/MessageView.tsx` | 单条聊天消息、普通工具卡片及主任务确认按钮 |
| `apps/desktop/src/renderer/src/SubagentView.tsx` | 子任务状态、折叠、按需详情、工具轨迹、独立权限和停止按钮 |
| `apps/desktop/src/renderer/src/state.ts` | reducer：按任务把工具卡片插在回复上方，更新模式、权限、轮次与执行状态；恢复会话时让旧确认失效 |
| `apps/desktop/src/renderer/src/styles.css` | 聊天、工具折叠卡片、权限、MCP 状态和输入区样式；限制长输入与结果的宽高 |
| `apps/desktop/src/renderer/src/main.tsx` | React 页面入口 |

其余 `electron.vite.config.ts`、`tsconfig.json` 和各级 `package.json` 是构建配置，不参与一条消息的业务处理。

## 配置和文档

| 文件 | 职责 |
| --- | --- |
| `README.md` | 项目展示首页：界面截图、核心设计、快速启动与章节导航 |
| `assert/memoryExample.png` | 项目记忆使用与桌面聊天界面截图 |
| `assert/subAgent.png` | 子任务卡片、续接与结果汇总截图 |
| `config/agents/reviewer.md` | 可直接使用的只读审查角色示例；YAML 元信息、Markdown 行为说明 |
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
| `docs/chapters/07-context/README.md` | 第七章统一 messages、三层上下文保护、Claude Code 公开机制对照及启动和测试说明 |
| `docs/chapters/08-memory/README.md` | 会话恢复/切换、项目指令、记忆工具、全链路核心代码和手测 |
| `docs/chapters/09-subagents/README.md` | 子任务全部链路、核心代码、手测、限制与自测题 |
| `scripts/test-all.ps1` | 顺序执行本地自动化检查 |

本地 `tests/mcp/demo_server.py` 和 `demo_http_server.py` 分别模拟 stdio 与 HTTP
Server；对应测试覆盖两条真实闭环。每章目录只允许有一个 `README.md`；这些测试
文件由 `.gitignore` 排除，不会提交。`probe_amap.py` 只诊断高德连接和工具发现，
`probe_amap_agent.py` 用真实模型走一遍“模型 → 高德工具 → 最终回答”；二者都不会
打印 URL 或 Key。

`.jixue/tool-results/` 由程序运行时自动创建。里面保存工具完整大结果，界面和模型只接收预览；该目录不属于源码，也不会提交到 Git。

`.jixue/sessions/<id>.jsonl` 保存界面事件和工作消息快照，`current.txt` 保存当前会话编号。`.jixue/memory/MEMORY.md` 保存新任务直接读取的元数据索引，记忆变更或显式重建时更新，`<name>.md` 保存含 YAML 头的独立记忆正文；它们都属于本地运行数据，不进入 Git。第八章本地专项桌面测试为 `tests/ui/ch08_electron.mjs`。

`.jixue/subagents/<session_id>/<agent_id>.json` 保存可续接子对话、模型与权限边界、报告和工具轨迹；运行中断后不自动重跑。第九章本地专项测试是 `tests/test_subagents.py` 与 `tests/ui/ch09_electron.mjs`；`tests/test_review_subagent_edges.py` 覆盖控制命令通知隔离、新旧中断检查点和连续续接记账。
