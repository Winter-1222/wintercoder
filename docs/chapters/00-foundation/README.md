# 第 0 章：先让 Electron 和 Python 互相认识

> 适合读者：第一次接触 Electron、Python 子进程、前后端通信的人。你不需要提前知道 IPC、NDJSON 或异步编程。

## 1. 这一章到底要做什么

霁雪实际上由两个主要程序合作完成：

- Electron 客户端负责显示窗口、接收点击和更新界面。
- Python 后端负责以后要加入的模型调用、工具、Agent Loop 和记忆。

它们使用的语言不同，运行在不同进程里，不能直接调用对方的函数。第 0 章要解决的就是：

> Electron 怎么启动 Python？两边怎么确认“我已经准备好了”？应用退出时又怎么一起关闭？

完成这一章后，你应该能看到：

1. 执行 `npm run dev` 后出现霁雪窗口。
2. 左下角先显示正在连接，随后变成“FakeLLM / Bridge 在线”。
3. 关闭窗口时 Python 一起退出，不出现 JavaScript 错误弹窗。

这一章还没有处理用户聊天。聊天消息的完整链路放在第 1 章。

## 2. 推荐阅读顺序

不要一上来把所有源码从头读到尾。按照下面顺序，每次只解决一个问题。

| 顺序 | 阅读内容 | 这一次只回答什么问题 | 第一遍可以跳过什么 |
| --- | --- | --- | --- |
| 1 | 本文第 3—6 节 | 霁雪由哪几个部分组成？启动时发生什么？ | 所有具体代码 |
| 2 | 根目录 `README.md` | 怎么安装和启动项目？ | 测试命令的细节 |
| 3 | `docs/PROJECT_STRUCTURE.md` | 每个目录负责什么？ | 还没学到的文件 |
| 4 | `apps/desktop/src/main/index.ts` | Electron 从哪里开始创建窗口？ | IPC 参数校验 |
| 5 | `apps/desktop/src/main/bridge-process.ts` | Electron 怎么启动 Python？ | stdout 缓冲区算法 |
| 6 | `src/jixue/bridge/__main__.py` 和 `server.py` | Python 从哪里开始等待命令？ | 并发任务集合 |
| 7 | `src/jixue/domain/events.py` | 两边传输的 JSON 为什么有固定字段？ | dataclass 的高级语法 |
| 8 | `src/jixue/bridge/application.py` | Python 收到 hello 后如何返回 ready？ | chat.send 分支，留到第 1 章 |
| 9 | `apps/desktop/src/preload/index.ts` | 状态怎样安全地进入页面？ | 聊天事件，留到第 1 章 |
| 10 | 回到本文第 7—10 节 | 能否独立复述启动链路并手动验证？ | 无 |

推荐做法是：先把本文读完并运行一次，再按顺序打开源码。源码中遇到不懂的 TypeScript 或 Python 语法，先看它的输入和输出，不必第一次就研究每个符号。

## 3. 零基础术语表

### 3.1 程序和进程

“程序”可以理解为磁盘上的代码；“进程”是程序真正运行起来后的实例。

例如：

- `electron.exe` 是程序，启动霁雪后会出现 Electron 进程。
- `python.exe` 是程序，Bridge 启动后会出现 Python 进程。

两个进程的内存彼此隔离。Electron 里的 JavaScript 变量，Python 不能直接读取；Python 对象也不能直接传给 React。

### 3.2 Electron 的 Main、Preload 和 Renderer

一个 Electron 应用不是只有一段 JavaScript：

| 名称 | 可以把它想成 | 霁雪里的职责 |
| --- | --- | --- |
| Main | 总管 | 创建窗口、启动 Python、注册安全通信入口 |
| Preload | 门卫 | 只把允许的少量功能交给页面 |
| Renderer | 网页界面 | 显示侧栏、消息、输入框和状态 |

Renderer 负责显示，因此它最接近用户；Main 权限更高，因此不能把所有能力都直接暴露给 Renderer。

### 3.3 stdin、stdout 和 stderr

启动一个命令行程序时，操作系统通常给它三条通道：

- stdin：标准输入，外界向程序写数据。
- stdout：标准输出，程序向外界返回正常结果。
- stderr：标准错误，程序输出日志和诊断信息。

霁雪约定：

```text
Electron ──写入──> Python stdin
Electron <──读取── Python stdout
开发日志 <──────── Python stderr
```

stdout 必须只放协议 JSON。假如 Python 把 `正在启动模型……` 这种日志写入 stdout，Electron 会误以为它是一条 JSON 消息，解析就会失败。

### 3.4 JSON 和 NDJSON

JSON 是一种文本数据格式：

```json
{"name":"霁雪","status":"ready"}
```

NDJSON 的全名是 Newline Delimited JSON，可以理解为“一行一个 JSON”：

```text
{"type":"bridge.ready","sequence":0}
{"type":"stream_text","sequence":1}
{"type":"stream_text","sequence":2}
```

每一行结束时都有换行符 `\n`。接收方只要不断收集字符，看到换行符就知道一条完整消息结束了。

### 3.5 Bridge 是什么

Bridge 的中文是“桥”。它不是模型，也不是 UI；它负责把 Electron 的命令送给 Python，再把 Python 的事件送回 Electron。

## 4. 先亲手启动一次

在 PowerShell 中进入项目目录：

```powershell
cd E:\Agents\dev\myAgent
npm run dev
```

如果是第一次运行，先安装依赖：

```powershell
conda run --no-capture-output -n mycoder python -m pip install --index-url https://pypi.org/simple -e ".[dev]"
npm install
npm run dev
```

启动后先不要发送消息，只观察左下角状态：

1. 最初是“正在连接 Python Bridge”。
2. Python 启动并处理握手命令。
3. 状态变成“FakeLLM / Bridge 在线”。

这个变化就是本章要理解的“启动握手”。

## 5. 先看全景：启动握手链路

```text
你执行 npm run dev
        ↓
Electron Main 运行 index.ts
        ↓
创建 BrowserWindow，同时创建 PythonBridge
        ↓
PythonBridge 用 Conda 启动 python -m jixue.bridge
        ↓
Python Bridge 开始从 stdin 等待一行 JSON
        ↓
Electron 发送 bridge.hello
        ↓
Python 返回 bridge.ready
        ↓
Electron 把 ready 状态转发给 Renderer
        ↓
React 更新左下角：FakeLLM / Bridge 在线
```

接下来逐步展开这张图。

## 6. 启动握手逐步拆解

### 第 1 步：`npm run dev` 找到桌面应用

根目录 `package.json` 中的 `dev` 命令会进入 `@jixue/desktop` workspace，运行 `electron-vite dev`。

你暂时只需要知道，electron-vite 会准备三部分代码：Main、Preload 和 Renderer，然后启动 Electron。

输入：终端命令 `npm run dev`。

输出：Electron 进程开始运行。

### 第 2 步：Main 创建窗口

入口文件：`apps/desktop/src/main/index.ts`

Electron 准备完成后执行：

```ts
app.whenReady().then(() => {
  mainWindow = createWindow()
  // 后面还会创建 Bridge
})
```

`createWindow()` 创建 `BrowserWindow`。重要配置包括：

- `contextIsolation: true`：页面和高权限代码分开。
- `nodeIntegration: false`：页面不能随意调用 Node.js。
- `sandbox: true`：进一步限制 Renderer。

输入：Electron 的 ready 事件。

输出：用户能看到的桌面窗口。

### 第 3 步：Main 创建 `PythonBridge`

仍在 `index.ts`：

```ts
bridge = new PythonBridge(projectRoot)
bridge.start()
```

`projectRoot` 是项目根目录 `E:\Agents\dev\myAgent`。Bridge 需要它来找到 Python 源码并把 Python 的当前工作目录设置正确。

输入：项目根目录。

输出：一个负责管理 Python 子进程的 `PythonBridge` 对象。

### 第 4 步：`PythonBridge.start()` 启动 Conda 中的 Python

文件：`apps/desktop/src/main/bridge-process.ts`

核心命令等价于：

```powershell
conda run --no-capture-output -n mycoder python -u -m jixue.bridge
```

逐段解释：

- `conda run`：在指定 Conda 环境中运行命令。
- `-n mycoder`：指定环境名为 `mycoder`。
- `python -u`：使用无缓冲模式，事件产生后立刻输出。
- `-m jixue.bridge`：把 `jixue.bridge` 当作模块运行。

Node 的 `spawn()` 会拿到这个 Python 进程的 stdin、stdout、stderr，因此两边可以通信。

输入：Conda 命令和环境变量。

输出：一个正在运行的 Python 子进程。

### 第 5 步：Python 找到模块入口

文件：`src/jixue/bridge/__main__.py`

当 Python 收到 `-m jixue.bridge` 时，它会执行这个文件，再调用 `server.py` 中的 `main()`。

`main()` 做三件事：

1. 把三个标准通道统一设置成 UTF-8。
2. 创建 `FakeLLMClient` 和 `BridgeApplication`。
3. 创建 `BridgeServer`，开始持续读取 stdin。

此时 Python 已经启动，但 Electron 还不知道它是否真的能处理协议，所以还要握手。

### 第 6 步：Electron 发送 `bridge.hello`

文件：`apps/desktop/src/main/bridge-process.ts`

子进程触发 `spawn` 事件后，`sendHello()` 构造一个信封：

```json
{
  "version": 1,
  "type": "bridge.hello",
  "request_id": "bridge_随机UUID",
  "sequence": 0,
  "timestamp": "当前UTC时间",
  "payload": {
    "protocol_version": 1,
    "client_version": "0.1.0"
  }
}
```

然后 `writeEnvelope()` 做两件事：

1. `JSON.stringify(envelope)` 把 JavaScript 对象变成 JSON 字符串。
2. 在末尾加 `\n`，写入 Python stdin。

这里的信封类似快递外包装：固定字段告诉接收方“这是什么、属于谁、顺序是多少”，`payload` 才是里面真正的业务内容。

### 第 7 步：Python 读取并验证这一行

文件：`src/jixue/bridge/server.py`

`BridgeServer.run()` 一直循环执行 `readline()`。它读到换行符后得到完整的一行，再交给：

```python
Envelope.from_json_line(line)
```

文件：`src/jixue/domain/events.py`

这个函数会检查：

- 是不是合法 JSON。
- 是否包含六个必需字段。
- `version` 是否等于 1。
- `request_id` 是否为非空字符串。
- `sequence` 是否为非负整数。
- `payload` 是否为对象。

为什么要检查？因为进程边界上的输入不能默认可信。越早拒绝坏数据，后面的业务代码越简单。

### 第 8 步：应用层处理 hello

文件：`src/jixue/bridge/application.py`

`BridgeApplication.handle()` 看到：

```python
if command.type == "bridge.hello":
```

于是产生一条 `bridge.ready` 事件：

```json
{
  "version": 1,
  "type": "bridge.ready",
  "request_id": "与 hello 相同的 request_id",
  "sequence": 0,
  "timestamp": "当前UTC时间",
  "payload": {
    "protocol_version": 1,
    "backend_version": "0.1.0",
    "capabilities": ["fake_llm", "stream_text", "usage"]
  }
}
```

相同的 `request_id` 表示 ready 是对刚才 hello 的回应。

### 第 9 步：Python 把 ready 写入 stdout

回到 `server.py`，`_write_event()`：

1. 调用 `event.to_json_line()` 生成一行 JSON。
2. 写入 stdout。
3. 调用 `flush()` 立即把数据送出去。

写锁 `_write_lock` 保证将来多个任务同时产生事件时，不会把两行 JSON 写到一起。

### 第 10 步：Electron 把 stdout 拼成完整行

回到 `bridge-process.ts`。

操作系统不保证一次 `data` 事件正好是一整行，可能半行到达，也可能一次到达两行。因此 `consumeStdout()` 先把文本追加到 `stdoutBuffer`，再按 `\n` 切分。

完整行交给 `consumeLine()`：

1. `JSON.parse()` 把字符串恢复为对象。
2. `isBridgeEnvelope()` 检查对象结构。
3. 如果类型是 `bridge.ready`，把 Bridge 状态改成 ready。

### 第 11 步：状态经过 Main 和 Preload 进入页面

`PythonBridge.setState()` 通知 Main 注册的状态监听器。

Main 使用：

```ts
sendToRenderer('jixue:bridge-state', state)
```

把状态送到 Renderer。`sendToRenderer()` 会先确认窗口没有被销毁，避免退出时出现主进程错误。

文件：`apps/desktop/src/preload/index.ts`

Preload 只暴露 `window.jixue.onBridgeState()`。页面不直接接触 Electron 的 `ipcRenderer`。

### 第 12 步：React 更新左下角状态

文件：`apps/desktop/src/renderer/src/App.tsx`

页面启动时订阅：

```ts
window.jixue.onBridgeState((bridgeState) => {
  dispatch({ type: 'bridge_changed', state: bridgeState })
})
```

`dispatch` 把动作交给 `state.ts` 中的 `chatReducer()`。reducer 返回新状态，React 自动重新渲染，于是左下角从“正在连接”变成“FakeLLM / Bridge 在线”。

到这里，一次完整握手结束。

## 7. 为什么不让 Renderer 直接启动 Python

假设页面可以直接调用 Node 的 `spawn()`：

1. 页面中的任意脚本都可能启动系统命令。
2. 以后渲染 Markdown 或外部内容时，攻击面会变大。
3. UI 会同时负责样式、进程和业务，代码很快混乱。

现在的边界是：

```text
Renderer 只知道 window.jixue
Preload 只开放四个白名单方法
Main 才能操作 Electron 和 Python 子进程
Python 只通过 NDJSON 与 Main 对话
```

每层只做自己的事情，出错时也更容易定位。

## 8. 应用退出时发生什么

关闭窗口不只是“画面消失”，还要回收 Python：

```text
用户关闭窗口
  ↓
BrowserWindow 触发 closed，mainWindow 设为 null
  ↓
window-all-closed 调用 app.quit()
  ↓
before-quit 调用 bridge.stop()
  ↓
关闭 Python stdin，并终止仍在运行的子进程
  ↓
Python readline() 收到 EOF，结束 BridgeServer.run()
```

发送状态前必须检查窗口是否已销毁。项目曾经漏掉这一步，导致退出时弹出 “A JavaScript error occurred in the main process”；现在 `sendToRenderer()` 和 `stop()` 都保护了关闭竞态。

## 9. 本章文件地图

| 文件 | 先找哪个函数 | 它解决的问题 |
| --- | --- | --- |
| `apps/desktop/src/main/index.ts` | `createWindow()` | 如何创建安全窗口 |
| 同上 | `sendToRenderer()` | 如何避免向已关闭窗口发事件 |
| `apps/desktop/src/main/bridge-process.ts` | `start()` | 如何启动 Python |
| 同上 | `sendHello()` | 如何发送握手命令 |
| 同上 | `consumeStdout()` | 如何把任意文本片段拼回完整 JSON 行 |
| `src/jixue/bridge/__main__.py` | 模块入口 | `python -m` 最先执行哪里 |
| `src/jixue/bridge/server.py` | `main()`、`run()` | 如何持续读取 stdin |
| `src/jixue/domain/events.py` | `from_json_line()` | 如何验证协议信封 |
| `src/jixue/bridge/application.py` | `handle()` | 如何把 hello 变成 ready |
| `apps/desktop/src/preload/index.ts` | `api` | Renderer 被允许使用哪些功能 |

所有现有文件的职责可以查阅 `docs/PROJECT_STRUCTURE.md`。

## 10. 跟着做一次调试

### 练习 A：只观察握手

1. 执行 `npm run dev`。
2. 不发送消息。
3. 观察左下角状态从 starting 变成 ready。
4. 关闭窗口，确认没有错误弹窗。

### 练习 B：找到握手两端

1. 在 `bridge-process.ts` 搜索 `sendHello`。
2. 记住它发送的 `type` 是 `bridge.hello`。
3. 在 `application.py` 搜索同一个字符串。
4. 找到它返回的 `bridge.ready`。

这就是阅读跨进程代码最实用的方法：从一个协议类型出发，在发送端和接收端分别搜索。

### 练习 C：故意画错再纠正

先不看文档，自己画箭头：Renderer、Preload、Main、Python 的先后顺序。再与下面答案比较：

```text
启动状态返回：Python → Main → Preload → Renderer
普通命令发送：Renderer → Preload → Main → Python
```

## 11. 常见问题

### 为什么不用 HTTP？

第一周只需要一个 Electron 客户端。stdin/stdout 不占端口、依赖少，也天然适合一行一条事件。以后需要多个客户端或远程连接时再评估 HTTP。

### 为什么一定要加换行符？

JSON 本身没有告诉接收方“这一段流到哪里结束”。换行符就是 NDJSON 的消息边界。

### `-u` 有什么用？

它让 Python 标准输出尽量不缓冲。否则 Python 可能先攒一批文本再输出，UI 看起来就不像流式回复。

### 为什么 stdout 不能打日志？

stdout 是协议专线。日志不是合法信封，混进去会破坏 JSON 解析；日志应该去 stderr。

### 为什么同一份协议在 Python 和 TypeScript 各写一次？

两种语言不能直接共享类型。Python 在运行时校验输入，TypeScript 在开发时帮助前端发现字段错误；两边共同遵守同一份字段契约。

## 12. 自测题与答案

1. **谁负责创建窗口？** Electron Main 的 `createWindow()`。
2. **谁真正启动 Python？** Main 中的 `PythonBridge.start()`。
3. **Python 从哪里读取命令？** stdin。
4. **Python 把业务事件写到哪里？** stdout。
5. **普通日志应该写到哪里？** stderr。
6. **握手请求和响应怎样对应？** 使用相同 `request_id`。
7. **Renderer 为什么不能直接使用 `ipcRenderer`？** Preload 只开放安全白名单，减少权限暴露。
8. **为什么退出前要检查窗口是否销毁？** 防止 Bridge 的迟到事件发往失效的 webContents。

如果这些问题还答不上来，建议重新读第 5、6、8 节，不需要先背代码。

## 13. 本章边界和下一章

本章已经完成：

- Electron 启动 Python。
- hello/ready 握手。
- 状态安全进入 Renderer。
- 退出时一起回收。

本章没有完成：

- 把用户消息送给模型。
- 流式显示模型回复。
- Token、耗时和 Markdown。

下一章会用一句具体的“你好”从输入框出发，跟踪它如何走到 FakeLLM，再把回复一小段一小段送回界面。
