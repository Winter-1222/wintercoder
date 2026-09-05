# 第七章：上下文管理

本章解决一个问题：Agent 工作久了以后，怎样避免把越来越多的内容全部塞给模型。

本章已经完成六步：**单个大结果落盘**、**活动上下文视图**、**手动 `/compact`**、**自动压缩触发器**、**超长请求单次重试**和**连续失败暂停**。

## 1. 这一小步完成了什么

- 工具结果不超过 50,000 字符：保持原样，不写文件。
- 工具结果超过 50,000 字符：完整内容写入 `.jixue/tool-results/`。
- 对话历史和界面只接收“开头预览 + 结尾预览 + artifact_id”。
- 新增 `read_artifact`：模型可以按关键词搜索，也可以按字符范围分段读取。
- `read_file` 新增行号范围，适合只重读大文件中真正需要的部分。
- `.jixue`、`.env` 和 `.env.*` 不允许被文件读取与搜索工具访问。
- 同一条长任务的工具结果超过 200,000 字符后，旧结果只在模型视图中变成短占位符。
- 最近 3 个完整工具轮保持原文；UI 工具卡片和本次任务的 `full_history` 不被改写。
- 输入精确的 `/compact`，可以把较早的普通对话整理成摘要，同时保留最近 2 个完整对话轮。
- 摘要使用一次独立的无工具模型请求；只有摘要完整、非空并且确实更短时才会应用。
- 原始消息仍留在内存和页面中，压缩只改变下一次发给模型的消息视图。
- 每次真正请求模型前都会测量 `active_history`；超过 160,000 字符时自动复用同一摘要事务。
- 自动压缩成功后会重新生成摘要视图，并接回当前任务已有的完整工具轮；失败则继续使用旧视图。
- 供应商返回 `prompt_too_long` 时，会调用同一摘要事务、重建请求并且只重试一次。
- 自动摘要连续失败 3 次后暂停，避免反复产生失败请求；手动 `/compact` 成功后恢复。

这里的 artifact 可以先理解成“工具大结果的临时档案”。它不是长期记忆，也不是聊天记录。

## 2. 推荐阅读顺序

1. `src/jixue/context.py`
   依次看大结果落盘、`ActiveContext.build`、摘要提示词和 `<summary>` 提取。
2. `src/jixue/domain/conversation.py`
   看 `prepare_compaction`、`apply_compaction` 和 `_api_view` 怎样保存摘要边界。
3. `src/jixue/agent.py`
   搜索 `needs_auto_compaction` 和 `_apply_compaction_transaction`，看手动和自动入口怎样共用同一事务。
4. `src/jixue/llm/adapters/anthropic_client.py`
   看无工具摘要请求为什么连 `tools` 字段也不发送。
5. `src/jixue/tools/read_artifact.py`
   看模型怎样按需取回档案中的小片段。
6. `src/jixue/tools/read_file.py`
   看普通文件怎样按起止行读取。
7. `src/jixue/bridge/server.py`
   看 `read_artifact` 怎样注册进 Agent 的工具列表。

第一遍只顺着上面的链路读，不必先研究每个校验分支。

## 3. 一条大工具结果怎样跑起来

```text
用户发送任务
  ↓
模型返回 tool_use
  ↓
ToolRegistry 执行对应工具，得到 ToolResult
  ↓
Agent._execute_tool_call 调用 ToolResultStore.prepare
  ↓
结果是否超过 50,000 字符？
  ├─ 否：原样返回
  └─ 是：完整内容写入 .jixue/tool-results
           ↓
         生成有界预览和 artifact_id
           ↓
         同一个预览发往两个地方
           ├─ tool_result 事件 → Electron 界面
           └─ APIToolResultBlock → 下一轮模型请求
                                      ↓
                         模型信息够用：直接回答
                         模型需要细节：调用 read_artifact
```

关键代码可以缩成下面几句伪代码：

```python
result = await registry.execute(tool_name, tool_input)
safe_result = await result_store.prepare(tool_use_id, tool_name, result)

yield tool_result_event(safe_result)       # 给界面
full_history.append(api_tool_result(safe_result))  # 保留本次任务的原文
```

为什么放在 Agent 中统一处理，而不是每个工具各写一次？

因为内置工具和 MCP 工具最后都会经过 `_execute_tool_call`。只守住这一个出口，就不会漏掉某个工具。

## 4. 多轮工具结果怎样退出活动上下文

`full_history` 是当前这一条用户任务里的完整 API 历史。它不会直接交给模型，而是每轮先复制成 `active_history`：

```text
当前任务的 full_history（原文）
  ↓ ActiveContext.build，生成副本
统计 active_history 中的 tool_result 字符数
  ↓ 超过 200,000 字符才开始清理
保留最近 3 个完整工具轮
  ↓ 从最旧的完整轮开始，把 result.content 换成短占位符
清到约 120,000 字符，或已经没有可清理的旧轮
  ↓
self._llm.stream(active_history, ...)
```

这里只替换内容，不删除消息和块：

```text
assistant: tool_use(id=call_1)               保留
user:      tool_result(id=call_1, 原始结果)  变成短占位符
```

所以 `tool_use_id`、消息顺序和错误标记仍在，Anthropic 协议要求的配对不会被拆散。一轮有多个并行工具调用时，会整轮一起处理；ID 对不上或结果尚未返回的异常结构保持原样。

如果旧结果之前已经落盘，占位符会保留 `artifact_id`。没有档案的旧结果则提示模型重新调用对应工具。

为什么不是刚超过阈值就只删几个字？一次从 200,000 左右清到 120,000 左右，可以留出余量，减少连续几轮反复改写旧前缀，对 Prompt Cache 更友好。

UI 不使用 `active_history`。工具执行时发出的 `tool_result` 事件仍带当时的完整可见结果，所以页面上的旧工具卡片不会突然消失。

注意：这里的 `full_history` 只存在于一次 `Agent.run()` 中。任务结束后，只把用户文字和最终回答留在 `ConversationManager`，上一轮的 `tool_use` 和 `tool_result` 不会直接进入手动摘要；重要工具结论应先体现在最终回答中。手动 `/compact` 处理的是这些普通对话；跨重启持久化仍属于第 8 章。

## 5. 手动 `/compact` 怎样跑起来

`/compact` 继续走普通聊天通道，所以 Electron 不需要增加另一套 IPC 和页面状态。但 Agent 会在写入普通历史之前拦截这个精确命令：

```text
输入 /compact
  ↓ Renderer 按普通消息发送 chat.send
BridgeApplication 调用 Agent.run("/compact")
  ↓ Agent 识别命令，不执行 add_user
ConversationManager.prepare_compaction(keep_recent_turns=2)
  ├─ 不足 3 个完整对话轮：提示“历史不足”，不调用模型
  └─ 足够：取出较早前缀，最近 2 轮保留原文
              ↓
      追加九部分摘要要求
              ↓
      self._llm.stream(..., tools=空, system=摘要专用提示词)
              ↓
      内部收集输出，页面不会看到半份摘要
              ↓
      只提取完整的 <summary>，并检查它确实比原文短
              ↓
      apply_compaction(summary, cutoff)
              ↓
      stream_text 显示完成提示
      turn_complete → loop_complete → 输入框恢复
```

摘要要求模型先用 `<analysis>` 整理草稿，再在 `<summary>` 中保留九部分：用户意图、技术概念、文件与代码、错误与修复、解决过程、用户消息、待办、当前工作和下一步。程序只保存 `<summary>`。

核心伪代码只有四步：

```python
source, cutoff, count = conversation.prepare_compaction(keep_recent_turns=2)
raw = await summarize(source, tools=())
summary = extract_compaction_summary(raw)
conversation.apply_compaction(summary, cutoff)
```

这里的“完整对话轮”不是简单数数组元素，而是按真正的 API 清洗规则计算：一个有效 user 分组后面跟着一个有效 assistant 分组才算一轮；空消息、失败消息和取消消息不会参与，相邻同角色消息会先合并。这样压缩边界不会切在半轮对话中间。

真正代码还会检查九部分是否齐全、摘要不能过短、模型必须以 `end_turn` 正常结束，而且摘要要比原文短。网络错误、模型误调工具、标签不完整、输出被截断、摘要反而更长，都会在 `apply_compaction` 前失败，所以原历史不会被半成品覆盖；点击停止也会保留原历史。

下一条普通消息到来时，`to_api_format()` 生成的模型视图是：

```text
user:      <system-reminder>较早对话摘要</system-reminder>
assistant: 我已了解以上压缩摘要，将从这里继续。
...最近 2 个完整 user/assistant 对话轮...
user:      用户的新问题
```

固定的 assistant 确认句用于维持 API 的角色交替，不是模型现场回复。页面仍保留旧聊天；`/compact` 和完成提示只是页面操作记录，不进入下一次模型历史。

## 6. 自动压缩怎样触发

自动压缩不看 `total_usage`。它是整个会话已经花掉的累计账单，即使历史很短也可能很大，不能代表下一次请求的大小。

Agent 会在每次调用模型前先生成真正的 `active_history`，然后估算其中的消息文字、工具参数和工具结果。当前触发线是 160,000 字符；构造 Agent 时可以传入更小的值做自动化测试。

```text
当前任务 full_history
  ↓ ActiveContext.build
本次即将发送的 active_history
  ↓ api_history_characters > 160,000？
  ├─ 否：直接调用模型
  └─ 是：本任务尝试一次共用摘要事务
          ├─ 无可压缩旧轮次：继续原请求
          ├─ 摘要失败：不移动边界，继续原请求
          ├─ 用户取消：停止当前任务，原历史不变
          └─ 摘要成功
               ↓
             重新调用 ConversationManager.to_api_format()
               ↓
             加回动态提醒和当前任务已经完成的工具轮
               ↓
             再次构建 active_history 后调用模型
```

重建这一步不能省略。压缩前的 `full_history` 仍包含旧普通对话；如果继续使用它，虽然摘要已经提交，本轮请求却不会变小。另一方面，当前任务内部的 `tool_use` 和 `tool_result` 只存在于本地 `task_history`，重建时必须成对接回，否则供应商会拒绝消息结构。

手动和自动入口最终都调用 `_apply_compaction_transaction()`。因此完整标签、九个章节、最小长度、`end_turn`、摘要更短、无工具请求和取消回滚只有一套实现。

### 预算没有预判到超长怎么办

Anthropic 适配器会把 413，以及错误正文明确包含 context length、context window 等标记的 400，统一翻译成安全的 `prompt_too_long`。Agent 不解析供应商原始错误文字。

只有本次模型请求尚未产生任何流事件时，Agent 才会处理这个错误：调用共用摘要事务，成功后按上面的方式重建 `full_history` 和 `active_history`，再重试同一轮模型请求。重试额度在一条用户任务中只有一次；重建后仍然超长就把错误交给页面，不能无限循环。

如果模型已经输出过文字、工具调用或用量事件，则不会重试。这样可以避免页面看到重复正文或重复工具卡片。

### 为什么连续失败后要暂停

摘要失败后原任务仍使用旧视图继续，但如果每条消息都再次自动摘要，既浪费费用，也可能一直得到同一种失败。因此 Agent 记录自动摘要的连续失败次数：

- 自动摘要成功：计数清零。
- 没有可压缩旧轮次：不算失败。
- 用户取消：不算失败，并停止当前任务。
- 生成、网络或摘要校验失败：计数加一。
- 达到 3 次：后续预算触发和超长恢复都不再自动调用摘要模型。

暂停只存在于当前 Agent 进程内，不删除任何历史。用户输入 `/compact` 仍可手动尝试；手动压缩成功后会清零计数并恢复自动压缩。

## 7. 落盘以后为什么仍可能占上下文

落盘不是让内容“永远免费”，而是把一次性塞入 60,000 字符，改成模型按需要取 8,000 字符。

例如一个 100,000 字符结果：

```text
旧做法：下一轮直接携带约 100,000 字符
新做法：下一轮先携带约 8,000 字符预览
        如果预览够用，剩余内容永远不进入上下文
        如果不够，再搜索或读取一个不超过 20,000 字符的片段
```

所以 `read_artifact` 返回的片段仍会占用上下文。真正的收益是：**由模型选择需要的少量内容，而不是无条件装入全部内容。**

## 8. 怎样按需读取

`read_artifact` 不接受任意文件路径，只接受落盘提示中的 `artifact_id`。

优先搜索关键词：

```json
{
  "artifact_id": "toolu_abc-1a2b3c4d5e6f",
  "search": "报错关键词"
}
```

搜索不到或需要上下文时，再分段读取：

```json
{
  "artifact_id": "toolu_abc-1a2b3c4d5e6f",
  "offset": 8000,
  "limit": 8000
}
```

- `offset`：从第几个字符开始，0 表示开头。
- `limit`：本次最多返回多少字符，默认 8,000，最大 20,000。
- 结果没读完时，会提示下一次该使用的 offset。

`read_artifact` 虽然是只读工具，但目前会先把一份档案加载到内存，所以它声明为“不允许并发”。这是 `is_concurrency_safe` 根据具体资源开销做判断的例子：只读不等于一定适合并发。

普通项目文件则继续使用 `read_file`。它现在可以按行读取：

```json
{
  "path": "src/jixue/agent.py",
  "start_line": 200,
  "end_line": 260
}
```

不写行号仍可读取整个文件；如果结果过大，仍会经过统一落盘保护。

## 9. 文件为什么不会反复写

档案名来自唯一的 `tool_use_id`，并附加短哈希。写入使用 Python 的 `x` 模式：

- 文件不存在：创建并写入。
- 文件已经存在：不覆盖，也不重复写。

这样同一条工具结果在后续轮次重新整理历史时，不会反复写磁盘。

`.jixue/` 已被 Git 忽略，工具大结果不会进入提交记录。

## 10. 启动与手动测试

在项目根目录启动：

```powershell
conda activate mycoder
npm run dev
```

为了稳定制造一个大文件，可在项目根目录的 PowerShell 中执行：

```powershell
New-Item -ItemType Directory -Force tests/manual
$content = "BEGIN`n" + ("中" * 60000) + "`nJIXUE-CONTEXT-777`nEND"
Set-Content -LiteralPath tests/manual/large.txt -Value $content -Encoding utf8
```

使用真实模型发送：

```text
请读取 tests/manual/large.txt。如果结果被保存为 artifact，请使用 read_artifact
搜索 JIXUE-CONTEXT-777，然后告诉我是否找到了。
```

应该看到：

1. 第一轮调用 `read_file`。
2. 工具卡片只显示预览和 `artifact_id`，不会展示 60,000 个“中”。
3. 项目下出现 `.jixue/tool-results/` 和一个 `.txt` 档案。
4. 下一轮模型调用 `read_artifact` 搜索标记。
5. 模型最终回答找到了标记，界面仍能继续发送消息。

FakeLLM 仍可用于免费验证 `/read tests/manual/large.txt` 是否触发落盘，但它不会自主继续调用 `read_artifact`；完整 Agent 决策链要用真实模型测试。

活动上下文是模型请求前的内部副本，页面不会直接展示“已清理多少”。最可靠的验证命令是：

```powershell
conda run --no-capture-output -n mycoder python -m pytest tests/test_context.py
```

手动回归时可以让真实模型完成一个多次读取的任务，确认旧工具卡片仍在、最终回复正常、停止按钮仍可用。

手动测试 `/compact` 时，先完成至少 5 个内容较充实的普通对话轮。可以沿用当前真实开发聊天，也可以在第一轮告诉模型“我的测试代号是冬青-729”，再连续讨论几个具体问题。前 3 轮应明显长于一份摘要，最后 2 轮会保留原文。

然后发送精确命令：

```text
/compact
```

预期没有工具卡片，页面最后显示“上下文压缩完成”，状态栏计入这次摘要请求的 Token，随后输入框恢复。旧聊天仍在页面上。接着询问“我的测试代号是什么”，模型应能从摘要中回答。只有 1～2 个完整对话轮时执行命令，则应提示历史不足，并且不调用模型。若待压缩前缀本身很短，九部分摘要可能没有原文短；程序会拒绝应用，这是正常保护。

还可以在压缩期间立刻点击停止。预期页面结束流式状态、输入框恢复，下一条普通消息仍能使用压缩前历史。发送“请解释 `/compact` 是什么”不应触发压缩，因为只有去掉首尾空白后完全等于 `/compact` 才是命令。

## 11. 自动化检查

```powershell
conda run --no-capture-output -n mycoder pytest
conda run --no-capture-output -n mycoder ruff check .
conda run --no-capture-output -n mycoder mypy src
npm run test:frontend
npm run typecheck
npm run test:electron
```

测试文件位于本机 `tests/`，被 Git 忽略，不会提交。

## 12. 常见问题

### 为什么不把完整内容放进 metadata？

metadata 会交给界面。如果偷偷把完整结果放进去，内存和进程通信压力仍然存在，也容易意外泄漏，因此 metadata 只保存编号、长度和本地路径等小信息。

### 为什么 `read_file` 不能直接读取 `.jixue`？

否则模型可以绕过每次最多 20,000 字符的限制，一次读取完整档案。档案只能通过 `read_artifact` 受控访问。

### Bash 输出还会在工具内部截断吗？

不会。Bash 把完整结果交给统一的 `ToolResultStore`，由它落盘并生成预览。这样磁盘里保存的是完整结果，不是已经丢失尾部的截断结果。

用户在 Bash 执行期间点击停止时，Agent 的取消信号也会终止正在等待的命令进程，避免界面停了但命令仍在后台继续。

### 当前已经解决所有上下文增长了吗？

本章已经解决模型请求侧的主要增长路径：单个大结果、同一任务旧工具结果、普通对话摘要、预算触发和超长单次重试。尚未处理的是无限大的进程内工具输出，以及跨重启会话保存和长期记忆。

另外，当前工具仍是先在 Python 进程里得到完整结果，再判断是否落盘；这一小步保护的是模型上下文，不是无限大的进程内存。真正的超大命令输出以后可再改成边产生边写盘，本章先保持代码简单。

### 为什么摘要请求不能带工具？

摘要模型只需要整理已有文字。如果继续发送工具 Schema，模型可能误以为应该执行原任务并请求工具。霁雪传入空工具列表，Anthropic 适配器会进一步省略整个 `tools` 字段。

### `/compact` 会释放 Python 内存或保存会话吗？

不会。原消息仍保存在当前进程内，压缩只缩小模型请求视图；关闭程序后也不会恢复。会话 JSONL 和长期记忆属于第 8 章。

## 13. 变更记录

- 第 1 步：加入大工具结果落盘、有界预览、`read_artifact` 和 `read_file` 行范围读取。
- 第 2 步：加入 `full_history → active_history → LLM` 视图，成轮替换旧工具结果。
- 第 3 步：加入手动 `/compact`、九部分结构化摘要、最近 2 轮原文保留和失败回滚。
- 第 4 步：按即将发送的 `active_history` 自动触发同一摘要事务，成功后重建当前任务历史。
- 第 5 步：识别供应商 `prompt_too_long`，压缩并重建后只重试一次正常模型请求。
- 第 6 步：自动摘要连续失败 3 次后暂停，手动 `/compact` 成功后恢复。

## 14. 自测题与答案

### 1）50,000 字符的结果会落盘吗？

不会。只有“大于 50,000 字符”才落盘。

### 2）落盘之后，完整内容还会立刻发给模型吗？

不会。模型先收到预览和 `artifact_id`。

### 3）模型调用 `read_artifact` 后，返回片段占不占上下文？

占，但单次有上限，而且模型可以先搜索关键词，减少无关内容。

### 4）为什么落盘逻辑放在 Agent，不放在 `read_file`？

因为 Agent 是所有内置工具和 MCP 工具结果的共同出口，统一处理最不容易遗漏。

### 5）`read_artifact` 为什么不用文件路径？

只接受安全编号可以把访问范围固定在 `.jixue/tool-results`，避免它变成另一个任意文件读取工具。

### 6）本步骤和自动压缩有什么区别？

前两步保护工具结果，第三步让用户主动摘要普通对话，第四步会在下一次 `active_history` 超过字符预算时自动运行同一摘要事务。

### 7）`full_history` 和 `active_history` 有什么区别？

前者保留当前任务的工具原文，后者只是这一轮发给模型的副本，旧结果可能已经变成占位符。

### 8）为什么页面上的旧工具卡片没有一起消失？

因为页面消费的是工具执行时产生的事件，不是模型请求前临时生成的 `active_history`。

### 9）为什么不能直接删除旧的 `tool_result`？

模型请求中的 `tool_use` 和 `tool_result` 必须用相同 ID 配对。删除其中一边可能让供应商直接拒绝整个请求，所以只替换内容、不删结构。

### 10）`/compact` 是普通用户消息吗？

不是。它借用 `chat.send` 到达 Agent，但会在 `add_user` 前被识别，因此模型历史里不会出现这条命令。

### 11）为什么保留最近 2 个完整对话轮？

最近内容通常最接近当前工作，保留原文能减少摘要遗漏细节；更早内容才交给模型整理。

### 12）压缩到一半失败或被取消会怎样？

原视图保持不变。只有完整摘要通过标签和长度校验后，`apply_compaction` 才会移动摘要边界。

### 13）为什么压缩后页面上的旧消息还在？

页面和 `ConversationManager.messages` 保留原记录；压缩改变的是 `to_api_format()` 生成的模型视图。

### 14）手动压缩和自动压缩有什么区别？

手动压缩只在用户输入 `/compact` 时运行并显示完成提示；自动压缩在模型请求前检查实际活动历史，成功后安静地重建并继续当前任务。

### 15）为什么自动压缩不能看 `total_usage`？

`total_usage` 是多轮请求累计产生的账单，不是下一次请求长度。自动触发只测量已经经过大结果活动视图处理、即将真正发送的 `active_history`。

### 16）为什么超长请求只能重试一次？

摘要后仍然超长，通常说明最近两轮、当前任务工具结果或输出预算本身已经超过限制。继续重复同一动作不会产生新信息，只会增加费用。

### 17）自动压缩暂停后怎样恢复？

输入 `/compact` 手动尝试。完整摘要成功提交后，连续失败计数清零，下一条任务可以再次自动压缩。
