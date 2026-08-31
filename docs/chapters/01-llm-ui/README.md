# 第 1 章：让 AI 开口说话

本章已经完成一个最小闭环：界面发送消息，Python 保存完整历史，LLM 流式回复，界面在结束后渲染 Markdown。

这里还没有 Agent Loop，也没有工具。真正的 Agent 会在第三章出现；第一章只是它以后调用 LLM 的底座。

## 推荐阅读顺序

1. `apps/desktop/src/renderer/src/App.tsx`：从发送按钮开始。
2. `apps/desktop/src/renderer/src/state.ts`：看事件怎样变成页面状态。
3. `apps/desktop/src/main/bridge-process.ts`：看 Electron 怎样启动 Python。
4. `src/jixue/bridge/application.py`：这是本章最核心的业务文件。
5. `src/jixue/domain/conversation.py`：看多轮历史怎样保存和清洗。
6. 最后看 `src/jixue/llm/fake.py` 和 `llm/adapters/anthropic_client.py`，比较模拟与真实模型。

## 先认识五个词

| 词 | 小白解释 |
| --- | --- |
| 流式 | 回复不是最后一次出现，而是一小段一小段到达 |
| Bridge | Electron 和 Python 之间的桥 |
| NDJSON | 一行放一个 JSON，换行就是一条消息的边界 |
| reducer | 接收“旧状态 + 一个事件”，返回“新状态”的函数 |
| 适配器 | 把外部 SDK 的格式翻译成霁雪自己的格式 |

## 一条消息是怎么跑起来的

假设用户输入“你好”并按 Enter：

1. `App.sendMessage()` 生成 `requestId`，并派发 `request_started`。
2. `chatReducer` 立刻把用户消息和一个空的 AI 消息放进页面，所以界面不会卡住。
3. `window.jixue.sendChat()` 经过 preload，把消息送到 Electron Main。
4. Main 校验消息长度和 `requestId`，再调用 `PythonBridge.sendChat()`。
5. `PythonBridge` 把命令转成一行 JSON，写入 Python 子进程的 stdin。
6. `BridgeServer.run()` 从 stdin 读到这一行，解析为 `Envelope`。
7. `BridgeApplication._chat()` 把“你好”加入 `ConversationManager`。
8. `to_api_format()` 过滤失败消息、合并相邻同角色消息，得到干净的完整历史。
9. LLM 的 `stream(history)` 收到历史。Fake 和真实模型都遵守同一个 `LLMClient` 接口。
10. 每到一段文字，Application 就发一个 `stream_text`；最后再发 `usage` 和 `turn_complete`。
11. Python 把这些事件逐行写到 stdout；`PythonBridge` 解析后转发给 React。
12. `App.handleEvent()` 把事件交给 `chatReducer`。文字持续追加，完成时状态改成 `complete`。
13. `MessageView` 在回复中显示纯文本；完成后才交给 `ReactMarkdown` 渲染。

整条链路可以缩成：

```text
输入框 → App → preload → Electron Main → PythonBridge
      → BridgeServer → BridgeApplication → ConversationManager → LLM
      ← stream_text / usage / turn_complete ←←←←←←←←←←←←←←
```

## 最核心的伪代码

```python
历史.add_user(用户输入)
干净历史 = 历史.to_api_format()

async for 事件 in llm.stream(干净历史):
    if 是文字:
        立刻发给界面
    if 是用量:
        更新状态栏
    if 已完成:
        把完整回复加入历史
        通知界面渲染 Markdown
```

本章真正需要理解的核心就是这几行。其他文件只是在负责“消息怎么穿过进程”和“页面怎么显示”。

## reducer 的每个 case 是什么

`chatReducer(state, action)` 不直接修改旧状态，而是返回一个新状态：

| case | 作用 |
| --- | --- |
| `bridge_changed` | 更新 Python 在线/离线状态 |
| `model_changed` | 更新状态栏模型名 |
| `request_started` | 加入用户消息和空的 AI 消息 |
| `text_received` | 把新文字追加到 AI 消息末尾 |
| `usage_received` | 更新累计 Token |
| `request_completed` | 标记完成，允许 Markdown 渲染 |
| `request_failed` | 保留已到达的文字并标记失败 |

它就是所谓的 reducer。把状态更新集中在这里，`App.tsx` 不必到处手动改消息数组。

## 为什么能多轮对话

`ConversationManager` 保存内部 `Message`。内部消息有 ID、状态、时间和 Token；API 只需要 `role + content`。

第二次发送时，传给 LLM 的不是只有第二句话，而是：

```text
user: 第一条问题
assistant: 第一条回答
user: 第二条问题
```

失败或取消的 AI 消息不会传给 LLM，避免不完整内容污染下一轮。

## Fake 与真实模型

- `FakeLLMClient`：固定回复，不联网、不花钱，用来证明整条链路正常。
- `AnthropicLLMClient`：使用 Anthropic SDK 调用兼容端点。SDK 只在这个适配器文件中出现。

真实模型只需要四个配置字段：`protocol`、`model`、`base_url`、`api_key`。YAML 保存前三类公共信息，Key 只放在项目根目录 `.env`。

```dotenv
JIXUE_LLM_MODE=configured
JIXUE_MODEL_ID=flash
DEEPSEEK_API_KEY=你的Key
```

`.env` 会覆盖同名系统环境变量。修改后必须完全退出并重新执行 `npm run dev`，因为配置只在 Python 启动时读取一次。

可选模型短名是 `flash`、`pro`、`vision_exp`，默认 `flash`。当前只实现文本聊天，`vision_exp` 只是模型选择，不代表界面已经支持发图片。

## 启动和手动测试

```powershell
npm run dev
```

建议按这个顺序手测：

1. 先用 Fake 模式发送“你好”，观察文字逐段出现。
2. 回复完成后，确认标题和列表已变成 Markdown 样式。
3. 再问“我刚才说了什么？”，确认第二轮能看到第一轮历史。
4. 查看底部模型名、输入/输出 Token 和耗时。
5. 关闭窗口，确认没有错误弹窗和残留 Python 进程。
6. 需要真实模型时再配置 `.env`，重启后确认模型名不再是 `fake-jixue`。

自动检查：

```powershell
conda run --no-capture-output -n mycoder pytest
conda run --no-capture-output -n mycoder ruff check .
conda run --no-capture-output -n mycoder mypy src
npm run test:frontend
npm run typecheck
npm run test:electron
```

## 按现象排错

| 现象 | 先检查什么 |
| --- | --- |
| 一直显示 Fake | `.env` 是否在项目根目录，模式是否为 `configured`，改完是否重启 |
| 提示缺少凭据 | 变量名必须是 `DEEPSEEK_API_KEY`，值不能空 |
| Bridge 无法启动 | `conda run -n mycoder python --version` 是否成功 |
| 回复到一半失败 | 看界面的错误文字；已到达内容会保留，但不会进入下一轮历史 |
| 退出有 JavaScript 弹窗 | 运行 `npm run test:electron`，检查子进程关闭逻辑 |

## 本章变更记录

- 完成 Fake/真实 LLM 的统一接口和流式事件。
- 完成多轮历史、消息清洗和累计 Token。
- 完成 Electron 聊天界面、Markdown 完成后渲染和安全退出。
- 做过一次减法重构：删除工厂层、重复消息模块、模型目录高级能力和重复文档。

## 自测题与答案

**问：为什么流式过程中不立即渲染 Markdown？**

答：Markdown 语法可能只到了一半，反复解析会闪动；完成后一次渲染更稳定。

**问：为什么不能让领域层直接使用 Anthropic 的消息类型？**

答：那会让整个项目依赖一个供应商。霁雪使用自己的 `APIMessage`，更换供应商只改适配器。

**问：`chatReducer` 是不是 reducer？**

答：是。它接收旧 `ChatState` 和一个 `ChatAction`，返回新的 `ChatState`。

**问：为什么第二轮 API 请求要带第一轮内容？**

答：LLM API 本身不记得上一次请求。客户端必须每次带上完整历史，模型才像是在连续对话。
