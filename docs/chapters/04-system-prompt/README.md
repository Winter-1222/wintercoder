# 第 4 章：System Prompt 与动态提醒

## 当前成果

请求分成 `system`、`messages`、`tools` 三部分。基础规则和项目资料放在 `system`；用户问题、客户端提醒、模型回复和工具结果进入工作消息；工具定义由注册中心导出。

动态提醒是**任务开始时的状态快照**，包含 Plan/Do、权限模式、时间和 Git 变更数量。每个普通用户任务生成一次并追加到 `ConversationManager`；同一任务内的多次模型请求复用它。后续任务保留旧提醒，再追加新提醒。提醒正文说明：后续任务以新提醒为准，执行期间以工具结果为准。

页面只显示原始用户输入及 Agent 事件；持久化分别保存界面回放记录和工作消息快照，因此保存提醒不会把它显示成用户聊天气泡。

## 核心文件

| 文件 | 职责 |
| --- | --- |
| `src/jixue/prompt.py` | 组装 System Prompt，生成任务状态提醒 |
| `src/jixue/agent.py` | 创建 Agent、每个任务刷新 system、分发普通输入和 `/compact` |
| `src/jixue/agent_runtime/loop.py` | 保存用户问题与本次提醒，再进入模型和工具循环 |
| `src/jixue/agent_runtime/compaction.py` | 请求前清理旧工具正文、转换协议和按需摘要 |
| `src/jixue/domain/conversation.py` | 保存工作消息，合并相邻用户文字，按完整任务划定摘要边界 |
| `src/jixue/bridge/sessions.py`、`sessions/store.py` | 恢复工作快照，单独回放原始问题和界面事件 |
| `src/jixue/tools/registry.py` | 导出本地及已连接 MCP 工具的定义 |
| `src/jixue/llm/adapters/anthropic_client.py` | 将三部分请求交给 Anthropic SDK |

## 完整链路

```text
启动 Bridge
  → SessionController 读取当前会话的最新工作快照
  → 用恢复的 ConversationManager 创建 Agent，并生成初始 system
  → 页面请求 session.current：重新加载所选快照，回放界面事件

用户发送消息
  → Bridge 保存 turn_started：原始输入，供界面回放
  → 刷新当前 Agent 的可用工具；普通任务按需附上子任务通知
  → Agent.run 刷新本任务 system
  ├─ /compact：走摘要入口，不追加普通问题或动态提醒
  └─ AgentLoop.run
       → add_user：保存本次问题
       → build_system_reminder：生成一次任务状态快照
       → add_user：将提醒紧接在问题后保存
       → ToolRegistry.to_api_format：得到本任务 tools，Plan 只导出只读工具
       → request_messages：清旧工具正文，转换为 APIMessage，必要时摘要
       → ModelStream.respond：发送 system + messages + tools
       ├─ 完整 tool_use：执行工具，成对保存调用与结果，再次请求模型
       └─ 结束/停止/失败：保存回复和状态
  → Bridge 保存 turn_finished：工作快照 + 界面事件
  → 发送收尾事件，界面恢复输入
```

**历史通常在用户发问前已恢复。** 后续消息复用当前内存中的 `ConversationManager`，不在每次请求时重新读整份会话文件。重启或切换会话才重新加载；正常结束或停止后保存的快照包含提醒。进程被强制结束时，只能恢复最后一次已保存的快照。

System Prompt 由七段基础规则（角色、行为、工具使用、代码质量、安全、任务模式、输出风格）、工作目录和操作系统、根目录 `AGENTS.md`、记忆规则与索引、技能目录组成；主会话还附上子角色目录。创建 Agent 时生成初始值，每个新任务开始时刷新项目资料；同一任务的工具循环不再重建。资料未变时，重新组装的字符串保持相同。

MCP 工具通过后台连接、握手和 `list_tools` 发现，经 `MCPToolWrapper` 包装后注册到共享 `ToolRegistry`。下一次 `chat.send` 的 `refresh_tools()` 将可用工具加入当前 Agent，再统一导出名称、描述和参数 Schema，成为请求的 `tools` 数组。尚未连接成功的工具不会凭配置文件自动进入数组。

### 提醒怎样保存和发送

```text
工作历史：user(问题 A), user(提醒 R1), assistant(回答 A),
          user(问题 B), user(提醒 R2)

API 消息：user(问题 A + 两个换行 + 提醒 R1), assistant(回答 A),
          user(问题 B + 两个换行 + 提醒 R2)

界面回放：问题 A、回答 A、问题 B
```

`to_api_format()` 合并相邻同角色的纯文字，原始问题仍是独立的内部消息。问题和紧邻的提醒共同属于一个任务；提醒不增加任务计数，也不会单独成为摘要边界。

以前 R1 只临时拼进请求副本，下一条任务重新转换历史时 R1 消失，已经发过的前缀随之改变。现在旧提醒随历史保留，请求整理函数不再注入提醒，工具循环、摘要后重试也不会重复追加它。

这仍然不是“messages 永远不变”：旧工具正文清理和对话摘要会主动改写工作历史；项目指令、技能目录或工具集合变更也可能改变请求前缀。这里解决的是**跨任务丢失旧提醒**，没有测量或保证供应商实际缓存命中率。

## 启动与测试

离线手测先在本地 `.env` 中设置 `JIXUE_LLM_MODE=fake`，再启动：

```powershell
conda activate mycoder
npm run dev
```

1. 发送 `/loop README.md docs/PROJECT_STRUCTURE.md`，确认完成两次读取、三轮模型请求。
2. 接着发送“继续”，确认任务轮号只增加一次，用户气泡只显示原话。
3. 输出时点击停止，等“已停止”后重启应用，打开原会话并发送“继续”。确认历史、工具卡片可恢复，输入正常。
4. 切换 Plan/Do 后发送新消息，确认模式和工具限制仍然生效。

Fake 用于验证链路，不理解文件内容；判断模型是否正确采用最新环境需使用真实模型。实际 API 消息的逐项前缀比较由本地回归完成：

```powershell
conda run --no-capture-output -n mycoder python -m pytest tests/bridge/test_reminder_history.py
conda run --no-capture-output -n mycoder python -m pytest
conda run --no-capture-output -n mycoder ruff check src
conda run --no-capture-output -n mycoder mypy src
```

测试覆盖多次工具请求与下一任务的前缀、停止/截断/失败后恢复、UI 原话回放、摘要保留最近提醒，以及旧存档兼容。测试只保留在 Git 忽略的 `tests/` 中。

## 常见问题

**为什么暂时仍然每个任务生成一次？** 用最少状态保证新任务有当前模式、权限、时间和 Git 快照。本步先修复已发送提醒丢失；只在状态变化时追加需要另行设计变化判断和时间更新频率。

**旧提醒会不会覆盖当前状态？** 它明确标为历史快照，新任务读最新提醒，任务执行中读工具结果。真正权限仍由执行器校验；提醒不能授予工具能力或替代权限判断。

**旧存档没有提醒怎么办？** 原样恢复，从下一个普通任务开始追加。不能用当前时间和 Git 状态给过去的任务补造快照；无需修改存档格式。

**摘要时提醒怎么办？** 旧任务提醒随旧前缀成为摘要资料，最近两轮和当前任务的提醒保留。`/compact` 命令自身不创建新任务提醒。

**`<system-reminder>` 是独立的 API 角色吗？** 不是，只是 `messages` 中的文字标签。外部文件、网页或工具结果自带这个标签，也不能升级成系统指令。

**为什么 Git 状态只显示数量？** 避免把恶意文件名包装成客户端提醒。Git 子进程使用 `DEVNULL` 隔离 stdin，避免争抢 Bridge 的输入管道。

## 变更记录

- 建立七段 System Prompt、LLM 合同与 SDK 接线；隔离动态 Git 查询的 stdin。
- 接入每个任务的项目指令、记忆索引、技能和子角色目录刷新。
- 动态提醒改为任务开始时追加到工作历史；移除请求副本临时注入，支持正常停止和重启恢复；保持界面原始输入与旧存档兼容。

## 自测题与答案

1. **恢复历史和生成 system 谁先？** 会话控制器先恢复工作快照，再创建 Agent 生成初始 system；新问题到来时再刷新本任务 system。
2. **为什么把时间放在消息尾部？** 避免每次时间变化都改动靠前的 system，旧任务快照也能保持原文。
3. **保存提醒后，页面为什么不会显示它？** UI 使用原始输入和 Agent 事件回放，不直接遍历工作消息。
4. **一个任务请求三次模型，提醒生成几次？** 一次。工具调用和结果追加后，再从同一工作历史生成请求。
5. **提醒会被算作一个额外任务吗？** 不会。连续的原问题和提醒属于同一轮，终态 assistant 才结束该轮。
6. **前缀稳定是否等于缓存必然命中？** 不等于。还取决于其他请求字段、历史清理、供应商策略和缓存门槛。
