# 第八章：会话与项目记忆

## 当前成果

- [x] 1. JSONL 会话保存与恢复：工作消息快照、界面事件、累计用量和任务数。
- [x] 2. 桌面新建、列出、切换会话；任务运行和恢复期间禁止切换。
- [x] 3. 项目根目录 `AGENTS.md` 注入系统提示，每个新用户任务刷新。
- [x] 4. Markdown 项目记忆：读取、记住、忘记；模型通过统一工具权限链更新。

参考 Claude Code 公开的[会话机制](https://code.claude.com/docs/en/how-claude-code-works#work-with-sessions)与[项目指令和自动记忆](https://code.claude.com/docs/en/memory)。霁雪简化为项目根目录一份指令、一份记忆，以及每个会话一个 JSONL。这里使用 `AGENTS.md`；Claude Code 原生读取 `CLAUDE.md`，两者文件名并不相同。暂不实现目录层级继承、主题记忆文件、后台记忆 Agent 或第九章 Subagents。

| 数据 | 保存位置 | 用途 |
| --- | --- | --- |
| 工作消息 | `ConversationManager._messages` | 模型唯一的对话序列，三层压缩直接修改它 |
| 会话存档 | `.jixue/sessions/<id>.jsonl` | 界面回放 + 每轮结束后的工作消息快照 |
| 当前会话指针 | `.jixue/sessions/current.txt` | 下次启动选中哪个会话 |
| 项目指令 | 根目录 `AGENTS.md` | 用户维护的项目约定，可纳入 Git |
| 项目记忆 | `.jixue/memory/MEMORY.md` | 跨会话使用的稳定事实和偏好，不纳入 Git |
| 工具大结果 | `.jixue/tool-results/` | `read_artifact` 按编号取回原文，恢复会话后仍需要 |

JSONL 是磁盘存档，不是第二份参与推理的 history/active。界面记录回放结束后不会被加入模型消息。

## 核心文件

| 文件 | 职责 |
| --- | --- |
| `src/jixue/sessions/store.py` | 追加 JSONL、修复末尾半条记录、恢复、列举和文字分片合并 |
| `src/jixue/sessions/codec.py` | 序列化/校验消息、工具块、摘要标志、用量和累计任务数 |
| `src/jixue/bridge/sessions.py` | 切换会话时组装新的 Agent，共享 Registry 与 MCP 连接 |
| `src/jixue/bridge/application.py` | 会话命令、运行互斥、任务开始记录与结束落盘 |
| `src/jixue/project_context.py`、`prompt.py` | 读取项目指令，拼装基础规则、项目约定和记忆 |
| `src/jixue/memory.py`、`tools/memory.py` | Markdown 记忆存储，read_memory/update_memory 工具 |
| `src/jixue/agent.py` | 保留组装入口；在任务开始时刷新系统提示 |
| `apps/desktop/src/renderer/src/App.tsx`、`state.ts` | 会话侧栏、事件回放、状态恢复和旧权限按钮失效 |

## 完整链路与核心代码

### 1. 启动：恢复消息，然后组装 Agent

```text
npm run dev
  → Electron Main 启动 Conda mycoder 中的 Python Bridge
  → 注册本地工具（含记忆工具），后台连接 MCP
  → SessionController → SessionStore.current()
  → current.txt → 对应 JSONL → 最后一份 snapshot
  → ConversationManager(messages, total_usage, completed_turns)
  → Agent 组装 ModelStream / ToolExecutor / ContextCompactor / AgentLoop
  → Renderer 请求 session.current → 回放界面 → session.loaded
```

`SessionController._activate()` 的组装核心：

```python
self.conversation = restored.conversation
self.agent = Agent(
    self._llm,
    conversation=self.conversation,
    tools=self._tools,
    tool_context=ToolContext(self._root),
)
```

每次新建或切换都换掉会话消息和运行控制，复用工具注册表。已发现的 MCP 工具仍在这个注册表里，无需再次握手。新建/恢复采用默认 Do 与“修改需确认”，不恢复旧任务的一次性授权。

### 2. 用户消息：刷新项目上下文，再进入原有循环

`Agent.run()` 中的新增入口：

```python
self._model.set_system_prompt(
    await asyncio.to_thread(build_system_prompt, self._project_root)
)
stream = self._compactor.run_manual() if text == "/compact" else self._loop.run(text)
async for item in stream:
    yield item
```

系统提示由基础规则、环境、根目录 AGENTS.md、记忆使用规则和 MEMORY.md 组成。指令读取上限 20000 字符，超出部分截断并注明；记忆文件最多 16000 字符。二者独立于 messages，因此对话摘要不会删掉它们；它们仍消耗模型上下文额度。

同一任务的工具循环内保持系统提示不变，便于缓存前缀。文件变化在下一个用户任务生效；记忆工具的本次结果可以直接用于当前任务。

```text
chat.send → turn_started 落盘 → Agent.run
  → 构建 system + 当前模式/时间/Git 动态提醒
  → 请求消息整理与三层上下文保护 → ModelStream → LLM
  → tool_use → 参数校验 → Plan/Do → 权限检查/确认
  → 分批并发执行 → ToolResultStore 大结果保护
  → 成对 add_tool_round → 再请求 LLM → 最终回答
  → turn_finished 落盘 → loop_complete → 界面可开始下一任务
```

连续未知工具保护、只读工具分批并发、取消和 MCP 适配仍在原来的执行链中。记忆更新工具默认串行，不会和其他写入被当作安全读取一起并发。

### 3. 结束：界面记录与工作快照一起存档

JSONL 一行一个完整 JSON，结构示意：

```json
{"type":"session","version":1,"created_at":"..."}
{"type":"turn_started","request_id":"req_1","text":"读取项目","timestamp":"..."}
{"type":"turn_finished","snapshot":{"messages":[],"usage":{},"completed_turns":1},"events":[]}
```

上面空列表只是结构示意。真实 `finish_turn()` 保存的是当前完整快照及该任务的 UI 事件：

```python
self.append(
    session_id,
    {
        "type": "turn_finished",
        "snapshot": encode_conversation(conversation),
        "events": [json.loads(event.to_json_line()) for event in events],
    },
)
```

例如前六个任务被摘要：保存后的快照是“摘要 + 近期消息”，旧用户文字和工具卡片仍在之前的界面事件中。重启时只解码最后一份快照给模型，并独立回放全部 UI 事件。累计 Token 和完成任务数单独恢复，不通过摘要剩余条数反推。

任务开始记录先落盘，失败就不执行模型。任务结束快照经过 flush/fsync 后才发送收尾事件。崩溃留下的末尾半条记录会被忽略，并在后续追加前截掉；文件中已完成但损坏的 JSON 会报错。进程在任务中途退出时，界面显示该任务中断，模型恢复上一个完整快照，不自动重跑可能已经产生副作用的工具。

### 4. 记忆：模型提出更新，执行器负责权限与落盘

真实模型可根据“记住这个约定”或已经确认的稳定偏好主动请求：

```json
{"name":"update_memory","input":{"action":"remember","key":"python_env","content":"Python 使用 Conda mycoder 环境"}}
```

工具定义的关键部分：

```python
BaseTool(
    tool_name="update_memory",
    # 描述和 Schema 见源码
    handler=_update,
    validator=_validate,
    destructive=True,
    tool_category="memory",
)
```

未指定 `read_only` 和 `concurrency_check`，沿用 BaseTool 的“可写、串行”默认值。Plan 隐藏并拒绝可写工具；Do 下由当前权限模式决定询问或允许，拒绝不会改动文件。

存储的核心更新：

```python
if action == "remember":
    entries[key] = content
elif action == "forget":
    del entries[key]

# 完整校验和大小检查后，临时文件替换正式文件。
temporary.write_text(text, encoding="utf-8")
temporary.replace(path)
```

实际源码还处理不存在的 key。相同 key 覆盖旧值，避免重复；每条最多 1000 字符。文件可手工编辑，但需要保留格式：

```markdown
# 项目记忆

- **python_env**: Python 使用 Conda mycoder 环境
```

“自动记忆”的简化含义是模型在任务里主动调用同一个工具；没有额外后台模型调用，也没有绕过权限的隐式写盘。是否主动提取、提取质量仍取决于真实模型；Fake 只提供确定性命令验证执行链路。

## 启动与手动测试

```powershell
npm run dev
```

以下斜杠命令由 FakeLLM 提供；真实模型使用自然语言。需要离线验证时，将本地 `.env` 的 `JIXUE_LLM_MODE` 设为 `fake` 并重启。

1. 会话 A 发送 `/read README.md`，完成后关闭应用再启动。预期文字、工具卡片和 Token 恢复。
2. 点击“新建会话”，预期空对话；在侧栏切回 A，预期记录恢复。运行中按钮禁用。
3. Do +“修改需确认”发送 `/remember python_env 使用 mycoder`，允许后检查记忆文件。另发 `/remember denied 不应保存` 并拒绝，预期没有 denied 条目。
4. 新建会话 B，发送 `/memory`，预期能读取 python_env；在 Plan 模式发送 `/remember blocked 不应写入`，预期不写入。
5. 切回 Do，发送 `/forget python_env` 并允许；下一任务 `/memory` 应不含该条目。
6. 真实模型下修改 AGENTS.md 的回答约定，再发送新任务，检查是否遵循。完整请求注入已由记录请求的本地替身测试验证。
7. 至少进行四次对话，第一条包含足够长的资料，再输入 `/compact`。重启后界面保留旧消息，后端快照只含摘要与近期消息。

本地自动化：

```powershell
conda run --no-capture-output -n mycoder python -m pytest
conda run --no-capture-output -n mycoder ruff check .
conda run --no-capture-output -n mycoder mypy src
npm run typecheck
npm run test:frontend
npm run test:electron
node tests/ui/ch08_electron.mjs
```

专项 Electron 测试需要先 `npm run build`。所有自动化测试留在本机并被 Git 忽略；临时项目固定 FakeLLM，不读取项目真实密钥。已验证 JSONL 恢复/半条尾部、摘要后恢复、会话隔离、运行互斥、取消、存盘失败、指令刷新与长度限制、记忆权限/覆盖/忘记/边界，以及真实桌面关闭重启。

本次验证结果：152 项 Python 测试、8 项前端状态测试、mypy（45 个源码文件）、ruff、TypeScript、构建、原有 Electron 冒烟与第八章专项 Electron 测试全部通过。真实模型的记忆提取质量未在本次测试中评估。

## 常见问题

- **还需要 tool-results 吗？** 需要。恢复后的工具结果可能只有预览与编号，原文仍靠 read_artifact 读取。不要删除正在使用的会话引用的大结果。
- **忘记会删除以前的聊天吗？** 只删除长期记忆条目，不修改既有会话存档和聊天原文。下一次系统提示不会再注入该条目。
- **崩溃会保留最后一个字吗？** 当前按完整任务提交；中途文字和工具卡片不保证恢复，真实文件副作用也不会回滚。恢复界面会标记中断。
- **文件损坏怎么办？** 完整但损坏的记录报错并保留原文件；手工备份修复后重启。只有末尾未写完的记录会被自动忽略。记忆格式不合法时不会覆盖原文。
- **多开应用能同时写一个项目吗？** 本章面向单个 Bridge 进程；不支持多个进程同时写同一项目会话/记忆。
- **JSONL 会一直增长吗？** 会。当前每轮保存完整工作快照，界面事件也追加保留；归档清理和大规模分页留待后续。
- **三层压缩后仍超限？** 按约定留待后续，本章不扩展极端超限兜底。

## 变更记录

- 完成 JSONL 会话和桌面新建/恢复/切换，持久化与 Agent 执行分开。
- 项目 AGENTS.md 与 Markdown 记忆按用户任务刷新，记忆工具复用权限链。
- 专项桌面测试修正恢复时“累计任务数”与“本任务模型轮数”的混用。
- 收窄旧数据目录忽略规则，避免将新的 sessions 源码误排除；运行数据继续忽略。

## 自测题与答案

1. **为什么存档里有 events 和 snapshot？** events 恢复用户看到的内容，snapshot 恢复模型当前真正使用的消息；两者不会合并成双份模型上下文。
2. **为什么累计 Token 不能从恢复后的 messages 重新相加？** 摘要和正文清理会移除旧消息，但历史账单不能消失。
3. **新会话为什么还能知道 Python 环境？** 会话消息独立；项目 MEMORY.md 会作为系统上下文注入每个新任务。
4. **模型主动记忆会不会绕过确认？** 不会。它仍请求 update_memory，随后走 Plan/Do、权限检查、确认和执行器。
5. **为何在下个用户任务刷新系统提示？** 保持一次工具循环中的前缀稳定，同时让下一任务使用最新项目约定和记忆。
6. **重启为什么不能直接重跑最后一个工具？** 退出前工具可能已经改过文件；无法确认副作用，自动重跑可能重复修改。
