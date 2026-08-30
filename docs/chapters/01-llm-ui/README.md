# 第 1 章：一条消息是怎么跑起来的

> 适合读者：几乎零基础，已经知道第 0 章中的 Renderer、Preload、Main、Python Bridge 分别是什么。

> 当前进度：本章只完成了 FakeLLM 流式链路。真实 DeepSeek、配置加载、完整多轮历史和模型选择还没有实现，后文会明确区分“现在已有”和“以后再做”。

## 1. 本章最终要回答的问题

假设你在输入框中写下：

```text
你好
```

然后按 Enter。屏幕上很快出现：

```markdown
## 霁雪已经醒来

我收到了你的消息：**你好**
```

看起来只是“输入一句，返回一句”，内部实际经过了十多个步骤：

```text
输入框
  → React 状态
  → Preload
  → Electron Main
  → Python 子进程 stdin
  → BridgeServer
  → BridgeApplication
  → FakeLLM
  → Python stdout
  → Electron Main
  → Preload
  → React reducer
  → 流式纯文本
  → 完成后的 Markdown
```

本章会把这条链路逐步拆开。读完后你应该可以：

1. 指出一句用户消息在哪些文件中经过。
2. 解释 `request_id`、`sequence` 和 `message_id` 的区别。
3. 解释为什么一个回复会产生很多 `stream_text` 事件。
4. 解释为什么生成中显示纯文本，完成后才渲染 Markdown。
5. 知道当前 FakeLLM 和以后真实 DeepSeek 的替换位置。

## 2. 推荐阅读顺序

### 第一轮：只理解链路，不看实现细节

1. 先读第 3 节术语表。
2. 再读第 4 节全景图。
3. 跟着第 5—8 节走完“你好”的完整生命周期。
4. 最后读第 11 节，确认哪些部分还是假的。

第一轮不要停下来研究每一行语法。目标只是知道数据从哪里来、去了哪里。

### 第二轮：按数据经过的顺序读源码

| 顺序 | 文件 | 重点函数或位置 | 只回答什么问题 |
| --- | --- | --- | --- |
| 1 | `apps/desktop/src/renderer/src/App.tsx` | `sendMessage()` | 点击发送后第一步做什么？ |
| 2 | `apps/desktop/src/renderer/src/state.ts` | `request_started` | 为什么立刻出现用户消息和空白 AI 消息？ |
| 3 | `apps/desktop/src/preload/index.ts` | `sendChat` | 页面怎样跨过安全边界？ |
| 4 | `apps/desktop/src/main/index.ts` | `jixue:send-chat` | Main 做了哪些输入校验？ |
| 5 | `apps/desktop/src/main/bridge-process.ts` | `sendChat()` | JavaScript 对象怎样变成 NDJSON？ |
| 6 | `src/jixue/bridge/server.py` | `run()`、`_dispatch()` | Python 怎样读到并分发命令？ |
| 7 | `src/jixue/bridge/application.py` | `_handle_chat()` | 命令怎样变成 LLM 调用和 UI 事件？ |
| 8 | `src/jixue/llm/base.py` | `LLMClient`、`LLMStreamEvent` | 上层要求模型客户端提供什么？ |
| 9 | `src/jixue/llm/fake.py` | `stream()` | FakeLLM 怎样模拟流式回复？ |
| 10 | 回到 `bridge-process.ts` | `consumeStdout()`、`consumeLine()` | 返回事件怎样进入 Main？ |
| 11 | 回到 `preload/index.ts` | `onBridgeEvent` | 返回事件怎样进入 Renderer？ |
| 12 | 回到 `App.tsx` | `handleBridgeEvent()`、`MessageView` | 文本怎样显示并最终变成 Markdown？ |
| 13 | 回到 `state.ts` | `text_received`、`request_completed` | 每个事件怎样修改 UI 状态？ |

### 第三轮：带着问题读

建议在源码全局搜索以下字符串：

1. 搜索 `chat.send`，找到命令的发送端和接收端。
2. 搜索 `stream_text`，找到事件的生成端和消费端。
3. 搜索 `turn_complete`，找到 Markdown 从纯文本切换到渲染态的位置。
4. 搜索 `request_id`，观察它怎样贯穿整个请求。

跨语言项目最有效的阅读方法通常不是“按目录从头看”，而是跟踪同一个协议字段或事件类型。

## 3. 零基础术语表

### 3.1 UI、组件和状态

UI 就是用户看到的界面。React 把界面拆成组件，例如：

- `App`：整个应用。
- `MessageView`：一条消息。
- 输入框、发送按钮、侧栏也都是界面的一部分。

状态是“界面当前记住的数据”，例如：

- 输入框里有什么字。
- 当前有几条消息。
- AI 是否还在回复。
- Token 是多少。

状态变化后，React 会重新计算并更新需要变化的界面。

### 3.2 reducer、action 和 dispatch

这三个词第一次看很抽象，可以用“记账员”理解：

- action：一张业务单据，例如“请求开始了”“收到一段文字”。
- dispatch：把单据交给记账员。
- reducer：记账员根据旧账本和单据，生成一本新账本。

例子：

```ts
dispatch({ type: 'text_received', text: '霁' })
```

reducer 收到后，把 `霁` 追加到对应 assistant 消息末尾，React 再显示新内容。

### 3.3 同步、异步和 `await`

同步可以理解为“做完这一步才做下一步”。异步是“这件事需要等待，但等待期间程序还能处理其他事情”。

模型回复、进程通信都不是立即完成，因此代码会使用 `async`、`await` 和异步迭代。你现在不需要掌握语法细节，只需要知道：

```ts
await window.jixue.sendChat(...)
```

表示发送命令可能需要等待；界面不会因此冻结整个进程。

### 3.4 LLM、客户端和适配器

- LLM：Large Language Model，大语言模型。
- LLM 客户端：项目中负责向模型发送请求、接收结果的对象。
- 适配器：把某个供应商的具体格式转换成霁雪自己的格式。

现在用的是 `FakeLLMClient`。它不是 AI，不访问网络，只按固定规则返回一段文字。以后真实 DeepSeek 会替换这里，但上层仍然只依赖 `LLMClient`。

### 3.5 流式响应

非流式响应像等厨师把所有菜做好再一次端上桌；流式响应像做好一道就先上一道。

模型生成一句长回复时，不需要等全部完成，可以不断产生小片段：

```text
第 1 段：##
第 2 段： 霁
第 3 段：雪已经
第 4 段：醒来
```

UI 每收到一段就追加，因此用户能立刻看到回复在增长。

### 3.6 Markdown

Markdown 是一种纯文本标记：

```markdown
## 标题
**加粗**
- 列表项
`代码`
```

生成中可能只收到半个 `**` 或半个代码块，所以霁雪在流式阶段只显示原文，完成后再一次性渲染。

### 3.7 Token

Token 是模型处理文本时使用的计量单位，不等于字符数，也不等于单词数。当前 FakeLLM 没有真实 tokenizer，只用“约 4 个字符算 1 Token”的方式估算，用来验证 UI 能否显示用量。

## 4. 一条消息的全景图

下面的每一条箭头都对应真实代码：

```text
你在输入框输入“你好”
        ↓
App.sendMessage() 创建 request_id
        ↓
dispatch(request_started) 让 UI 先显示用户消息和空 AI 消息
        ↓
window.jixue.sendChat(request_id, "你好")
        ↓
Preload 调用 ipcRenderer.invoke("jixue:send-chat")
        ↓
Main 校验 request_id 和文本
        ↓
PythonBridge.sendChat() 创建 chat.send 信封
        ↓
writeEnvelope() 把一行 NDJSON 写入 Python stdin
        ↓
BridgeServer.run() 读取并解析信封
        ↓
BridgeApplication._handle_chat() 调用 LLMClient.stream()
        ↓
FakeLLMClient.stream() 不断 yield 文本小片段
        ↓
BridgeApplication 把每段变成 stream_text 信封
        ↓
BridgeServer 把每个信封写入 stdout
        ↓
PythonBridge.consumeStdout() 按换行拼出事件
        ↓
Main → Preload → App.handleBridgeEvent()
        ↓
dispatch(text_received) 追加到 assistant 消息
        ↓
React 重新渲染，屏幕上多出几个字
        ↓
usage 更新 Token
        ↓
turn_complete 把消息标记为完成
        ↓
MessageView 用 ReactMarkdown 渲染最终内容
```

为了便于理解，可以把链路分成四段：

1. Renderer 发送消息。
2. Main 把消息送进 Python。
3. Python 调用 FakeLLM 并产生事件。
4. 事件原路返回，React 更新界面。

## 5. 第一段：消息从输入框离开 Renderer

下面用用户输入“你好”作为固定例子。

### 第 1 步：输入框先把文字存在 React 状态中

文件：`apps/desktop/src/renderer/src/App.tsx`

输入框的 `value` 来自 `input`：

```ts
const [input, setInput] = useState('')
```

每次键盘输入，`onChange` 调用 `setInput()`。因此按下发送前，`input` 的值就是：

```text
你好
```

这时文字只在 Renderer 内存中，还没有进入 Main，更没有到 Python。

### 第 2 步：点击发送或按 Enter

发送按钮和 Enter 最终都会调用：

```ts
sendMessage()
```

它先执行：

```ts
const text = input.trim()
```

`trim()` 会去掉首尾多余空白。如果结果为空，就不发送。

`canSend` 还会检查：

- Bridge 必须是 ready。
- 当前没有另一个请求在运行。
- 输入不能是空字符串。

### 第 3 步：创建 `request_id`

```ts
const requestId = `req_${crypto.randomUUID()}`
```

它可能长这样：

```text
req_4c57d1e2-5c07-4d5c-a88c-4a1c65b1e9ad
```

为什么需要它？因为将来可能同时存在多个请求。每个返回事件都带同一个 `request_id`，UI 才知道应该把文本追加到哪一次请求。

### 第 4 步：UI 先更新，不等后端返回

`sendMessage()` 立即派发：

```ts
dispatch({
  type: 'request_started',
  requestId,
  text,
  startedAt: Date.now()
})
```

文件：`apps/desktop/src/renderer/src/state.ts`

`chatReducer()` 收到 `request_started` 后添加两条内部 UI 消息：

```text
user 消息：内容是“你好”，状态 complete
assistant 消息：内容为空，状态 streaming
```

为什么提前创建空 assistant 消息？后面每个文本增量可以直接找到它并追加，不必等第一段到达时才临时创建。

同时：

- `activeRequestId` 记录正在运行的请求。
- `startedAt` 记录开始时间。
- 输入框被 `setInput('')` 清空。
- 发送按钮暂时禁用，防止当前版本并行发送。

### 第 5 步：调用 Preload 暴露的方法

Renderer 执行：

```ts
await window.jixue.sendChat(requestId, text)
```

注意，Renderer 没有直接导入 Electron，也没有调用 Python。它只认识 `window.jixue`。

文件：`apps/desktop/src/preload/index.ts`

Preload 把这个调用翻译成：

```ts
ipcRenderer.invoke('jixue:send-chat', requestId, text)
```

IPC 是 Inter-Process Communication，意思是进程间通信。这里是 Renderer 向 Electron Main 发消息。

## 6. 第二段：Main 把消息送进 Python

### 第 6 步：Main 校验 Renderer 输入

文件：`apps/desktop/src/main/index.ts`

Main 注册了：

```ts
ipcMain.handle('jixue:send-chat', ...)
```

收到调用后先检查：

1. 发送者是不是当前主窗口。
2. `requestId` 是否符合 `req_...` 格式。
3. 文本是否为字符串。
4. 文本是否非空且不超过 20,000 个字符。

为什么 Renderer 已经检查过，Main 还要再检查？因为安全边界不能相信高权限边界外传来的参数。页面检查是为了用户体验，Main 检查才是权限边界的防守。

校验通过后调用：

```ts
bridge?.sendChat(requestId, text)
```

### 第 7 步：`PythonBridge.sendChat()` 创建业务信封

文件：`apps/desktop/src/main/bridge-process.ts`

首先确认 Bridge 已经 ready。然后创建 JavaScript 对象：

```json
{
  "version": 1,
  "type": "chat.send",
  "request_id": "req_4c57...",
  "sequence": 0,
  "timestamp": "2026-08-30T12:00:00.000Z",
  "payload": {
    "session_id": "session_dev",
    "text": "你好",
    "model_id": "fake"
  }
}
```

字段解释：

| 字段 | 当前例子 | 用途 |
| --- | --- | --- |
| `version` | `1` | 协议版本，防止两端格式不兼容 |
| `type` | `chat.send` | 告诉 Python 这是发送聊天命令 |
| `request_id` | `req_4c57...` | 串起这一次请求的所有事件 |
| `sequence` | `0` | 发送命令当前只有一条，所以从 0 开始 |
| `timestamp` | UTC 时间 | 记录事件创建时刻 |
| `payload` | 文本、会话、模型 | 真正的业务内容 |

### 第 8 步：对象变成一行 NDJSON

`writeEnvelope()` 执行：

```ts
this.child.stdin.write(`${JSON.stringify(envelope)}\n`, 'utf8')
```

此时对象变成一行文本，经由 Python 进程的 stdin 进入后端。

## 7. 第三段：Python 调用 FakeLLM 并产生事件

### 第 9 步：`BridgeServer` 读取一整行

文件：`src/jixue/bridge/server.py`

`run()` 中的 `readline()` 等到换行符，得到完整 `chat.send` JSON。单行如果超过 1 MiB 会被拒绝。

接着：

```python
command = Envelope.from_json_line(line)
```

把 JSON 文本解析为 Python `Envelope`，并检查所有必需字段。

### 第 10 步：为命令创建异步任务

`BridgeServer` 调用：

```python
task = asyncio.create_task(self._dispatch(command))
```

你可以把 task 理解为“一份正在后台执行的工作”。这样 Bridge 仍然可以继续读取下一行，而不必停在当前请求直到所有回复结束。

### 第 11 步：`BridgeApplication` 识别 `chat.send`

文件：`src/jixue/bridge/application.py`

`handle()` 根据命令类型分支：

```python
if command.type == "chat.send":
    async for event in self._handle_chat(command):
        yield event
```

`yield` 可以先简单理解为“产生一个结果，但函数还没结束，以后还会继续产生更多结果”。这正适合流式事件。

### 第 12 步：检查文本并准备元数据

`_handle_chat()` 先确认 `payload.text` 是非空字符串，然后创建：

- `sequence = 0`：第一条返回事件的序号。
- `message_id = msg_随机值`：这一条 assistant 消息的 ID。
- `started_at`：用于计算总耗时。

三个 ID/序号不要混淆：

| 名称 | 标识什么 | 生命周期 |
| --- | --- | --- |
| `request_id` | 用户按一次发送产生的完整请求 | 从 Renderer 一直贯穿到最后 |
| `message_id` | 本轮产生的 assistant 消息 | 所有文本块共享同一个值 |
| `sequence` | 同一请求中事件的先后顺序 | 每发一个事件加 1 |

### 第 13 步：通过自己的接口调用模型

文件：`src/jixue/llm/base.py`

领域层规定所有模型客户端都要提供：

```python
def stream(self, prompt: str) -> AsyncIterator[LLMStreamEvent]
```

`BridgeApplication` 只调用这个接口：

```python
async for llm_event in self._llm.stream(text):
```

它不知道 `_llm` 是 FakeLLM、DeepSeek 还是别的供应商。这就是“暴露领域语义，隐藏实现细节”。

### 第 14 步：FakeLLM 生成固定回复

文件：`src/jixue/llm/fake.py`

`FakeLLMClient.stream("你好")` 先构造完整文本：

```markdown
## 霁雪已经醒来

我收到了你的消息：**你好**

- Python Bridge 正常
- NDJSON 事件流正常
- Electron 可以继续接收下一轮消息

当前使用的是 `FakeLLM`，所以不会产生 API 费用。
```

它再按照循环块长：

```python
chunk_sizes = (1, 2, 5, 3, 8)
```

切成许多不规则片段。每段之间等待约 0.025 秒，然后产生：

```python
LLMStreamEvent(type=LLMEventType.TEXT, text=chunk)
```

不规则切分很重要，因为真实 API 也不会保证每一段正好是完整汉字、单词或 Markdown 结构。

### 第 15 步：LLM 事件变成 Bridge 事件

FakeLLM 产生的是 Python 内部的 `LLMStreamEvent`。它不能直接发给 Electron。

`BridgeApplication` 把每个文本事件转换成统一 `Envelope`：

```json
{
  "type": "stream_text",
  "request_id": "req_4c57...",
  "sequence": 0,
  "payload": {
    "text": "#",
    "message_id": "msg_a1b2..."
  }
}
```

下一段可能是：

```json
{
  "type": "stream_text",
  "request_id": "req_4c57...",
  "sequence": 1,
  "payload": {
    "text": "# ",
    "message_id": "msg_a1b2..."
  }
}
```

每产生一条，`sequence += 1`。

### 第 16 步：Server 立即写入 stdout

`BridgeServer._write_event()` 把每个信封序列化成一行 JSON，写入 stdout 并 `flush()`。

因此不是等 FakeLLM 全部完成后一次返回，而是每个 chunk 产生后就立即返回。

## 8. 第四段：事件返回 UI 并逐字显示

### 第 17 步：Main 缓冲 stdout

文件：`apps/desktop/src/main/bridge-process.ts`

`consumeStdout(chunk)` 中的 `chunk` 是操作系统交给 Node 的任意文本片段，不一定等于一个 LLM chunk，也不一定等于一行 NDJSON。

所以 Main 必须：

1. 把它追加到 `stdoutBuffer`。
2. 搜索换行符。
3. 每找到完整一行就取出来。
4. 剩下的半行继续放在 buffer，等下次数据。

这是“传输分片”和“模型文本分片”的区别：

- 模型分片由 FakeLLM/真实 SDK 决定。
- 传输分片由操作系统管道决定。
- NDJSON 换行符让 Main 可以恢复完整事件。

### 第 18 步：Main 解析并转发事件

`consumeLine()`：

1. 使用 `JSON.parse()` 恢复对象。
2. 使用 `isBridgeEnvelope()` 验证结构。
3. 通知 Main 注册的业务事件监听器。

`index.ts` 再调用：

```ts
sendToRenderer('jixue:bridge-event', event)
```

### 第 19 步：Preload 把事件交给页面回调

文件：`apps/desktop/src/preload/index.ts`

Preload 监听 `jixue:bridge-event`，然后执行 Renderer 之前注册的 listener。

Renderer 看起来只是使用：

```ts
window.jixue.onBridgeEvent((event) => handleBridgeEvent(event))
```

它不需要知道底层使用 Electron IPC、stdout 或 Python。

### 第 20 步：`handleBridgeEvent()` 识别文本事件

文件：`apps/desktop/src/renderer/src/App.tsx`

收到 `stream_text` 后派发：

```ts
dispatch({
  type: 'text_received',
  requestId: event.request_id,
  messageId: event.payload.message_id,
  text: event.payload.text
})
```

### 第 21 步：reducer 把文本追加到正确消息

文件：`apps/desktop/src/renderer/src/state.ts`

`text_received` 分支遍历现有消息，找到同时满足下面两个条件的消息：

- `message.requestId === action.requestId`
- `message.role === 'assistant'`

然后执行逻辑等价于：

```text
新内容 = 旧内容 + 新片段
```

例如：

```text
旧内容：## 霁雪
新片段：已经
新内容：## 霁雪已经
```

React 得到新状态后自动重新渲染。这个过程对每个 `stream_text` 重复一次，所以看起来像文字正在生成。

### 第 22 步：流式阶段只显示原文

`MessageView` 检查：

```ts
message.status === 'streaming'
```

如果还在生成，使用 `<pre>` 显示原始字符串，并显示闪烁光标。

此时即使内容开头是 `##`，也暂时不会变成真正标题。

## 9. 回复怎样结束

文本全部发送后，FakeLLM 还会产生两类事件。

### 第 23 步：产生 usage

FakeLLM 用字符数估算：

```python
input_tokens = max(1, (len(prompt) + 3) // 4)
output_tokens = max(1, (len(response) + 3) // 4)
```

假设这一次估算得到 1 个输入 Token、42 个输出 Token，`BridgeApplication` 会把它转换成：

```json
{
  "type": "usage",
  "payload": {
    "turn": {
      "input_tokens": 1,
      "output_tokens": 42
    },
    "cumulative": {
      "input_tokens": 1,
      "output_tokens": 42
    }
  }
}
```

Renderer 派发 `usage_received`，reducer 更新输入/输出 Token，输入框下方的状态随之变化。

### 第 24 步：产生 turn_complete

FakeLLM 最后产生内部 `COMPLETE`。Bridge 返回：

```json
{
  "type": "turn_complete",
  "payload": {
    "turn_index": 1,
    "stop_reason": "end_turn",
    "duration_ms": 856,
    "model": "fake-jixue",
    "message_id": "msg_a1b2..."
  }
}
```

字段解释：

- `turn_index`：当前是第几次模型调用，现阶段固定为 1。
- `stop_reason`：模型为什么停下，`end_turn` 表示自然完成。
- `duration_ms`：从 Python 开始处理到完成的毫秒数。
- `model`：实际使用的模型显示名。

### 第 25 步：UI 把消息标记为完成

`handleBridgeEvent()` 派发 `request_completed`。reducer：

- 把 `activeRequestId` 设为 null。
- 把 `startedAt` 设为 null，停止实时计时。
- 保存最终耗时和模型名。
- 把 assistant 消息状态从 `streaming` 改为 `complete`。

### 第 26 步：完成后渲染 Markdown

`MessageView` 重新渲染时发现消息不再是 streaming，于是使用：

```tsx
<ReactMarkdown remarkPlugins={[remarkGfm]}>
  {message.content}
</ReactMarkdown>
```

这时：

- `##` 变成标题。
- `**你好**` 变成粗体。
- `-` 开头的行变成列表。
- 反引号内容变成行内代码。

完整请求到这里结束，发送按钮重新可用。

## 10. 三种“事件”为什么不直接用同一个类型

读代码时你会看到三层名字，很容易混淆：

| 层次 | 例子 | 使用范围 | 为什么存在 |
| --- | --- | --- | --- |
| LLM 领域事件 | `TEXT`、`USAGE`、`COMPLETE` | Python 模型层内部 | 隐藏供应商 SDK |
| Bridge 信封事件 | `stream_text`、`usage`、`turn_complete` | Python 和 Electron 之间 | 跨进程传输、带请求 ID 和序号 |
| React action | `text_received`、`usage_received`、`request_completed` | Renderer 内部 | 清楚表达 UI 要怎样改状态 |

例如一段文字会经历：

```text
LLMStreamEvent(TEXT, "霁雪")
  ↓ BridgeApplication 转换
Envelope(type="stream_text", payload.text="霁雪")
  ↓ App.handleBridgeEvent 转换
ChatAction(type="text_received", text="霁雪")
  ↓ chatReducer
assistant.content += "霁雪"
```

这种转换看起来多一层，但它让每一层只使用自己的语言。将来 Anthropic SDK 改事件结构时，只需要修改适配器，不需要修改 React reducer。

## 11. 哪些是真的，哪些还是模拟的

### 当前已经真实运行的部分

- Electron Main、Preload、Renderer 是真实进程边界。
- Conda `mycoder` 中的 Python 子进程真实启动。
- stdin/stdout NDJSON 通信真实运行。
- 流式片段真实逐条传输。
- React 状态更新、耗时和 Markdown 渲染真实运行。
- 关闭窗口和回收 Bridge 真实运行。

### 当前仍然是模拟的部分

- `FakeLLMClient` 不是真实 AI，只返回固定模板。
- Token 是字符数估算，不是供应商准确用量。
- `session_id` 目前固定为 `session_dev`。
- `model_id` 目前固定为 `fake`。
- 每次只把当前用户文本发给 FakeLLM，没有附带完整历史。
- 左侧“新任务”目前只是界面占位，没有创建会话功能。

因此现在连续发送两条消息，只证明“可以连续请求”，不代表第二次请求知道第一次聊过什么。真正多轮对话要等 `ConversationManager`。

## 12. 真实 DeepSeek 接入后，哪里会变

当前核心调用是：

```python
BridgeApplication(FakeLLMClient())
```

以后会由配置和工厂创建 Anthropic 协议适配器，例如概念上变成：

```text
BridgeApplication(AnthropicLLMClient(config))
```

适配器内部才允许导入 `anthropic` SDK，并负责：

1. 用 `protocol/model/base_url/api_key` 创建 SDK 客户端。
2. 把霁雪消息转换成 Anthropic API 请求。
3. 把 SDK 流事件转换成 `LLMStreamEvent`。
4. 把 SDK 异常转换成霁雪可以理解的错误。

下面这些上层代码不应该改变：

- `BridgeApplication` 仍然调用 `LLMClient.stream()`。
- Bridge 仍然发送 `stream_text/usage/turn_complete`。
- Main 和 Preload 仍然转发同一协议。
- React reducer 仍然处理同一组 action。

这就是封装供应商 SDK 的意义。

## 13. 如何亲手跟踪一条消息

### 手动观察

1. 执行 `npm run dev`。
2. 等待左下角显示 Bridge 在线。
3. 输入“你好”。
4. 按 Enter。
5. 观察用户气泡立即出现。
6. 观察 assistant 文本逐渐增长，此时能看到原始 Markdown。
7. 观察完成后标题、列表和粗体被渲染。
8. 观察 Token 和耗时停止变化。
9. 关闭窗口，确认没有错误弹窗。

### 用搜索跟踪代码

在编辑器中全局搜索：

```text
chat.send
```

你会找到 JavaScript 发送端和 Python 接收端。然后搜索：

```text
stream_text
```

你会找到 Python 生成端和 React 消费端。最后搜索：

```text
turn_complete
```

观察完成事件怎样把 `streaming` 改成 `complete`。

### 使用本地自动化测试

```powershell
npm run test:all
npm run test:electron
```

`test:electron` 会真实启动窗口、发送消息、检查界面、关闭窗口，并检查主进程退出错误。测试源码只保留在本机，不进入 Git。

## 14. 出错时从哪里查

| 现象 | 先看哪里 | 可能原因 |
| --- | --- | --- |
| 左下角一直显示正在连接 | `bridge-process.ts` 的 `start()` | Conda、Python 模块或握手失败 |
| 点击发送没有用户气泡 | `App.tsx` 的 `sendMessage()` | `canSend` 为 false 或输入为空 |
| 有用户气泡但 Python 没反应 | Main 的 `jixue:send-chat` 和 `writeEnvelope()` | IPC 校验失败或 stdin 不可写 |
| Python 报 JSON 错误 | `events.py` 的 `from_json_line()` | stdout 被日志污染或信封字段错误 |
| 回复最后一次性出现 | Python `-u`、stdout `flush()` | 输出被缓冲，没有逐事件刷新 |
| 回复有重复或串到别处 | `request_id` 和 reducer 匹配条件 | 事件归属错误 |
| Token 不更新 | `usage` 事件链路 | Python 没发或 Renderer 没处理 usage |
| 最后仍显示原始 `##` | `turn_complete` 链路 | 消息状态没有变成 complete |
| 退出弹 JavaScript 错误 | `sendToRenderer()` 和 `stop()` | 向已销毁窗口发送或关闭竞态 |

排查原则：先确认数据最后成功到达了哪一层，再检查下一条箭头，不要同时修改所有层。

## 15. 本章文件地图

| 文件 | 职责 | 本章最重要的位置 |
| --- | --- | --- |
| `src/jixue/llm/base.py` | 定义自己的模型接口 | `LLMClient`、`LLMStreamEvent` |
| `src/jixue/llm/fake.py` | 模拟流式模型 | `FakeLLMClient.stream()` |
| `src/jixue/bridge/application.py` | 模型事件转 Bridge 事件 | `_handle_chat()` |
| `src/jixue/bridge/server.py` | stdin/stdout 运输 | `run()`、`_write_event()` |
| `apps/desktop/src/main/bridge-process.ts` | Node 与 Python 管道 | `sendChat()`、`consumeStdout()` |
| `apps/desktop/src/main/index.ts` | IPC 校验和转发 | `registerIpc()`、`sendToRenderer()` |
| `apps/desktop/src/preload/index.ts` | Renderer 安全白名单 | `api` 对象 |
| `apps/desktop/src/shared/protocol.ts` | TypeScript 协议类型 | `BridgeEnvelope` |
| `apps/desktop/src/renderer/src/state.ts` | UI 状态变化 | `chatReducer()` |
| `apps/desktop/src/renderer/src/App.tsx` | 输入、事件消费和渲染 | `sendMessage()`、`handleBridgeEvent()`、`MessageView` |

详细目录说明见 `docs/PROJECT_STRUCTURE.md`。

## 16. 从简单到困难的练习

### 练习 1：修改固定回复

在 `fake.py` 的 `response` 中增加一行：

```markdown
- 这是我的第一次修改
```

重启 `npm run dev` 后发送消息，确认新列表项出现。这个练习只改模型实现，不改协议和 UI。

### 练习 2：让流式速度变慢

把 `FakeLLMClient` 的 `chunk_delay` 从 `0.025` 改成 `0.1`。观察生成速度变化。

思考：为什么 Electron、BridgeApplication 和 React 都不需要修改？

### 练习 3：改变分片大小

把：

```python
chunk_sizes = (1, 2, 5, 3, 8)
```

改成：

```python
chunk_sizes = (10,)
```

观察 UI 每次跳出的文字变多，但最终内容相同。

### 练习 4：画出事件序列

假设完整回复被切成三段，自己写出事件顺序，再对照答案：

```text
sequence 0 → stream_text
sequence 1 → stream_text
sequence 2 → stream_text
sequence 3 → usage
sequence 4 → turn_complete
```

## 17. 自测题与答案

1. **用户按发送后，为什么用户气泡能立即出现？** 因为 Renderer 先 dispatch `request_started`，不等待 Python。
2. **哪一层真正校验文本不超过 20,000 字符？** Electron Main 的 IPC handler。
3. **`chat.send` 从哪里进入 Python？** Python 子进程 stdin。
4. **FakeLLM 为什么使用 `yield`？** 为了逐个产生流式事件，而不是一次返回全部内容。
5. **一段 LLM 文本怎样找到正确 UI 消息？** Bridge 事件携带 `request_id` 和 `message_id`，reducer 按 requestId 与 assistant role 匹配。
6. **为什么 `sequence` 不一直是 0？** 它表示同一请求中事件先后顺序，每发一个事件递增。
7. **为什么流式时不用 ReactMarkdown？** Markdown 可能只到半截，频繁解析会闪烁或产生错误结构。
8. **什么时候开始 Markdown 渲染？** 收到 `turn_complete`，reducer 把消息状态设为 complete 后。
9. **当前第二条请求知道第一条消息吗？** 不知道；ConversationManager 尚未实现。
10. **换成 DeepSeek 后 React 是否应该重写？** 不应该；供应商差异应被 LLM 适配器隐藏。

如果第 1—8 题能用自己的话回答，你已经理解当前消息链路。第 9、10 题帮助你区分“当前能力”和“未来设计”。

## 18. 本章下一小步

接下来按这个顺序继续，不进入工具系统：

1. 实现 `LLMConfig` 和 `config/models.yaml` 加载，解释四字段怎样变成客户端。
2. 在适配器内部接入 Anthropic SDK，并用假 SDK 流先测试转换。
3. 使用 DeepSeek Anthropic 端点完成一次真实流式手测。
4. 实现内部消息与 API 消息两层模型。
5. 实现 `ConversationManager.to_api_format()`，让第二次请求携带完整历史。
6. 加入 Flash、Pro、Vision Exp 模型选择。

每完成一步，本章都会增加该能力自己的推荐阅读顺序、端到端链路、输入输出示例、常见错误和手动验证方法。
