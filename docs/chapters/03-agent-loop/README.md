# 第 3 章：Agent Loop

本章的目标是让霁雪不只“调用一次工具就停下”，而是可以反复执行：模型决定用工具，Agent 执行工具，把结果交还模型，直到模型给出最终回答。

目前已经完成最小循环、自然停止、50 轮上限、界面轮次展示和用户取消。异常工具连续检测、并发执行和 Plan Mode 还没有实现。

## 当前成果

- 真正的 Agent 核心仍只有一个入口：`src/jixue/agent.py` 中的 `Agent.run()`。
- 一轮代表一次 LLM API 请求；一次用户任务可以包含很多轮。
- 模型返回工具请求时，Agent 执行全部工具，再进入下一轮。
- 模型不再请求工具时，循环自然结束。
- 默认最多执行 50 轮，防止模型无限循环。
- `turn_complete` 表示一轮 LLM 结束，`loop_complete` 表示整条用户任务结束。
- 运行中可以点击停止按钮；取消只结束当前任务，应用仍可继续对话。
- 页面状态栏会显示当前轮次、正在停止和已停止状态。
- 取消的消息保留在 UI，但不会进入下一次 LLM 上下文。

## 推荐阅读顺序

只按下面顺序看，不需要一开始读完整个项目：

1. `src/jixue/agent.py`：先看 `Agent.run()` 的循环，再看 `cancel()` 和 `_stream_llm()`。
2. `src/jixue/tools/registry.py`：复习 Agent 怎样按名称执行工具。
3. `src/jixue/bridge/application.py`：看 `chat.send` 和 `chat.cancel` 怎样并行到达。
4. `src/jixue/bridge/server.py`：看为什么一个任务运行时仍能读取取消命令。
5. `apps/desktop/src/main/bridge-process.ts`：看 Electron 怎样写入取消信封。
6. `apps/desktop/src/renderer/src/App.tsx`：看发送按钮怎样切换成停止按钮。
7. `apps/desktop/src/renderer/src/state.ts`：看 reducer 怎样等待 `loop_complete` 后解锁。

如果只想先抓住核心，读第 1、3、6 个文件即可。

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

## 同时读取两个文件为什么通常只有两轮

用户让模型读取 `AGENTS.md` 和 `README.md` 时，模型可以在第一轮响应中一次返回两个 `tool_use`：

```text
第 1 轮 LLM
  用户问题
    → 模型返回 read_file(AGENTS.md)
    → 模型返回 read_file(README.md)
    → turn_complete(iteration=1)

Agent 执行工具
  → 得到 AGENTS.md 的 tool_result
  → 得到 README.md 的 tool_result

第 2 轮 LLM
  完整历史 + 两个 tool_result
    → 模型输出最终回答
    → turn_complete(iteration=2)
    → loop_complete(iterations=2)
```

轮数少并不代表工具已经并发。当前 Agent 仍用 `for call in tool_calls` 依次执行两个工具，只是把两个结果放在同一条 user 工具结果消息中，一次送回模型，所以总共两轮。以后加入并发只会减少工具等待时间，不会改变这里的轮数。

如果模型第一轮只读取 `AGENTS.md`，看完后第二轮才决定读取 `README.md`，再用第三轮回答，那么页面会显示三轮。轮数由 LLM 请求次数决定，不由工具数量决定。
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

## 点击停止后怎样传到 Agent

停止不是关闭 Electron，也不是杀死 Python 进程。它只取消当前 `request_id` 对应的 Agent Loop：

```text
用户点击停止按钮
  → Renderer 调用 window.jixue.cancelChat(request_id)
  → Preload 转发到 Electron Main
  → PythonBridge 写入 chat.cancel 信封
  → BridgeServer 并发读取这条新命令
  → BridgeApplication 核对目标是否是当前请求
  → Agent.cancel() 设置 asyncio.Event
  → _stream_llm() 同时等待“下一个模型事件”和“取消信号”
  → 取消信号先到：关闭正在等待的模型流
  → Agent 发出 loop_complete(cancelled=true)
  → reducer 标记“已停止”并重新解锁输入框
```

这里最容易误解的是：`chat.send` 还没有完成，为什么 Python 还能收到 `chat.cancel`？因为 `BridgeServer` 为每条命令创建独立异步任务。聊天任务等待网络数据时，读取循环仍可继续接收下一行 JSON。

取消后，页面会保留用户问题和已经流出的半截回复，方便复盘；`ConversationManager` 会把这一对消息标记为 `cancelled`。`to_api_format()` 只发送 `complete` 消息，因此下一次提问不会夹带被取消的任务。

当前内置文件工具执行很快。若取消时工具已经开始，它可能先完成当前工具；Agent 会在下一处取消检查点停止，不再进入下一轮。
## 启动和手动测试

在项目根目录 `.env` 中使用真实配置：

```env
JIXUE_LLM_MODE=configured
```

然后启动：

```powershell
npm run dev
```

按下面顺序手测：

1. 输入“请读取 AGENTS.md 和 README.md，并分别总结它们的作用”。
2. 如果模型第一轮同时请求两个文件，最后通常显示“共 2 轮”；工具目前仍是串行执行。
3. 再发送一个会产生较长回复的任务，例如“详细分析当前项目结构，并给出逐文件阅读建议”。
4. 模型开始流式输出后，点击输入框右下角的方形停止按钮。
5. 状态栏应先显示“正在停止”，随后消息标题显示“已停止”。
6. 输入框恢复可用后发送“你好”，应能正常收到新回答，并且模型不应继续上一条已取消任务。
7. 点击停止不会关闭窗口，也不会让 Python Bridge 离线。

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
- 在 Agent 中导入 nthropic：会破坏供应商隔离，SDK 只能存在于适配器。
- 收到取消就杀掉 Python：应用无法继续对话；取消只应结束当前 Loop。
- 取消后仍把消息发给模型：下一轮会继续已经放弃的任务；取消消息必须从 API 历史中过滤。
- 只在每轮开头检查取消：模型长时间没有新数据时按钮会像失效；当前实现同时等待模型流和取消信号。

## 暂未实现

- 连续请求不存在工具时提前终止。
- 根据 `isConcurrencySafe()` 对多个工具分批并发。
- `/plan`、`/do` 和只读工具模式。

这些能力会继续按小步骤加入，本步骤不提前增加抽象。

## 本章变更记录

- 第 1 步：建立独立 `Agent` 核心，Bridge 只保留协议转发。
- 第 2 步：把固定两次请求改为真正循环。
- 第 2 步：加入自然停止、默认 50 轮上限和 `loop_complete`。
- 第 2 步：加入离线多轮测试和前端轮次显示。
- 第 3 步：加入 chat.cancel、可中断模型流和停止按钮。
- 第 3 步：取消消息保留给 UI，但从后续 API 历史中过滤。

## 自测题与答案

**问：一轮和一条用户任务是同一件事吗？**

答：不是。一轮是一次 LLM API 请求；一条任务可能经过很多轮模型请求和工具执行。

**问：Agent Loop 最核心的继续条件是什么？**

答：本轮模型返回了至少一个 `tool_use`。Agent 执行工具、追加结果，然后进入下一轮。

**问：什么时候自然停止？**

答：一轮 LLM 响应结束后没有任何 `tool_use`，通常同时会得到 `stop_reason=end_turn`。

**问：为什么 UI 不能在 `turn_complete` 时解锁？**

答：因为这可能只是工具循环中的一轮，Agent 后面还要执行工具和再次请求模型。只有 `loop_complete` 才代表整条任务完成。

**问：一次请求两个读文件工具，为什么通常只显示两轮？**

答：模型在第一轮可以同时返回两个 `tool_use`，Agent 收集两个结果后用第二轮一次性交给模型生成最终回答。工具数量不是轮数。

**问：点击停止会退出程序吗？**

答：不会。停止只给当前 Agent Loop 设置取消信号，Electron 和 Python Bridge 都继续运行，用户可以马上发送下一条消息。

**问：为什么取消后不能把原问题留在下一次 API 历史里？**

答：用户已经明确放弃该任务。如果仍发送给模型，它可能在新问题中继续旧任务。内部将取消消息标记为 `cancelled`，API 转换时会过滤它们。

**问：第 50 轮仍请求工具时会怎样？**

答：Agent 不执行最后一批工具，关闭对应工具卡片，给出自动停止提示，并以 `max_iterations` 结束整个循环。
