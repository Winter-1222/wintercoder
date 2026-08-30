# 第 1 章：一条消息是怎么跑起来的

> 适合读者：几乎零基础，已经知道第 0 章中的 Renderer、Preload、Main、Python Bridge 分别是什么。

> 当前进度：本章已完成 FakeLLM 流式链路、独立模型配置加载和 Anthropic 协议适配器。适配器已用本地假 SDK 验证，但尚未接入 Electron，也没有发起真实 DeepSeek 请求；完整多轮历史和 UI 模型选择仍未实现。后文会一直明确区分“源码已写”“本地已测”“真实网络已验收”。

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
5. 知道当前 FakeLLM、已经写好的 Anthropic 适配器和以后真实 DeepSeek 请求分别位于哪一层。
6. 解释 `models.yaml` 怎样变成只有四个字段的 `LLMConfig`。
7. 解释一段 SDK 文本流怎样变成霁雪的文本、用量和完成事件。
8. 解释为什么 SDK 异常不能直接交给 Bridge 或 React。

## 2. 推荐阅读顺序

### 第一轮：只理解链路，不看实现细节

1. 先读第 3 节术语表。
2. 再读第 4 节全景图。
3. 跟着第 5—8 节走完“你好”的完整生命周期。
4. 最后读第 11 节，确认哪些部分还是假的。
5. 再读第 12 节，单独走一遍“配置文件怎样变成 Python 对象”。

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

配置加载和真实适配器目前还没有插入上面这条 Electron 聊天链路，所以第二轮读完 FakeLLM 消息后，再单独按这个顺序阅读：

| 顺序 | 文件 | 重点位置 | 只回答什么问题 |
| --- | --- | --- | --- |
| 1 | `config/models.yaml` | `default_model`、`models`、`llm` | 人写的三模型配置长什么样？ |
| 2 | `src/jixue/llm/config.py` | `load_model_catalog()` | 默认文件、本地覆盖和环境变量怎样汇合？ |
| 3 | 同一文件 | `_parse_catalog()`、`_parse_llm_config()` | 普通字典怎样经过校验变成领域对象？ |
| 4 | 同一文件 | `LLMConfig` | 为什么真正的适配器最终只看到四个字段？ |
| 5 | `src/jixue/llm/factory.py` | `create_llm_client()` | `protocol` 怎样决定选择哪个适配器？ |
| 6 | `src/jixue/llm/adapters/anthropic_client.py` | `stream()` | SDK 文本流怎样变成霁雪事件？ |
| 7 | 同一文件 | `_get_client()`、`_translate_anthropic_error()` | 为什么延迟创建客户端，SDK 错误又怎样变安全？ |
| 8 | `src/jixue/bridge/application.py` | 两个 `except` 分支 | 领域错误怎样变成 UI 能理解的错误信封？ |

### 第三轮：带着问题读

建议在源码全局搜索以下字符串：

1. 搜索 `chat.send`，找到命令的发送端和接收端。
2. 搜索 `stream_text`，找到事件的生成端和消费端。
3. 搜索 `turn_complete`，找到 Markdown 从纯文本切换到渲染态的位置。
4. 搜索 `request_id`，观察它怎样贯穿整个请求。
5. 搜索 `load_model_catalog`，观察配置入口和每一层校验函数。
6. 搜索 `create_llm_client`，观察协议选择只出现在哪一层。
7. 搜索 `import anthropic`，确认它只出现在适配器文件。
8. 搜索 `cache_control`，找到提示缓存参数真正进入 SDK 请求的位置。

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
- 默认/本地 YAML 合并、环境变量展开和四字段配置校验真实运行。
- Flash、Pro、Vision Exp 三条目录记录能够被真实加载；缺 Key 会得到非致命状态。
- Anthropic 适配器使用真实官方 SDK 接口和类型，但网络流由本地假 SDK 对象模拟。
- SDK 请求参数、流式事件顺序、最终 Token、停止原因和常见类型化异常翻译已经自动验证。

### 当前仍然是模拟的部分

- `FakeLLMClient` 不是真实 AI，只返回固定模板。
- Token 是字符数估算，不是供应商准确用量。
- `session_id` 目前固定为 `session_dev`。
- `model_id` 目前固定为 `fake`。
- 每次只把当前用户文本发给 FakeLLM，没有附带完整历史。
- 左侧“新任务”目前只是界面占位，没有创建会话功能。
- Electron Bridge 还没有从模型目录创建 `AnthropicLLMClient`，所以界面仍不会请求 DeepSeek。
- 还没有用用户自己的 Key 做真实网络手测，也没有观察到真实 Prompt Cache 命中。

因此现在连续发送两条消息，只证明“可以连续请求”，不代表第二次请求知道第一次聊过什么。真正多轮对话要等 `ConversationManager`。

## 12. 模型配置是怎么跑起来的

这一节讲的是本章第二条链路。配置、工厂和适配器已经连在代码层，但还没有接入 Electron 的默认启动入口：

```text
现在的聊天：
Electron → Python Bridge → FakeLLM

现在的配置检查：
models.yaml → 配置加载器 → ModelCatalog → LLMConfig

现在已经完成并用假 SDK 测试：
LLMConfig → 客户端工厂 → AnthropicLLMClient → 霁雪流事件

下一小步要接上的最后一段：
ModelCatalog → 客户端工厂 → AnthropicLLMClient → Python Bridge 启动入口
```

所以配置与适配器都是可运行源码，但 Electron 仍然注入 FakeLLM。这样的分步方式让我们可以先免费验证转换逻辑，再决定何时使用真实 Key。

### 12.1 先认识 YAML、目录和 dataclass

#### YAML 是什么

YAML 是一种适合人手写的配置格式。它用缩进表示层级，例如：

```yaml
default_model: flash
models:
  flash:
    label: Flash
```

可以先把它理解成“比 JSON 少一些括号的键值表”。缩进是语法的一部分，少两个空格或多两个空格都可能改变含义。

#### 这里的“模型目录”不是文件夹

`ModelCatalog` 中文叫“模型目录”，意思是一张可选择模型的清单，不是 Windows 文件夹。它包含：

```text
ModelCatalog
├─ schema_version：配置格式版本
├─ default_model：默认选哪个稳定 ID
└─ models：所有模型
   ├─ flash → ModelDefinition
   ├─ pro → ModelDefinition
   └─ vision_exp → ModelDefinition
```

每个 `ModelDefinition` 再分成两类信息：

| 信息 | 例子 | 谁使用 |
| --- | --- | --- |
| 应用元数据 | label、experimental、capabilities、context_window | UI、上下文管理、功能判断 |
| LLM 连接配置 | protocol、model、base_url、api_key | 客户端工厂和供应商适配器 |

#### dataclass 是什么

Python `@dataclass` 可以理解成“主要用来装数据的类”。例如 `LLMConfig` 定义了四个字段，Python 会帮助它生成初始化方法：

```python
config = LLMConfig(
    protocol="anthropic",
    model="deepseek-v4-flash",
    base_url="https://api.deepseek.com/anthropic",
    api_key=None,
)
```

`frozen=True` 表示对象创建后不能随意改字段；`slots=True` 限制对象只能拥有声明过的字段。两者共同减少运行中误改配置的机会。

### 12.2 为什么 `LLMConfig` 严格只有四个字段

`LLMConfig` 只回答“怎样连接一次 LLM 服务”：

| 字段 | 问题 | 当前值示例 |
| --- | --- | --- |
| `protocol` | 应该走哪种 API 协议？ | `anthropic` |
| `model` | 请求中填写哪个供应商模型名？ | `deepseek-v4-flash` |
| `base_url` | 请求发到哪里？ | `https://api.deepseek.com/anthropic` |
| `api_key` | 怎样证明调用者有权限？ | 从环境变量读取，缺失时为 `None` |

`label` 不在里面，因为“Flash”只是 UI 展示文字；`context_window` 不在里面，因为它属于应用的上下文预算；`experimental` 也不在里面，因为 SDK 不需要知道界面怎样标记实验模型。

这种拆分叫“关注点分离”：一个对象只回答一类问题。

### 12.3 从一条终端命令开始

在项目根目录运行：

```powershell
conda run --no-capture-output -n mycoder python -m jixue.llm.config
```

这条命令的每一段含义是：

| 片段 | 含义 |
| --- | --- |
| `conda run` | 临时进入一个 Conda 环境运行命令 |
| `-n mycoder` | 指定环境名为 `mycoder` |
| `python -m` | 把一个 Python 模块当程序运行 |
| `jixue.llm.config` | 运行 `src/jixue/llm/config.py` 的 `main()` |

它不会启动 Electron，不会导入 Anthropic SDK，也不会请求 DeepSeek。

### 12.4 第 1 步：`main()` 找到默认文件

- 文件：`src/jixue/llm/config.py`
- 函数：`main()`、`_default_config_path()`
- 输入：命令行参数；默认没有额外参数
- 输出：`config/models.yaml` 的 `Path`
- 为什么存在：手动检查时不应该要求初学者先写 Python 代码

`_default_config_path()` 先检查当前目录下面有没有 `config/models.yaml`。如果命令不是从项目根目录运行，它再尝试从当前源码文件的位置反推项目根。

### 12.5 第 2 步：确定可选的本地覆盖文件

- 函数：`load_model_catalog()`
- 输入：默认路径、可选 `local_path`、环境变量映射
- 输出：还不是目录对象，只是确定两份可能的输入文件
- 为什么存在：提交到 Git 的默认配置和个人电脑配置要分开

没有显式传 `local_path` 时，加载器自动寻找：

```text
config/models.local.yaml
```

它不存在是正常情况。它已经被 `.gitignore` 排除，因此可以放本地端点或个人选择，但仍然不建议直接写 Key；Key 最好继续放环境变量。

### 12.6 第 3 步：YAML 文本变成普通 Python 数据

- 函数：`_read_yaml_mapping()`
- 输入：文件路径
- 中间处理：UTF-8 读取，再调用 `yaml.safe_load()`
- 输出：`dict[str, object]`
- 为什么存在：后面的校验函数需要先拿到普通键值数据

以 Flash 为例，YAML 解析后可以粗略理解为：

```python
{
    "default_model": "flash",
    "models": {
        "flash": {
            "label": "Flash",
            "llm": {
                "protocol": "anthropic",
                "model": "deepseek-v4-flash",
                "base_url": "https://api.deepseek.com/anthropic",
                "api_key": "${DEEPSEEK_API_KEY}",
            },
        },
    },
}
```

这里使用 `safe_load` 而不是危险的通用加载，是因为配置文件属于外部输入，不能允许 YAML 标签要求 Python 随意创建对象。

注意：此时数据仍然是“不可信的普通字典”。字段可能拼错、缺失或类型错误，还不能直接交给客户端。

### 12.7 第 4 步：本地模型按 ID 整体覆盖

- 函数：`_merge_local_document()`
- 输入：默认字典和可选本地字典
- 输出：合并后的新字典
- 为什么存在：允许个人配置，又不修改可提交的默认文件

规则是：

```text
默认 models：flash、pro、vision_exp
本地 models：pro
                    ↓
最终 models：默认 flash、本地 pro、默认 vision_exp
```

同名 `pro` 是“整条替换”，不是字段级深合并。本地 `pro` 如果只写 `model` 而漏掉 `label`，后面的校验会报错。

为什么这样看起来比较啰嗦，反而更安全？因为最终一条模型记录只来自一份文件。深合并会让一半字段来自默认文件、一半来自本地文件，初学者很难判断当前程序究竟用了什么。

### 12.8 第 5 步：检查目录顶层结构

- 函数：`_parse_catalog()`
- 输入：合并后的普通字典
- 输出：校验中的目录数据
- 检查内容：`schema_version`、`default_model`、`models`

顶层字段必须一个不少、也不能多出拼错的字段。例如把 `default_model` 写成 `default_models` 时，不会拖到 API 请求才报奇怪错误，而会直接得到类似：

```text
模型目录 缺少字段：default_model
```

当前只接受 `schema_version: 1`。以后配置格式变化时可以新增版本迁移，而不是悄悄用旧代码误读新格式。

### 12.9 第 6 步：逐条检查模型元数据

- 函数：`_parse_model_definition()`
- 输入：`flash` 这样的稳定 ID 和对应字典
- 输出：`ModelDefinition`
- 为什么存在：UI 元数据和连接字段需要分别验证

主要规则：

1. ID 只能使用小写字母、数字和下划线，并以字母开头。
2. `label` 必须是非空字符串。
3. `experimental` 必须真的是 YAML `true/false`。
4. `capabilities` 必须是非空、无重复的字符串列表。
5. `context_window` 必须是正整数。
6. `llm` 必须继续经过下一层四字段校验。

稳定 ID 和供应商 model 不是同一个东西：

```text
flash                    deepseek-v4-flash
  ↑                              ↑
霁雪内部稳定 ID             供应商 API 模型名
```

供应商以后升级模型名称时，可以修改右边，而 UI 保存的 `flash` 选择仍然有效。

### 12.10 第 7 步：严格提取四字段 `LLMConfig`

- 函数：`_parse_llm_config()`
- 输入：模型记录中的 `llm` 字典
- 输出：`LLMConfig`
- 为什么存在：把 YAML 和未来适配器彻底隔开

这里要求字段集合精确等于：

```text
protocol + model + base_url + api_key
```

当前 `protocol` 只接受 `anthropic`。如果写成 `openai`，错误会说明“尚未安装适配器”，而不是偷偷使用 Anthropic 发送错误格式。

`base_url` 至少要是带主机名的完整 `http://` 或 `https://` 地址。本地开发端点可以使用 HTTP，远程服务应使用 HTTPS。

### 12.11 第 8 步：展开环境变量，但不泄露 Key

YAML 中提交的是：

```yaml
api_key: ${DEEPSEEK_API_KEY}
```

加载器看到整个字段符合 `${VAR}` 形状后，才去当前 Python 进程环境查找 `DEEPSEEK_API_KEY`。

结果分两种：

| 环境变量 | `LLMConfig.api_key` | `credential_status` | 是否是致命配置错误 |
| --- | --- | --- | --- |
| 已设置且非空 | 真实字符串 | `ready` | 否 |
| 未设置或为空 | `None` | `credentials_missing` | 否 |

缺 Key 不让目录加载失败，因为新用户仍然应该能打开应用和使用 FakeLLM。但下一步接入真实客户端后，发送 DeepSeek 请求前必须给出明确提示。

`api_key` 字段还使用了 `repr=False`。因此下面这种调试打印不会包含 Key：

```python
print(catalog)
```

错误消息和诊断摘要也只显示凭据状态。

首版只支持“整个字段替换”，不支持：

```yaml
base_url: https://${HOST}/anthropic
```

这是刻意的简化。完整替换只有“替换成功”或“变量缺失”两种结果，更容易解释和排错。

### 12.12 第 9 步：生成只读领域对象并打印安全摘要

- 函数：`_parse_catalog()`、`format_catalog_summary()`
- 输出：`ModelCatalog` 和不含 Key 的文本
- 为什么存在：后续代码只消费已经验证的数据

`models` 最终用 `MappingProxyType` 包成只读映射，避免某个运行中函数无意执行：

```python
catalog.models["flash"] = another_model
```

正常诊断输出类似：

```text
模型目录加载成功
默认模型：flash
模型数量：3
- flash: Flash → deepseek-v4-flash（credentials_missing）
- pro: Pro → deepseek-v4-pro（credentials_missing）
- vision_exp: Vision Exp → deepseek-v4-flash-vision-exp（credentials_missing，实验模型）
```

这就是从“人手写 YAML”到“程序可安全使用对象”的完整链路。

### 12.13 哪些错误允许继续，哪些必须停止

| 情况 | 结果 | 原因 |
| --- | --- | --- |
| `models.local.yaml` 不存在 | 正常继续 | 本地覆盖本来就是可选的 |
| `DEEPSEEK_API_KEY` 不存在 | 目录加载成功，状态为缺凭据 | 仍可使用 FakeLLM |
| 默认模型 ID 不存在 | 抛出 `ModelCatalogError` | 应用无法知道默认选什么 |
| `llm` 少一个字段 | 抛出 `ModelCatalogError` | 不能构造可靠客户端 |
| 未知 protocol | 抛出 `ModelCatalogError` | 对应适配器没有实现 |
| URL 不是完整 HTTP(S) 地址 | 抛出 `ModelCatalogError` | 请求端点结构无效 |
| YAML 语法损坏 | 抛出 `ModelCatalogError` | 连普通数据都无法可靠读取 |

这里的原则是：可以由用户稍后补上的认证信息允许降级；会让程序猜测行为的结构错误立即停止。

### 12.14 怎样写本地覆盖

例如只想在个人电脑上替换 Pro，可创建不提交的 `config/models.local.yaml`：

```yaml
default_model: pro
models:
  pro:
    label: 本地 Pro
    experimental: true
    capabilities: [text, tools]
    context_window: 1000000
    llm:
      protocol: anthropic
      model: my-local-pro
      base_url: http://127.0.0.1:9000/anthropic
      api_key: ${LOCAL_LLM_API_KEY}
```

`pro` 条目必须完整；`flash` 和 `vision_exp` 没写，所以继续使用默认文件中的记录。调试完成后运行诊断命令确认最终结果，不要靠肉眼猜合并结果。

### 12.15 客户端工厂现在做了什么

当前聊天核心调用仍是：

```python
BridgeApplication(FakeLLMClient())
```

但 `src/jixue/llm/factory.py` 已经能够执行下面这段选择：

```python
def create_llm_client(config: LLMConfig) -> LLMClient:
    if config.protocol == "anthropic":
        return AnthropicLLMClient(config)
    raise LLMClientError(...)
```

这里的“工厂”不是工厂建筑，而是“集中负责创建对象的函数”。它有两个好处：

1. Bridge 不需要到处写 `if protocol == ...`。
2. 以后增加另一种协议时，只改工厂和新适配器，上层继续使用 `LLMClient`。

工厂只接收已经校验过的 `LLMConfig`。它不会再次读取 YAML，也不会自己读取环境变量。这就是“一个模块只做一件事”。

### 12.16 一次假 SDK 文本流是怎么跑起来的

本地测试不请求互联网。它创建一个“长得像 SDK 客户端”的假对象，把它注入 `AnthropicLLMClient`。完整链路如下：

```text
测试准备 LLMConfig
  ↓
创建 AnthropicLLMClient，并注入 FakeAsyncAnthropic 工厂
  ↓
调用 client.stream("你好")
  ↓
_get_client() 第一次创建并缓存假 SDK 客户端
  ↓
client.messages.stream(...) 收到 model/max_tokens/messages/cache_control
  ↓
async with 进入假流
  ↓
text_stream 依次给出多个文本片段
  ↓
每个非空片段变成 LLMStreamEvent(TEXT)
  ↓
await get_final_message() 取得最终 usage 和 stop_reason
  ↓
依次产生 LLMStreamEvent(USAGE) 和 LLMStreamEvent(COMPLETE)
```

把每一步展开：

1. 测试创建四字段配置。测试 Key 只是字符串 `test-key`，不会离开内存。
2. 构造函数先保存配置，不会立刻创建 SDK 客户端。这叫“延迟创建”。
3. 当测试真正开始遍历 `stream()` 时，`_get_client()` 才检查 Key。
4. 第一次调用会把 `api_key`、`base_url` 和 `max_retries=2` 明确传给客户端工厂。
5. `messages` 当前只有一个 `{"role": "user", "content": "你好"}`；多轮历史要等 ConversationManager。
6. `messages.stream()` 同时收到模型名、最大输出 Token 和提示缓存参数。
7. `async for text in stream.text_stream` 一小段一小段读取新增文本，空片段被忽略。
8. `yield TEXT` 把控制权临时交回上层，所以 UI 将来可以边收边显示，而不是等全部结束。
9. 文本流消费完后，必须 `await stream.get_final_message()`。这里的 `await` 是等待异步结果，不是再请求一次模型。
10. 最终 Message 中的供应商 Usage 被复制到霁雪自己的 `Usage`，SDK Message 本身不会离开适配器。
11. 适配器先发 `USAGE`，再发 `COMPLETE`。Bridge 因此仍能沿用现在的状态栏和收口逻辑。

对应的本地测试文件是 `tests/llm/test_anthropic_client.py`。它还会检查 SDK 客户端只创建一次，后续请求复用连接。

### 12.17 Prompt Cache 在这里做了什么

请求里有这一行：

```python
cache_control={"type": "ephemeral"}
```

可以先把 Prompt Cache 理解成“供应商暂时记住稳定的请求前缀”。后续请求如果带着相同的长前缀，供应商可能复用已经处理过的部分，减少重复计算。

当前要诚实区分三件事：

1. **已实现**：适配器把 `cache_control` 传给官方 SDK。
2. **本地已验证**：假 SDK 测试断言参数确实存在，没有在封装层丢失。
3. **尚未验证**：当前请求只有一条短文本，没有完整历史，`Usage` 也还没有缓存读写字段，所以我们不能声称真实缓存已经命中。

ConversationManager 加入后，角色设定和较早的稳定对话会形成可复用前缀。届时再扩展 Usage，并用真实服务返回值确认缓存效果。

### 12.18 SDK 错误怎样安全到达 UI

如果 SDK 抛出 `AuthenticationError`，不能把整个 SDK 异常对象交给 Bridge。供应商错误可能包含请求、响应或不适合公开的细节。

适配器按下面的链路处理：

```text
anthropic.AuthenticationError
  ↓ _translate_anthropic_error()
LLMClientError(
  code="authentication_failed",
  message="API Key 无效或已经失效，请检查模型认证配置",
  retryable=False
)
  ↓ BridgeApplication 捕获
Envelope(type="error", payload={code, message, retryable, scope})
  ↓ Electron / React
用户看到可操作提示，Bridge 进程继续运行
```

认证失败、权限不足、模型/端点不存在和坏请求默认不可重试；限流、超时、连接失败和服务端错误标记为可重试。这里的“可重试”是给上层决策的信息，当前版本不会擅自无限重试。

真正不认识的程序异常由 Bridge 转成固定的 `llm_internal_error`，不会把 `repr(error)`、用户输入或 Key 回显到 UI。

### 12.19 接入真实 DeepSeek 时，哪里会变

下一小步会让 Bridge 启动入口根据配置选择客户端，概念上是：

```text
catalog.get("flash").llm
  ↓
create_llm_client(config)
  ↓
AnthropicLLMClient(config)
  ↓
BridgeApplication(client)
```

下面这些上层代码不应该改变：

- `BridgeApplication` 仍然调用 `LLMClient.stream()`。
- Bridge 仍然发送 `stream_text/usage/turn_complete`。
- Main 和 Preload 仍然转发同一协议。
- React reducer 仍然处理同一组 action。

这就是封装供应商 SDK 的意义。真实手测会由用户在本机临时设置 Key 后进行；Key 不写入 YAML、测试、日志或 Git。

### 12.20 外部 API 信息从哪里核对

模型名称和端点会随服务更新，不能只依赖记忆。本步对照了 DeepSeek 官方资料：

- [Models & Pricing](https://api-docs.deepseek.com/quick_start/pricing/)：列出 Flash、Pro、Vision Exp、1M 上下文和 Anthropic 端点。
- [Using the Anthropic API](https://api-docs.deepseek.com/guides/anthropic_api/)：确认 `base_url` 和 Anthropic SDK 调用方式。
- [Change Log](https://api-docs.deepseek.com/updates/)：确认 Vision Exp 的实验模型名。
- [Anthropic Python SDK](https://github.com/anthropics/anthropic-sdk-python)：核对异步客户端、流式 helper 和类型化异常。
- [Anthropic Prompt Caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)：理解自动缓存、稳定前缀和缓存用量。

如果以后真实请求提示模型不存在，先重新查官方文档，再修改 `config/models.yaml`，不要先去改 React 或 reducer。

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
| 发送真实请求前提示 credentials_missing | `AnthropicLLMClient._get_client()` | 当前进程没有模型 Key |
| 返回 authentication_failed | `_translate_anthropic_error()` | Key 无效、失效或端点认证不匹配 |
| 假 SDK 测试卡在 final message | `await stream.get_final_message()` | 忘记等待异步方法，拿到的是协程而不是 Message |
| 看不到缓存命中数字 | `cache_control` 与 `Usage` | 当前只传了缓存参数，尚未扩展缓存统计和真实多轮前缀 |

排查原则：先确认数据最后成功到达了哪一层，再检查下一条箭头，不要同时修改所有层。

## 15. 本章文件地图

| 文件 | 职责 | 本章最重要的位置 |
| --- | --- | --- |
| `src/jixue/llm/base.py` | 定义自己的模型接口 | `LLMClient`、`LLMStreamEvent` |
| `src/jixue/llm/config.py` | 模型目录和四字段配置 | `load_model_catalog()`、`LLMConfig` |
| `src/jixue/llm/factory.py` | 按协议创建模型客户端 | `create_llm_client()` |
| `src/jixue/llm/adapters/anthropic_client.py` | 隔离官方 SDK 并转换流/错误 | `stream()`、`_get_client()`、`_translate_anthropic_error()` |
| `src/jixue/llm/fake.py` | 模拟流式模型 | `FakeLLMClient.stream()` |
| `config/models.yaml` | 可提交的三模型目录 | `default_model`、每个模型的 `llm` |
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

### 练习 5：只读检查模型目录

执行：

```powershell
conda run --no-capture-output -n mycoder python -m jixue.llm.config
```

先不要设置 Key。确认三个模型仍然能加载，但状态都是 `credentials_missing`。思考：为什么“缺 Key”和“YAML 缺字段”不能都让整个程序直接退出？

### 练习 6：观察假 SDK 适配器

执行：

```powershell
conda run --no-capture-output -n mycoder python -m pytest tests/llm/test_anthropic_client.py -vv
```

它不会请求 DeepSeek，也不会产生费用。运行后打开测试文件，先找 `captured_calls`，再找对它的断言。尝试用自己的话回答：为什么我们既检查最终事件，也检查传给 SDK 的参数？

答案：只检查最终文字，无法发现模型名、端点、缓存参数或 Key 传错；只检查请求参数，又无法证明文本流和最终用量转换正确。两边都测才能守住适配器的输入与输出。

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
11. **`LLMConfig` 为什么不包含 label 和 context_window？** 这些是应用模型目录的展示与调度元数据，不是创建供应商客户端必需的四个字段。
12. **没有 `DEEPSEEK_API_KEY` 时，目录为什么仍能加载？** 缺凭据是可补救的运行状态，用户仍应能启动应用并使用 FakeLLM。
13. **本地覆盖为什么整条替换，而不是只补一个 model 字段？** 整条替换能看清最终配置来自哪里，避免深合并把两份配置悄悄拼成一条。
14. **为什么 `api_key` 设置了 `repr=False`？** 防止打印配置对象时把真实 Key 带进终端、日志或错误报告。
15. **运行配置诊断命令会调用 DeepSeek 吗？** 不会；它只读文件、环境变量并构造本地 Python 对象。
16. **为什么 `import anthropic` 只能写在适配器目录？** 因为供应商实现细节不能污染领域层；以后换协议时，Bridge、Agent Loop 和 UI 才不需要跟着改。
17. **为什么构造 `AnthropicLLMClient` 时不立刻创建 SDK 客户端？** 延迟创建允许应用在没有 Key 时先启动，真正发送时再返回明确的凭据错误。
18. **`text_stream` 产出的是什么？** 每次只产出新增的一小段文本，不是到目前为止的完整回复。
19. **为什么 `get_final_message()` 前有 `await`？** 异步 SDK 需要等待流完全收口后才能取得最终 Message；不等待只会拿到协程对象。
20. **SDK Message 为什么不能直接传给 Bridge？** 它是供应商类型，会让上层与 Anthropic 耦合；适配器只复制霁雪需要的文本、Token 和停止原因。
21. **`cache_control` 已传入是否等于缓存已命中？** 不等于；还需要足够稳定的可缓存前缀和供应商返回的缓存用量证据。
22. **限流错误为什么标记 `retryable=True`？** 限流通常是暂时状态，上层稍后可以重试；认证失败通常需要修改配置，原样重试没有意义。
23. **现在点击 Electron 发送会调用 DeepSeek 吗？** 不会；Bridge 启动入口仍注入 FakeLLM，真实适配器尚未接管聊天链路。

如果第 1—8 题能用自己的话回答，你已经理解当前消息链路；第 9、10 题帮助区分“当前能力”和“未来设计”；第 11—15 题用于复盘配置加载；第 16—23 题用于复盘客户端工厂、流式适配器、缓存和错误边界。

## 18. 本章下一小步

接下来按这个顺序继续，不进入工具系统：

1. 把模型目录、客户端工厂和 Bridge 启动入口接起来，同时保留 FakeLLM 作为默认离线模式。
2. 由用户在本机临时设置 Key，使用 DeepSeek Anthropic 端点完成一次真实流式手测。
3. 实现内部消息与 API 消息两层模型。
4. 实现 `ConversationManager.to_api_format()`，让第二次请求携带完整历史。
5. 加入 Flash、Pro、Vision Exp 模型选择。

每完成一步，本章都会增加该能力自己的推荐阅读顺序、端到端链路、输入输出示例、常见错误和手动验证方法。
