# 第一章：一条消息的最短全链路

> 只看这份文件，就能知道消息怎样从输入框到 LLM，再流式返回页面。
> 这里只保留核心代码或伪代码。

## 1. 先看结论

当前实现的是一轮聊天，还不是真正的 Agent Loop：

```text
用户输入
  → Electron 把消息交给 Python
  → Python 整理完整对话历史
  → LLM 流式返回文字
  → Electron 把文字显示出来
```

第三章才会增加“调用工具后自动继续问模型”的 Agent Loop。

## 2. 整条链路

```text
① App.tsx
   用户点击发送，创建 requestId
        ↓
② chatReducer
   立即显示用户消息和空白 AI 消息
        ↓
③ Preload → Electron Main → PythonBridge
   校验消息，写成一行 NDJSON
        ↓
④ BridgeServer
   从 Python stdin 读出 chat.send
        ↓
⑤ BridgeApplication
   加入用户消息，准备完整历史，调用 LLM
        ↓
⑥ ConversationManager → LLMClient
   内部 Message 变成 role + content，LLM 开始流式输出
        ↓
⑦ BridgeApplication → Python stdout → Electron
   每个文字片段变成 stream_text 事件
        ↓
⑧ chatReducer → MessageView
   追加文字；完成后渲染 Markdown
```

下面只解释这 8 个节点。

## 3. ①②：点击发送并更新页面

文件：

- `apps/desktop/src/renderer/src/App.tsx`
- `apps/desktop/src/renderer/src/state.ts`

`sendMessage()` 的核心：

```ts
text = 输入框内容.trim()
requestId = 新 ID

dispatch(request_started)
await window.jixue.sendChat(requestId, text)
```

`request_started` 让 reducer 立即创建两条 UI 消息：

```text
user      "你好"   complete
assistant ""       streaming
```

`chatReducer` 就是“旧状态 + 发生的事件 → 新状态”：

| action | 它只做什么 |
| --- | --- |
| `request_started` | 加入用户消息和空白 AI 消息 |
| `text_received` | 给 AI 消息追加一段文字 |
| `usage_received` | 更新 Token |
| `request_completed` | 标记完成，保存耗时和模型名 |
| `request_failed` | 标记失败，恢复输入框 |

reducer 不调用模型，也不操作 Python。

## 4. ③④：Electron 把消息交给 Python

涉及文件：

- `apps/desktop/src/preload/index.ts`
- `apps/desktop/src/main/index.ts`
- `apps/desktop/src/main/bridge-process.ts`
- `src/jixue/bridge/server.py`

核心伪代码：

```text
Renderer
  → preload.sendChat()
  → Main 校验 requestId 和 text
  → PythonBridge.sendChat()
```

`PythonBridge` 生成命令：

```json
{
  "type": "chat.send",
  "request_id": "req_123",
  "payload": {"text": "你好"}
}
```

然后写入 Python：

```ts
python.stdin.write(JSON.stringify(command) + "\n")
```

`BridgeServer` 按行读取：

```python
line = await stdin.readline()
command = Envelope.from_json_line(line)
await dispatch(command)
```

换行表示“一条命令结束”。`BridgeServer` 只负责运输，不处理聊天业务。

## 5. ⑤⑥：Python 准备历史并调用 LLM

涉及文件：

- `src/jixue/bridge/application.py`
- `src/jixue/domain/conversation.py`
- `src/jixue/llm/base.py`

`BridgeApplication._run_chat()` 是第一章最重要的函数：

```python
conversation.add_user(text)
api_messages = conversation.to_api_format()

async for event in llm.stream(api_messages):
    if event 是 TEXT:
        保存文字片段
        yield stream_text

    if event 是 USAGE:
        yield usage

    if event 是 COMPLETE:
        保存完整 assistant 消息
        yield turn_complete
```

`to_api_format()` 把内部消息：

```text
Message(id, role, content, status, timestamp, usage)
```

转换成 LLM 需要的：

```text
APIMessage(role, content)
```

转换时只保留 `complete`，并检查角色顺序。

`LLMClient` 的核心合同：

```python
async def stream(
    messages: Sequence[APIMessage],
) -> AsyncIterator[LLMStreamEvent]
```

运行时可以注入：

- `FakeLLMClient`：离线固定回复。
- `AnthropicLLMClient`：适配器内部调用 DeepSeek Anthropic 协议。

上层永远只认识 `LLMClient`，不认识供应商 SDK。

## 6. ⑦⑧：流式事件回到页面

一段模型文字会变成：

```json
{
  "type": "stream_text",
  "request_id": "req_123",
  "sequence": 0,
  "payload": {
    "message_id": "msg_123",
    "text": "你"
  }
}
```

返回路线：

```text
BridgeApplication
  → BridgeServer 写入 stdout
  → PythonBridge 按换行读取
  → Electron Main
  → Preload
  → App.handleBridgeEvent()
  → dispatch(text_received)
  → chatReducer 追加文字
```

模型返回十个片段，reducer 就追加十次，所以页面看起来像逐字生成。

收到 `turn_complete` 后：

```text
消息状态：streaming → complete
显示方式：纯文本 → Markdown
```

生成中不渲染 Markdown，是因为标题或代码块可能只到一半。

## 7. 第二轮为什么记得第一轮

LLM API 不保存历史，是霁雪每次重新发送完整列表。

第一轮：

```text
[user1]
```

第一轮完成后，assistant1 被保存。第二轮：

```text
[user1, assistant1, user2]
```

这就是多轮对话的核心。

如果回复中途断开，它会以 `failed` 状态留在内部历史供 UI 复盘，但
`to_api_format()` 会过滤它，不让半句话污染下一轮。

## 8. 只按这个顺序读源码

1. `App.tsx`：`sendMessage()`。
2. `state.ts`：`chatReducer()`。
3. `bridge-process.ts`：`sendChat()`。
4. `server.py`：`_dispatch()`。
5. `application.py`：`_run_chat()`。
6. `conversation.py`：`to_api_format()`。
7. `fake.py` 或 `anthropic_client.py`：`stream()`。
8. 回到 `state.ts`：`text_received` 和 `request_completed`。

最后只要能复述这句话，就已经掌握第一章主干：

> UI 把 `chat.send` 写给 Python；Python 清洗完整历史后调用 LLM；LLM 的文字、用量和
> 完成事件再返回 reducer，流式文字不断追加，完成后渲染 Markdown。
