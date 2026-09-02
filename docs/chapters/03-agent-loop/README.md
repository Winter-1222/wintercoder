# 第 3 章：Agent Loop

本章的目标是让霁雪不只“调用一次工具就停下”，而是可以反复执行：模型决定用工具，Agent 执行工具，把结果交还模型，直到模型给出最终回答。

本步骤已经完成最小循环、自然停止、50 轮上限和界面轮次展示。用户取消、异常工具连续检测、并发执行和 Plan Mode 还没有实现。

## 当前成果

- 真正的 Agent 核心仍只有一个入口：`src/jixue/agent.py` 中的 `Agent.run()`。
- 一轮代表一次 LLM API 请求；一次用户任务可以包含很多轮。
- 模型返回工具请求时，Agent 执行全部工具，再进入下一轮。
- 模型不再请求工具时，循环自然结束。
- 默认最多执行 50 轮，防止模型无限循环。
- `turn_complete` 表示一轮 LLM 结束，`loop_complete` 表示整条用户任务结束。
- Fake 模式新增 `/loop 文件1 文件2`，无需 API Key 就能观察三轮循环。
- 页面状态栏会显示当前进行到第几轮。

## 推荐阅读顺序

只按下面顺序看，不需要一开始读完整个项目：

1. `src/jixue/agent.py`：重点看 `Agent.run()` 中的 `for iteration`。
2. `src/jixue/llm/fake.py`：看 `/loop` 怎样确定性地请求两次工具。
3. `src/jixue/tools/registry.py`：复习 Agent 怎样按名称执行工具。
4. `src/jixue/bridge/application.py`：看 AgentEvent 怎样被套上通信信封。
5. `apps/desktop/src/renderer/src/App.tsx`：看页面怎样分发事件。
6. `apps/desktop/src/renderer/src/state.ts`：看 reducer 怎样记录轮次和完成状态。

如果只想先抓住核心，读前两个文件就够了。

## Agent、Bridge 和界面的分工

| 部件 | 它负责什么 | 它不知道什么 |
| --- | --- | --- |
| `Agent` | 历史、LLM、工具、循环、停止 | Electron、stdin、页面样式 |
| `BridgeApplication` | 给事件补请求编号和顺序 | 模型为何调用工具 |
| `BridgeServer` | stdin/stdout 收发一行 JSON | Agent 业务逻辑 |
| React 页面 | 展示文字、工具卡片、轮次 | SDK 和循环内部实现 |

所以以后即使换成网页或终端 UI，`Agent.run()` 仍可复用。

## 一条普通消息怎样跑

普通消息不需要工具，只经历一轮：

```text
用户输入
  → Electron 发送 chat.send
  → BridgeApplication 调用 Agent.run(文字)
  → Agent 把用户消息加入 ConversationManager
  → Agent 第 1 轮调用 LLMClient.stream()
  → LLM 流式返回 text
  → Agent 连续发出 stream_text
  → LLM 返回 end_turn，且没有 tool_use
  → Agent 发出 turn_complete（第 1 轮结束）
  → Agent 发出 loop_complete（整个任务结束）
  → reducer 解锁输入框并做 Markdown 渲染
```

最重要的判断不是“有没有文字”，而是“这一轮有没有 `tool_use`”：

```python
for iteration in range(1, 51):
    模型响应 = 调用一次 LLM
    发出 turn_complete

    if 模型没有请求工具:
        break

    执行工具
    把 tool_use 和 tool_result 追加到历史

发出 loop_complete
```

这段伪代码就是当前 Agent Loop 的骨架。

## `/loop` 的三轮完整链路

在 Fake 模式输入：

```text
/loop README.md docs/ROADMAP.md
```

它会确定性地走下面这条链路：

```text
第 1 轮 LLM
  → 请求 read_file(README.md)
  → turn_complete(iteration=1)
  → Agent 执行工具
  → tool_result(fake_loop_1)

第 2 轮 LLM
  → 收到上一轮完整的 tool_use + tool_result
  → 请求 read_file(docs/ROADMAP.md)
  → turn_complete(iteration=2)
  → Agent 执行工具
  → tool_result(fake_loop_2)

第 3 轮 LLM
  → 收到前两次工具请求和结果
  → 输出最终 Markdown
  → stop_reason=end_turn，没有新工具
  → turn_complete(iteration=3)
  → loop_complete(iterations=3)
```

为什么工具请求和工具结果都要放回历史？因为模型必须知道“自己刚才请求了什么，以及工具回答了什么”，才能决定下一步。`tool_result.tool_use_id` 还必须和原来的工具请求 ID 相同，否则供应商会拒绝消息格式。

## 两个完成事件为什么不能合并

| 事件 | 含义 | UI 应该做什么 |
| --- | --- | --- |
| `turn_complete` | 一次 LLM 请求结束 | 更新轮次，继续等待 |
| `loop_complete` | 整条用户任务结束 | 解锁输入框，完成 Markdown 渲染 |

如果收到第一次 `turn_complete` 就解锁输入框，用户可能在工具还没执行时发送新消息，两条历史会交叉。因此 reducer 只有收到 `loop_complete` 才真正收口。

## 50 轮上限怎样停止

默认上限是 50。若第 50 轮仍请求工具：

1. 不再真正执行这批工具。
2. 给工具卡片返回错误结果，避免它一直显示“执行中”。
3. 在回复中加入“已自动停止”的提示。
4. 发出 `loop_complete`，其中 `stop_reason` 是 `max_iterations`。

测试时可以传入 `Agent(..., max_iterations=3)`，不用真的跑 50 轮。正常启动不传这个参数，使用默认值即可。

## 启动和手动测试

确认项目根目录 `.env` 中没有真实配置或把 `JIXUE_LLM_MODE` 设为 `fake`，然后在项目根目录运行：

```powershell
npm run dev
```

依次测试：

1. 输入普通消息，应该显示“正在第 1 轮”，最后显示“共 1 轮”。
2. 输入 `/read README.md`，应该出现一张工具卡片，最后显示“共 2 轮”。
3. 输入 `/loop README.md docs/ROADMAP.md`，应该依次出现两张 `read_file` 卡片，最后显示“共 3 轮”。
4. 两张卡片都完成后，最终回复应出现“Agent Loop 完成”。
5. 完成后输入框应恢复可用，页面仍能上下滚动。

自动检查：

```powershell
npm run test:all
conda run --no-capture-output -n mycoder ruff check .
conda run --no-capture-output -n mycoder mypy src tests
npm run test:electron
```

## 常见坑

- 把 `turn_complete` 当成任务结束：工具循环会在第一轮就被 UI 提前截断。
- 只把 `tool_result` 放回历史：模型看不到对应的 assistant `tool_use`，API 消息格式不完整。
- 工具失败就抛程序异常：模型失去调整参数和重试的机会；普通工具错误应继续作为 `ToolResult` 返回。
- 忘记上限：模型可能重复请求同一个工具，任务永远不结束。
- 用 `stop_reason=end_turn` 作为唯一判断：当前实现优先检查有无工具请求，更容易兼容不同供应商。
- 在 Agent 中导入 `anthropic`：会破坏供应商隔离，SDK 只能存在于适配器。

## 暂未实现

- 用户点击停止并传播取消信号。
- 连续请求不存在工具时提前终止。
- 根据 `isConcurrencySafe()` 对多个工具分批并发。
- `/plan`、`/do` 和只读工具模式。

这些能力会继续按小步骤加入，本步骤不提前增加抽象。

## 本章变更记录

- 第 1 步：建立独立 `Agent` 核心，Bridge 只保留协议转发。
- 第 2 步：把固定两次请求改为真正循环。
- 第 2 步：加入自然停止、默认 50 轮上限和 `loop_complete`。
- 第 2 步：加入 Fake 两工具演示和前端轮次显示。

## 自测题与答案

**问：一轮和一条用户任务是同一件事吗？**

答：不是。一轮是一次 LLM API 请求；一条任务可能经过很多轮模型请求和工具执行。

**问：Agent Loop 最核心的继续条件是什么？**

答：本轮模型返回了至少一个 `tool_use`。Agent 执行工具、追加结果，然后进入下一轮。

**问：什么时候自然停止？**

答：一轮 LLM 响应结束后没有任何 `tool_use`，通常同时会得到 `stop_reason=end_turn`。

**问：为什么 UI 不能在 `turn_complete` 时解锁？**

答：因为这可能只是工具循环中的一轮，Agent 后面还要执行工具和再次请求模型。只有 `loop_complete` 才代表整条任务完成。

**问：`/loop` 为什么需要三轮 LLM 请求？**

答：第一轮请求第一个工具，第二轮看完第一个结果后请求第二个工具，第三轮看完第二个结果后输出最终回答。

**问：第 50 轮仍请求工具时会怎样？**

答：Agent 不执行最后一批工具，关闭对应工具卡片，给出自动停止提示，并以 `max_iterations` 结束整个循环。
