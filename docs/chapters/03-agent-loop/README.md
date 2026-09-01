# 第 3 章：Agent Loop

本章正在开发。第 1 步只完成一件事：让项目中真正出现一个容易找到的 Agent 核心，同时保持第二章功能完全不变。

现在找 Agent，直接打开：

```text
src/jixue/agent.py
```

它目前仍然固定最多请求两次 LLM，只允许一次工具往返。真正的循环、停止条件、取消和并发将在后续小步骤加入。

## 当前成果

- 新增 `Agent`：维护对话历史、调用 LLM、执行工具并产生事件。
- 新增 `AgentEvent`：Agent 与 Electron 界面之间不直接依赖。
- `BridgeApplication` 从两百多行核心逻辑缩成协议转发层。
- 启动时显式执行 `Agent(llm, tools=tools)`，不再把 Agent 藏在 Bridge 中。
- SDK 仍然只存在于 Anthropic 适配器，Agent 只认识 `LLMClient`。

## 推荐阅读顺序

1. `src/jixue/agent.py`：先看真正的核心。
2. `src/jixue/bridge/application.py`：看 Bridge 如何转发事件。
3. `src/jixue/bridge/server.py`：看程序启动时如何组装 Agent。
4. `src/jixue/llm/base.py`：看 Agent 依赖的供应商无关接口。
5. 第二章 README：不理解工具内容块时再回去复习。

## Agent 和 Bridge 有什么区别

可以把它们想成“厨师”和“服务员”：

| 部件 | 职责 | 不应该做什么 |
| --- | --- | --- |
| `Agent` | 思考流程：历史、模型、工具、结果 | 不读取 stdin，不知道 Electron |
| `BridgeApplication` | 协议翻译：命令和信封 | 不调用 LLM，不执行工具 |
| `BridgeServer` | stdin/stdout 收发 JSON | 不理解聊天业务 |
| `Renderer` | 展示文字和工具卡片 | 不理解 Agent 内部轮次 |

这样以后即使把 Electron 换成网页或终端，`Agent.run()` 仍然可以原样使用。

## 一条消息现在怎样跑

先看全貌：

```text
用户输入
  → Electron 发送 chat.send
  → BridgeApplication 拆出文字
  → Agent.run(文字)
  → ConversationManager 生成历史
  → LLM 流式返回文字或 tool_use
  → Agent 执行工具并生成 AgentEvent
  → Bridge 把 AgentEvent 包装成 Envelope
  → Electron 根据事件更新界面
```

按代码顺序展开：

1. `BridgeServer` 从 stdin 读到一行 `chat.send` JSON。
2. `BridgeApplication.handle()` 取出其中的 `text`。
3. Bridge 在聊天锁内调用 `self._agent.run(user_text)`。
4. `Agent.run()` 把用户消息加入 `ConversationManager`。
5. Agent 从注册中心取得工具定义，然后调用 `LLMClient.stream()`。
6. 模型每返回一段文字，Agent 立即产生 `stream_text` 事件。
7. 模型返回 `tool_use` 时，Agent 找到工具并执行。
8. Agent 产生 `tool_result`，再把结果交给 LLM 生成最终文字。
9. Agent 最后产生 `turn_complete`。
10. Bridge 只为每个事件补上 `request_id`、`sequence` 和时间戳。
11. Electron 收到信封并更新 reducer，Agent 完全不知道页面长什么样。

最核心的伪代码：

```python
async for agent_event in agent.run(用户文字):
    envelope = 给事件加上请求编号和顺序
    发送给 Electron
```

## AgentEvent 是什么

事件表示“刚刚发生了一件事”。现在有：

| 事件 | 含义 |
| --- | --- |
| `stream_text` | 模型又输出了一小段文字 |
| `tool_use` | 模型请求调用工具 |
| `tool_result` | 工具执行完成或失败 |
| `usage` | Token 用量有更新 |
| `turn_complete` | 当前用户消息处理结束 |
| `error` | 出现可展示给用户的错误 |

`AgentEvent` 不包含 Electron 的请求编号和事件序号。这两个字段属于通信协议，由 Bridge 添加。

## 为什么继续使用手写流程

官方 SDK 有自动 Tool Runner，但霁雪后面需要破坏性操作确认、用户取消、并发分批和自定义 UI 事件，所以保留手写流程更容易控制。`Codex-api` 技能也要求手写流程必须保留完整 `tool_use` 块，并使用匹配的 `tool_use_id` 返回结果；当前代码继续遵守这两点。

## 启动和手动测试

在项目根目录运行：

```powershell
npm run dev
```

Fake 模式先发送普通消息，确认流式聊天正常；再发送：

```text
/read README.md
```

正常现象：

1. 普通回复仍然逐段显示。
2. `read_file` 工具卡片出现并完成。
3. 模型根据工具结果生成最终 Markdown。
4. 页面仍可上下滚动。

这一步是内部重构，因此界面表现应当和第二章结束时完全一样。

自动测试：

```powershell
npm run test:all
conda run --no-capture-output -n mycoder ruff check .
conda run --no-capture-output -n mycoder mypy src tests
npm run test:electron
```

## 常见坑

- 在 `agent.py` 导入 `anthropic`：会破坏供应商隔离，SDK 只能留在适配器。
- 在 Bridge 中重新加入工具逻辑：职责会再次混在一起。
- AgentEvent 直接携带 `request_id`：这会让 Agent 依赖某一种 UI 协议。
- 误以为现在已有循环：当前 `range(2)` 仍是第二章的一次工具往返。
- 重构后工具结果 ID 不一致：`tool_result.tool_use_id` 必须对应原请求。

## 本章变更记录

- 第 1 步：新增 `src/jixue/agent.py` 和 AgentEvent。
- 第 1 步：BridgeApplication 精简为协议转发层。
- 第 1 步：保持聊天、工具、Token 和错误行为不变。

## 自测题与答案

**问：现在真正的 Agent 在哪里？**

答：`src/jixue/agent.py` 中的 `Agent` 类。

**问：BridgeApplication 还是 Agent 吗？**

答：不是。它只负责把 Electron 命令交给 Agent，再包装 Agent 返回的事件。

**问：为什么 Agent 使用异步事件流，而不是最后一次性返回结果？**

答：模型和工具可能运行很久。事件流让 UI 立即显示文字、工具进度和 Token，不需要盯着空白页面等待。

**问：这一步完成 Agent Loop 了吗？**

答：没有。它只是把核心放到正确位置。下一步才会把固定两次请求改成带停止条件的循环。
