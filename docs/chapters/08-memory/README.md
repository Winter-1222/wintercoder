# 第八章：会话与项目记忆

## 当前成果与提交边界

- 基础版本已提交：`7fd1768`，包含会话 JSONL、桌面新建/切换/恢复、项目指令和单文件记忆。
- 四类记忆版本已提交：`27da642`，包含独立 Markdown 文件、程序生成索引和按需读取正文。
- 本次仅完善索引维护：新任务直接读 MEMORY.md；写入时更新索引，手工编辑后显式重建。作为第八章独立完善提交，与第九章保持边界。
- 主动保存仍由主模型调用工具完成；后台提取、小模型检索和完整记忆老化策略尚未实现。

| 数据 | 保存位置 | 用途 |
| --- | --- | --- |
| 工作消息 | `ConversationManager._messages` | 模型唯一的对话序列，三层压缩直接修改它 |
| 会话存档 | `.jixue/sessions/<id>.jsonl` | 界面事件 + 每轮结束后的工作消息快照 |
| 当前会话 | `.jixue/sessions/current.txt` | 下次启动恢复哪个会话 |
| 静态指令 | 根目录 `AGENTS.md` | 用户维护的项目约定 |
| 动态记忆索引 | `.jixue/memory/MEMORY.md` | 新任务直接加载的元数据与正文链接清单 |
| 动态记忆正文 | `.jixue/memory/<name>.md` | 每条独立记忆的 YAML 头和详细内容 |
| 工具大结果 | `.jixue/tool-results/` | `read_artifact` 按编号取回工具完整结果 |

会话存档解决“继续上次对话”，动态记忆解决“新会话使用过去确认的信息”。它们不合并成两套模型历史。所有 `.jixue/` 数据和本地测试由 Git 忽略。

## 核心文件

| 文件 | 职责 |
| --- | --- |
| `src/jixue/agent.py` | Agent 组装与任务分发；每个用户任务开始时刷新系统提示 |
| `src/jixue/prompt.py`、`project_context.py` | 基础规则、根目录 AGENTS.md、动态记忆索引与使用规则 |
| `src/jixue/memory_format.py` | 四种类型、元数据合同、YAML 解析、大小校验、正文和索引格式 |
| `src/jixue/memory.py` | 直接读索引、按名读正文、同名更新/忘记、写后更新索引和显式重建 |
| `src/jixue/tools/memory.py` | read_memory/update_memory 的 Schema、校验与执行，包含 rebuild_index 操作 |
| `src/jixue/sessions/codec.py`、`store.py` | 快照编解码、JSONL 追加、末尾半条记录处理和恢复 |
| `src/jixue/bridge/sessions.py`、`application.py` | 会话切换组装、互斥、命令转发和任务开始/结束存盘 |
| `src/jixue/llm/fake.py` | 用固定命令离线验证记忆工具链 |
| `apps/desktop/src/renderer/src/App.tsx`、`state.ts` | 侧栏、界面回放、权限按钮和状态恢复 |

## 静态层与动态层

静态层是用户维护的 AGENTS.md，每个任务读取，最多 20000 字符。同一任务的工具循环内系统提示不变。它是行为上下文；真正的操作权限由程序检查。

动态层只允许四种类型。类型表示内容类别，当前存储范围统一是当前项目，不代表已实现跨项目用户画像。

| 类型 | 内容 | 例子 |
| --- | --- | --- |
| user | 用户明确提供的背景 | 正在学习 Agent，希望理解核心代码 |
| feedback | 用户纠正或确认的工作方式 | 切换章节前先检查提交边界 |
| project | 跨会话有用的阶段、期限与决策 | 优先搭建主 Agent，复杂超限兜底延后 |
| reference | 外部信息位置和查阅时机 | 接口规范在哪里，什么时候需要查 |

`MEMORY.md` 只记录入口，例如：

```markdown
# 记忆索引

- [feedback_commit_boundary](feedback_commit_boundary.md) · feedback：章节切换前的提交约定（更新于 2026-09-06T00:00:00+00:00）
```

`feedback_commit_boundary.md` 才保存具体记忆：

```markdown
---
name: feedback_commit_boundary
description: 章节切换前的提交约定
type: feedback
updated_at: '2026-09-06T00:00:00+00:00'
---

进入下一章前，先检查上一章是否还有未提交改动，处理好提交边界。

原因：用户按章节学习，需要能单独查看每一章的代码变化。
```

这些文件名只是示例，不是四个固定的分类索引；同类型可以有多条记忆。描述用于判断是否值得读取，不能代替正文。

## 完整链路与核心代码

### 1. 启动与恢复

```text
npm run dev → Electron → Conda mycoder 中的 Python Bridge
  → 注册本地工具，后台连接 MCP
  → SessionStore.current → current.txt → 对应 JSONL 的最新 snapshot
  → ConversationManager(messages, total_usage, completed_turns)
  → SessionController 组装 Agent
  → Renderer 请求 session.current → 独立回放 UI events
```

新建/切换会话重新组装 Agent，共享工具注册表和 MCP 连接，工作消息相互独立。恢复默认 Do 与“修改需确认”，旧任务的一次性授权不会恢复。

### 2. 新任务先加载静态指令与动态索引

`Agent.run()` 的入口：

```python
self._model.set_system_prompt(
    await asyncio.to_thread(build_system_prompt, self._project_root)
)
stream = self._compactor.run_manual() if text == "/compact" else self._loop.run(text)
```

`build_system_prompt()` 把 `read_project_instructions(root)` 和 `read_memory_context(root)` 放入各自的上下文段。后者只返回索引：

```python
return MemoryStore(project_root).index()
```

索引直接来自磁盘 MEMORY.md，关键读取代码如下：

```python
with path.open(encoding="utf-8-sig") as stream:
    index = stream.read(MAX_INDEX_CHARACTERS + 1)
validate_index(index)
return index
```

`validate_index()` 只检查这段文本的大小和格式，不打开独立记忆文件。整个读取路径不扫描文件头、不写盘。同一用户任务内的工具循环仍复用这份 system prompt；下个用户任务重新读磁盘索引。

首次使用、记忆目录还不存在时返回“尚无项目记忆”，不创建文件。目录已存在但 MEMORY.md 缺失，或索引损坏时，system prompt 中显示不可用提示，要求显式重建；不会把异常当成“没有记忆”。

### 3. 模型决定读哪条正文

模型看到“章节切换前的提交约定”与当前任务相关，于是请求：

```json
{"name":"read_memory","input":{"name":"feedback_commit_boundary"}}
```

工具执行函数区分两种读取：

```python
if "name" in tool_input:
    return ToolResult(await asyncio.to_thread(store.read, str(tool_input["name"])))
return ToolResult(await asyncio.to_thread(store.index))
```

不传 name 只返回索引；传 name 才读取指定文件的头部与正文。正文最多 4000 字符，保持段落结构。名称不接受目录、扩展名、Windows 设备名或路径穿越；记忆文件和目录也不允许符号链接/目录联接。

```text
模型选择 name → Registry → 参数校验 → Plan/Do → 权限检查
  → MemoryStore.read(name) → tool_result
  → conversation.add_tool_round 成对写入
  → 下一次 LLM 请求看到这条正文 → 回答用户
```

正文作为正常工具结果进入消息，不会被放回系统提示。旧工具正文清理和对话摘要仍然适用；被压缩移除后可以再次读取。索引更新时间变化也提醒模型检查旧正文；目前没有代码级 alreadySurfaced 检索状态机。

### 4. 模型请求保存，程序执行存储

```json
{
  "action":"remember",
  "name":"feedback_commit_boundary",
  "description":"章节切换前的提交约定",
  "type":"feedback",
  "content":"进入下一章前，先检查并处理上一章的未提交改动。"
}
```

以上是 update_memory 的参数。remember 必须提供全部五个字段；forget 只需要 action 和 name；rebuild_index 只需要 action。程序校验四种类型、名称、描述和正文长度，更新时间由程序生成，不能通过工具参数伪造。

`MemoryStore.remember()` 核心流程：

```python
entries = self._metadata(excluding=name) + [metadata]
index = format_index(entries)
self._invalidate_index()
self._write(path, format_memory(metadata, content))
return self._save_index(index, message)
```

相同 name 更新同一文件。先从其他文件头和本条新元数据生成目标索引、检查容量，再撤下旧索引、写正文、写新索引。这样不必在写入前后重复扫描，也能避免正文写完才发现索引超限。`_write()` 用临时文件替换正式文件。删除同样先计算剩余条目的索引，再撤下旧索引、删除正文、写新索引。

读写由进程内锁串行协调，防止取消异步等待后，尚在运行的写盘线程与下一次操作交叉。正文和索引不是跨文件原子事务：撤下旧索引后若中断，保留磁盘上的实际正文状态，索引保持缺失。正文成功但索引写入失败会返回错误工具结果，明确说明“正文变更已完成”；下次读取提示重建，重启也不会加载过期索引。

保存和忘记沿用原权限链：Plan 不提供且拒绝可写工具；Do 默认需要确认；拒绝不创建或修改文件。模型也可以主动请求保存，但是否值得长期记住依赖模型与用户确认，类型校验本身不能证明内容真实或有价值。

### 5. 手工编辑后显式重建

独立文件是数据源，MEMORY.md 由程序维护。手工修改文件头、添加或删除文件后，调用：

```json
{"action":"rebuild_index"}
```

这是 update_memory 的第三种操作，仍然经过 Plan/Do 和权限检查。Fake 手测入口为 `/memory-rebuild`。

```python
def rebuild_index(self) -> str:
    with _MEMORY_LOCK:
        self._check_legacy_index()
        index = format_index(self._metadata())
        self._write(self._path("MEMORY.md"), index)
        return "已从独立记忆文件的 YAML 头重建 MEMORY.md。"
```

`_metadata()` 只读每条文件的 YAML 头，遇到闭合的 `---` 就停止，不加载正文。最多 100 条、单个头部 4096 字符、索引 16000 字符。任一头部损坏就报错，不跳过它生成残缺索引；修复文件后再重建。重建不修改独立正文，也不替用户更新 updated_at。

例如：手工把 description 从“旧描述”改为“新描述”后，`/memory` 仍显示旧描述；允许 `/memory-rebuild` 后，`/memory` 和下个任务的 system prompt 才显示新描述。只改正文时，按名读取立即取得磁盘新正文；仍应手工更新 updated_at 并重建，通知后续任务该条已变化。

### 6. 任务结束与下次使用

```text
turn_started 落盘 → Agent 循环 → 最终回答
  → turn_finished 保存工作快照和 UI 事件 → loop_complete
```

新会话的 messages 为空，但仍加载同一项目的静态指令和动态索引。忘记会删除对应正文文件并重建索引；不会改写历史聊天和 JSONL。

恢复时只把最后一份 snapshot 给模型，旧 UI 事件只用于显示。即便旧对话已经摘要，恢复后也不会把旧全文重新装回上下文。累计 Token 和任务数独立保存，不能通过摘要后的消息条数反推。

## 启动与测试

```powershell
npm run dev
```

Fake 模式下按顺序手测；真实模型使用自然语言。Fake 命令只用于验证流程，不会判断这条记忆是否真的值得保存。

1. Do +“修改需确认”，发送 `/remember feedback commit_boundary 章节切换前的提交约定 | 进入下一章前，先处理上一章的未提交改动`，点击允许。
2. 检查 MEMORY.md 只有描述和链接；commit_boundary.md 含 YAML 头、程序生成的更新时间和正文。
3. 新建会话，发送 `/memory`，只看到索引；发送 `/memory commit_boundary`，才看到正文。
4. Plan 下读取仍可执行，remember 不会写入；Do 下拒绝确认也不写入。
5. 手工修改 commit_boundary.md 的 description 和 updated_at，`/memory` 仍显示原索引。发送 `/memory-rebuild`，先拒绝一次，确认未变；再允许，确认描述更新。
6. 再次以同名 remember 更新，索引只有一条；`/forget commit_boundary` 获准后文件和索引条目消失。
7. 关闭重启，检查会话与工具卡片恢复；旧确认按钮不能重新授权。
8. 在测试项目删除 MEMORY.md，发送 `/memory` 应提示缺失且不会自动生成；允许 `/memory-rebuild` 后恢复。
9. 多轮长对话后 `/compact`，重启后模型恢复摘要快照，界面仍能显示旧记录。

```powershell
conda run --no-capture-output -n mycoder python -m pytest
conda run --no-capture-output -n mycoder mypy src
conda run --no-capture-output -n mycoder ruff check .
npm run typecheck
npm run test:frontend
npm run build
node tests/ui/ch08_electron.mjs
```

本地测试覆盖索引/正文分离、按需读取、四种类型、字段和路径校验、头部超过 30 行、容量限制、同名更新、忘记、权限拒绝、Plan 只读、旧格式保护、索引写入失败与会话重启。专项 Electron 使用独立临时项目和 FakeLLM，不读取真实密钥。真实模型选择记忆与主动保存的质量仍需实际评估。

本次验证通过：180 项 Python 测试、mypy（46 个源码文件）、ruff、Git 差异检查和真实 Electron 专项测试。覆盖读取不扫描或写盘、手工编辑后显式重建、缺失/损坏索引、写入或删除后的索引落盘失败，以及重建的允许/拒绝与 Plan 限制。桌面专项同时验证按需读取、会话切换、完整进程重启和忘记。

## 面试回答

“我把持久上下文分成静态项目指令与动态记忆。静态指令由用户维护在根目录 AGENTS.md。动态记忆分 user、feedback、project、reference 四类，每条保存为带元数据的 Markdown 文件，程序生成 MEMORY.md 索引。

每个用户任务开始读取 AGENTS.md 和磁盘 MEMORY.md；主模型根据描述判断相关性，通过工具读取需要的正文。保存时机由主模型根据用户要求或已确认的长期信息决定，不是每轮结束自动提取。模型提出保存、更新或忘记请求，权限通过后由程序写独立文件并更新索引；手工改文件后则显式重建。程序负责类型、路径、大小校验及文件替换。

会话恢复另用 JSONL，恢复压缩后的工作消息和界面记录。这样会话续接与跨会话记忆职责分开。目前没有后台提取代理或向量库，后续可以按需要加入任务结束后的结构化提取及检索排序。”

## 常见问题

- **四个示例文件也是索引吗？** 不是。只有 MEMORY.md 是索引；其他文件的头部用于生成索引，正文记录具体记忆。
- **记忆为何调用工具？** 模型决定读/写什么，程序执行真正的文件操作。自动判断与工具执行可以同时存在。
- **旧版单文件记忆怎么办？** 本项目升级前没有记忆数据。其他目录若检测到旧版 MEMORY.md，会报错并保留原文件；先将它备份到记忆目录外，按四类逐条整理，再通过新工具保存，不自动猜分类。
- **能手工编辑吗？** 可以编辑独立文件，保持 name 与文件名一致、四种合法类型和有效 updated_at；修改事实时同步更新时间。修改后调用 rebuild_index 更新索引，不要直接维护派生 MEMORY.md。
- **索引坏了怎么办？** 新任务和 read_memory 只报告异常，不自动扫描。允许 update_memory 的 rebuild_index 操作后，从合法文件头重建。旧版单文件记忆仍保留原文，不直接覆盖。
- **时间越新越可信吗？** updated_at 只是修改时间；没有实现自动事实核实、TTL 或统一遗忘机制。
- **删除记忆后聊天中还出现？** 忘记删除的是持久记忆文件；历史聊天和已读入的上下文不会被抹掉。用户最新更正优先于旧内容。
- **tool-results 还能删吗？** 当前会话可能引用它，仍需保留；完整工具大结果依赖 read_artifact 取回。
- **多进程能共享写入吗？** 暂不支持多个 Bridge 同时写同一项目；当前锁只协调一个进程。
- **三层压缩后仍超限？** 按约定留到后续，本次没有扩展该兜底。

## 变更记录

- `7fd1768` 提交会话与单文件项目记忆基础闭环，作为本次完善前的基线。
- `27da642` 提交四种记忆类型、独立正文、元数据索引与按需读取版本。
- 本次取消读取时扫描头部，改为直接读取 MEMORY.md；新增 rebuild_index 及 Fake 重建入口。
- 写入前撤下旧索引，中断后显式重建，避免使用过期索引；同步本地测试与说明，作为第八章独立完善提交。

## 自测题与答案

1. **MEMORY.md 与其他文件的区别？** 前者是生成的索引，后者是完整记忆的数据源。
2. **为何不把所有正文都放 system？** 不相关正文会消耗上下文并干扰任务；先看描述，再按需读取。
3. **谁做相关性判断？** 当前是主模型，程序只提供索引和受控读取，没有另一个小模型选择器。
4. **“自己学”会修改模型参数吗？** 不会，修改的是外部文件和后续请求的上下文。
5. **为什么只允许四种类型仍可能存错？** 类型是结构约束，不能替代事实核实与价值判断。
6. **索引写入失败会丢记忆吗？** 已成功写入的正文保留，旧索引已撤下；读取提示不可用，允许 rebuild_index 后从当前文件头恢复。
7. **压缩后是否永久禁止再次读取同一记忆？** 不禁止，正文被移出上下文后可以按需读取。

8. **什么时候扫描 YAML 头？** 记忆新增、更新、删除时生成新索引，或显式重建时扫描；新任务读取索引时不扫描。
