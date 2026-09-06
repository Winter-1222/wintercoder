# 第九章：子 Agent

## 当前成果

主模型通过唯一的 `Agent` 工具委派任务；同一执行器支持定义式、Fork、前台等待、后台只读、查询、停止和续接。子任务沿用现有 Agent Loop、权限和三层上下文保护。这里对齐 Claude Code 的核心思路，保留适合教学项目的范围。

| 项目 | 定义式 | Fork 式 |
| --- | --- | --- |
| 选择方式 | `subagent_type: "explore"` 等角色名 | 省略 `subagent_type` |
| 初始消息 | 空白对话，仅注入当前任务 | 父模型最近一次请求的消息快照，再追加子任务 |
| system | 角色正文、子任务规则、项目指令与记忆索引 | 原样复制父请求 system |
| 工具定义 | 角色工具与父任务可用工具的交集 | 原样复制父请求的工具定义和顺序 |
| 模型 | `inherit` 或 models.yaml 中的别名 | 继承父模型 |
| 执行边界 | 禁止再次委派和调用 update_memory | 相同，保留 Agent Schema 也无法执行它 |

**省略类型表示 Fork 是本项目的约定。** [Claude Code 子 Agent 文档](https://code.claude.com/docs/en/sub-agents)提供角色、工具限制和隔离上下文的公开说明；这里的 action 字段、存档格式、限额和后台只读策略属于本项目设计，不声称复刻其内部实现。

## 核心文件

- `src/jixue/agent.py`：组装运行组件，允许提供固定 system、工具定义和禁止工具集合。
- `src/jixue/tools/subagent.py`：稳定的 Agent Schema 与参数校验。
- `src/jixue/subagents/definitions.py`：内置 explore/general，读取项目 Markdown 角色。
- `src/jixue/subagents/manager.py`：选择创建方式、隔离状态、管理前后台、权限、通知和续接。
- `src/jixue/subagents/runner.py`：消费子 Agent 事件，维护工具轨迹、状态、用量和存档。
- `src/jixue/subagents/store.py`：每任务一个 JSON 快照，校验后恢复，原子替换写入。
- `src/jixue/bridge/sessions.py`：为每个父会话绑定 Agent 工具；会话共享一个子任务管理器。
- `apps/desktop/src/renderer/src/SubagentView.tsx`：子任务卡片、独立权限、停止和按需加载详情。

## 完整链路与核心代码

### 1. 主模型决定委派

```text
用户消息 → BridgeApplication → 父 Agent.run
  → 组装 system + 角色目录，发送稳定工具 Schema
  → LLM 返回 Agent 的 tool_use
  → ToolExecutor 校验参数和父权限
  → SubagentManager.handle → _start
```

创建与管理都通过一个工具；增加角色只更新提示词中的角色目录，Schema 不增加角色枚举：

```json
{"action":"run","subagent_type":"explore","prompt":"查清登录请求经过哪些函数"}
{"action":"run","prompt":"结合当前调查，检查权限遗漏","background":true}
{"action":"status","agent_id":"sub_实际编号"}
{"action":"wait","agent_id":"sub_实际编号"}
{"action":"stop","agent_id":"sub_实际编号"}
{"action":"run","agent_id":"sub_实际编号","prompt":"继续检查异常分支"}
```

`status` 不等待；`wait` 等结束；续接复用子对话，不重新选择角色或扩大原权限。前台调用会等待结果返回父模型；后台先返回编号，父模型可以继续。

### 2. 选择初始上下文，再组装同一种 Agent

父模型请求发送前，ModelStream 记录已经清理、附加动态提醒后的实际输入：

```python
self.last_request = RequestSnapshot(
    self._system_prompt, deepcopy(tuple(messages)), deepcopy(tuple(tools))
)
```

Fork 从这个快照建立独立消息序列，并从零计算子任务费用：

```python
snapshot = parent.request_snapshot
conversation = ConversationManager(
    [Message(m.role, deepcopy(m.content)) for m in snapshot.messages],
    total_usage=Usage(), completed_turns=0,
)
```

快照截止于父请求输入，**不包含父模型刚返回、尚未配对结果的 Agent 调用**。已经压缩的父历史复制的是摘要和保留原文，不会从 JSONL 恢复全部旧对话。Fork 禁用相邻文字消息合并；附加子任务后，父消息前缀仍然一致。

定义式先读取 YAML 元信息与 Markdown 正文，再创建空 ConversationManager。两种路径最终都创建 Agent：

```python
run.agent = Agent(
    self._model(info["model_id"]), conversation=run.conversation,
    tools=registry, tool_context=ToolContext(self.root),
    system_prompt=info["system"], reminder_override="",
    tool_definitions=info["tools"], max_iterations=info["max_turns"],
    blocked_tools=frozenset({"Agent", "update_memory"}),
    preserve_message_boundary=info["kind"] == "fork",
)
```

新任务消息附加子工作者规则：只做指定任务，不主动闲聊或要求用户补充；缺信息就报告未完成事项。报告要求“结论、依据、未完成事项”，目标不超过 1500 字。**权限检查仍会请求确认，提示词不能替用户批准。**

缓存只是一种可能的收益。相同模型、system、tools、messages 前缀为命中创造条件；TTL、供应商实现、上下文摘要等会影响实际命中。本章测试验证请求前缀一致，没有用真实 API 宣称命中率。[官方缓存说明](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)

### 3. 子任务跑自己的工具循环

```text
drive_subagent → 子 Agent.run → AgentLoop
  → LLM → 子 ToolExecutor → 权限检查/等待确认
  → Registry → 本地工具或共享 MCP 包装器
  → 工具结果 + 调用成对写回子 conversation → 再请求模型
  → 正常 end_turn 且最终正文非空 → completed
```

只复用执行代码，运行状态分别创建：conversation、RunControl、权限 waiter、压缩失败计数、流响应、累计用量。LLM 客户端、无会话状态的工具实现和 MCP 连接共享。当前没有独立的文件读取缓存，不为子 Agent 新增一套缓存抽象。

子执行器先检查禁止集合，所以即使模型在 Fork 中看到 Agent Schema，调用仍会失败。工具经过父模式与角色工具范围筛选，子权限只会继承或收紧；后台强制 Plan。前台 General 修改仍走现有确认链。

每个子任务单独使用三层压缩：大结果落盘 → 旧工具正文清理 → 对话摘要。只改子消息，不改父消息；大结果共用已有 `.jixue/tool-results/` 存储设施。父消息只接收最终报告或任务编号，子任务内部工具轮不会全部塞回父上下文。

### 4. 报告、事件与用量分别返回

子 runner 消费原始事件，只向桌面发 `subagent.updated`；子 `loop_complete` 不直接转发为父完成事件。权限用 `session_id + agent_id + 本次 token` 定位，确认一次即失效。

无工具调用并不总意味着成功：必须正常结束且有正文。异常、超时、取消、轮数耗尽分别返回失败或未完成状态。程序生成状态；最终报告只取最后一次回答文字，不拿父历史旧回复冒充报告。

```python
# 前台或 wait 等待同一个异步任务。
await asyncio.shield(run.task)
result = {key: run.data[key] for key in ("agent_id", "status", "report", "usage")}
```

后台完成后立即更新卡片；下一次主聊天请求附上完成通知，按原会话归属消费。主模型也可主动调用 status/wait 获取结果。通知作为补充资料进入 messages，不视为用户授权。后台完成不会偷偷启动一轮新的主模型请求。`/compact` 与空输入不消费后台通知；通知留到下一次有效的普通聊天。

检查点里的会话账本与任务用量采用同一累计值；运行中仅更新存档副本，避免 Loop 收尾再次记费。恢复旧检查点时补齐内外账本差额，连续续接只累计新增费用。父、子账单分别保存；桌面显示“父会话累计 + 每个子任务累计”。更新快照采用覆盖，续接沿用子累计，不重复相加，也不把父历史原费用算进 Fork。

### 5. 存档和续接

```text
.jixue/subagents/<session_id>/<agent_id>.json
  身份/角色/模型/工具/权限边界
  子 conversation + usage
  状态/报告/工具轨迹/通知标记
```

运行中保存已完整配对的消息；结束再保存最终快照。重启发现 running，改为 interrupted，等待明确续接，不自动重跑操作。续接使用原模型配置和原子对话，另建 Agent 与权限 waiter；历史授权不复用。模型配置已变化时拒绝续接，要求新建任务。

界面恢复只加载紧凑任务摘要，展开后再请求报告与最近工具轨迹。存档保留最近 80 条工具记录，详情显示最近 20 条，正文按显示长度截断；完整最终报告可用 Agent status 查询。所有运行数据由 Git 忽略。

## 启动与测试

在项目根目录运行 `npm run dev`。离线验证时 `.env` 使用 `JIXUE_LLM_MODE=fake`；Fake 不理解自然语言委派，使用以下确定性入口：

1. 定义式：`/agent {"action":"run","subagent_type":"explore","prompt":"/read README.md"}`。
2. Fork：`/agent {"action":"run","prompt":"/read README.md"}`；展开卡片查看读取结果。
3. 权限：`/agent {"action":"run","subagent_type":"general","prompt":"/write ch09-demo.txt 子任务测试"}`；尝试拒绝，再发送一次允许。
4. 后台：权限模式选“每次都询问”，发送 `/agent {"action":"run","background":true,"prompt":"/read README.md"}`；先允许父 Agent 调用，主回复结束后可确认或停止子任务。
5. 复制卡片里的实际编号，关闭重开桌面端，再发送 `/agent {"action":"run","agent_id":"复制的编号","prompt":"/read README.md"}`；确认任务编号不变且旧确认按钮不复活。

真实模型沿用已有 `.env` 配置，然后说：“用 explore 子任务查清主 Agent 的工具调用链，给我文件依据。”确认出现 Agent 工具与子任务卡片，工具结果返回后父模型进行汇总。独立模型在角色 YAML 中把 `model: inherit` 改为 `config/models.yaml` 里的别名；不要写 Key。

```powershell
conda run --no-capture-output -n mycoder pytest
conda run --no-capture-output -n mycoder ruff check src
conda run --no-capture-output -n mycoder mypy src
npm run typecheck
npm run test:frontend
npm run build
node tests/ui/ch09_electron.mjs
```

本次验证：206 项 Python 测试、11 项前端测试通过；Ruff、mypy、TypeScript 和桌面构建通过；第八章与第九章 Electron 专项通过。未调用真实模型 API。测试保留在本机并由 Git 忽略。专项覆盖两种创建、请求前缀、子权限、嵌套禁用、后台只读、并发限额、取消/超时、状态查询/等待、模型选择、MCP 包装器复用、压缩隔离、重启续接和用量。

## 常见问题与范围

- **可以同时改多个分支吗？** 本章共用项目目录，前台修改串行，后台只读；没有 worktree、多写者合并、Agent Teams。
- **一直跑怎么办？** 全进程最多 3 个活动子任务；一个父请求最多启动/续接 3 次；子任务默认最多 8 次 LLM 请求，角色可设 1—16；单次运行限时 120 秒。超限明确返回未完成。
- **为什么子任务失败后父回复仍正常？** 失败作为工具结果返回，父模型负责解释或改换方案；父循环正常结束不代表子任务完成目标。
- **恢复后能跨会话续接吗？** 不能，编号属于原会话；切回原会话再继续。每会话最多 100 个任务，单快照最多 16 MiB。
- **Fork 一定命中缓存吗？** 不保证；新增任务触发摘要时也会改变前缀。缓存度量与三层压缩后的极端超限兜底仍留待后续。
- **自动记忆提取做了吗？** 本章没有追加；子 Agent 不能调用 update_memory，主 Agent 仍按第八章机制维护项目记忆。
- **报告一定严格三段且 1500 字内吗？** 当前是模型输出约定，程序严格控制的是状态、轮数、权限和返回长度；没有再加一次总结模型。

## 变更记录

Review 修复：控制命令分发保留 `/compact` 原文，不误消费后台通知；统一子任务检查点用量并兼容旧检查点，避免中断续接漏记。回归覆盖命令前后通知消费、运行中账本不重复结算与连续两次续接。

本章基于第八章后的工具卡片修复提交 `a0ffb54` 开始。分步接通角色与稳定入口、请求快照与隔离运行、前后台生命周期与存档、桌面卡片与确认、自动化回归。第九章源码、角色配置和文档按独立提交维护；本地测试与运行数据不进入提交。

## 自测题与答案

1. **为什么不能直接复制整个父 Agent 对象？** 会共享取消状态、权限 waiter 和工作消息；只复制请求资料，重新组装运行组件。
2. **为何 Fork 复制最近请求而不是当前所有消息？** 最近请求已经完成压缩与提示词组装，且不会包含本次未配对的委派调用，更容易保持缓存前缀。
3. **后台如何让主模型知道完成？** 立即更新 UI，下一次主请求注入所属会话的完成资料；也支持主动 status/wait。
4. **复用了 MCP 会不会共享子对话？** 共享的是连接和工具包装器；消息、权限与调用编号由各自执行器提供。
5. **子任务“没再调工具”就成功吗？** 还要正常 end_turn 且有最终正文；超时、取消和轮数耗尽不能算成功。
