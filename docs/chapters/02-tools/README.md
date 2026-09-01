# 第 2 章：工具系统

本章已经完成一个最小工具闭环：模型请求 `read_file`，霁雪执行工具、显示结果，再把结果交回模型生成最终回答。

现在还没有真正的 `AgentLoop`。当前核心在 `BridgeApplication`，每条消息最多调用两次 LLM，只允许一次工具往返。这样代码短，也方便先看懂“工具为什么需要两次模型请求”。

## 当前成果

- `Tool`：规定每个工具必须实现的方法。
- `BaseTool`：统一做参数校验，并把可修复错误包装成 `ToolResult`。
- `ToolRegistry`：注册、启用、禁用、导出和执行工具。
- `read_file`：读取项目目录内 UTF-8 文件。
- Anthropic 适配器：拼接流式 JSON 参数，并隐藏 SDK 类型。
- Bridge：执行工具、发出 `tool_result`，再请求一次 LLM。
- UI：工具卡片显示执行中、完成或失败、输入、输出和耗时。
- FakeLLM：输入 `/read README.md`，无需 Key 和费用就能手测。

## 推荐阅读顺序

1. `src/jixue/tools/base.py`：认识工具合同、上下文和结果。
2. `src/jixue/tools/read_file.py`：看一个具体工具怎样创建。
3. `src/jixue/tools/registry.py`：看工具怎样集中管理和执行。
4. `src/jixue/domain/conversation.py`：认识 `tool_use`、`tool_result` 内容块。
5. `src/jixue/bridge/application.py`：看本章最核心的一次工具往返。
6. `src/jixue/llm/adapters/anthropic_client.py`：看领域类型怎样在边界转成 SDK 类型。
7. `src/jixue/llm/fake.py`：看 `/read` 怎样免费模拟模型请求工具。
8. `state.ts` 和 `App.tsx`：看工具事件怎样更新同一张卡片。

## 工具接口是什么意思

Python 使用蛇形命名，所以设计稿里的 `inputSchema` 在代码中叫 `input_schema`。

| 方法 | 初学者可以这样理解 |
| --- | --- |
| `name` | 模型调用工具时使用的名字 |
| `description` | 告诉模型这个工具适合做什么 |
| `input_schema` | 用 JSON Schema 说明参数格式 |
| `execute` | 校验通过后真正做事 |
| `is_read_only` | 是否只读取、不修改 |
| `is_destructive` | 是否可能造成难恢复的改变 |
| `is_concurrency_safe` | 将来是否允许与其他工具同时执行 |
| `category` | UI 分类，如 file、shell |
| `validate_input` | 执行前尽早拒绝坏参数 |

`ToolResult` 只有三个核心字段：

```text
content   给模型看的成功内容或错误信息
is_error  本次工具是否失败
metadata  只给 UI 的附加信息，不发给模型
```

文件不存在是模型有机会修正的反馈，不是程序崩溃。因此它返回 `is_error=True`，而不是抛出异常结束聊天。

## 一条工具消息怎样跑完

先看全貌。一次工具任务实际上包含两次 LLM 请求：

```text
用户消息
  → 第一次 LLM 请求
  → 模型返回 tool_use
  → 霁雪执行 read_file
  → 产生 tool_result
  → 第二次 LLM 请求
  → 模型根据结果输出最终文字
  → UI 完成本轮
```

按代码执行顺序展开：

1. Electron 把用户文字作为 `chat.send` 发给 Python。
2. `BridgeApplication` 从 `ConversationManager` 取得干净历史。
3. `ToolRegistry.to_api_format()` 把所有启用工具的定义交给 LLM。
4. 模型认为需要文件，于是在流中返回 `tool_use`。
5. Anthropic 适配器把 JSON 碎片拼好，生成霁雪自己的 `LLMStreamEvent`。
6. Bridge 先发 `tool_use` 给 UI，界面创建“工具执行中”卡片。
7. Bridge 调用 `ToolRegistry.execute()`，注册中心找到 `read_file`。
8. `BaseTool.execute()` 先校验参数，再读取项目目录内的文件。
9. Bridge 把 `ToolResult` 发给 UI；界面按 `tool_use_id` 更新原卡片。
10. Bridge 临时给历史追加下面两条 API 消息：

```text
assistant: [tool_use]
user:      [tool_result]
```

11. Bridge 第二次调用 LLM。模型已经看到文件内容，可以回答最初问题。
12. Bridge 保存最终文字和两次请求的 Token 总量，最后发 `turn_complete`。

为什么 `tool_result` 放在 `user` 消息中？因为工具是霁雪客户端替模型执行的，Anthropic 协议要求客户端用下一条 user 消息把执行结果交回模型。它不是用户亲口说的话，只是 API 规定的位置。

核心控制流可以缩成：

```python
history = conversation.to_api_format()

第一次响应 = await llm.stream(history, registry.to_api_format())
工具结果 = await registry.execute(工具名, context, 工具参数)

history += [模型的 tool_use, 客户端的 tool_result]
最终回答 = await llm.stream(history, registry.to_api_format())
```

代码中的 `for api_pass in range(2)` 就代表“最多两次”。如果第二次 LLM 又请求工具，霁雪会返回“本章只支持一次工具往返”并停止。第三章才会把这里改成带停止条件的循环。

## JSON 碎片为什么需要缓冲

模型可能这样流式返回工具参数：

```text
content_block_start:  id=tool_1, name=read_file
content_block_delta:  {"path":
content_block_delta:  "README.md"}
content_block_stop
```

单个 delta 不是完整 JSON，不能立刻解析。适配器按内容块 `index` 保存缓冲区，持续追加 `partial_json`，等 `content_block_stop` 后再调用 `json.loads()`。

如果 JSON 不完整或不是对象，适配器仍产生带错误信息的工具事件。Bridge 会把它作为失败的 `ToolResult` 继续送回模型，而不会让整个进程崩溃。

## 免费手动测试

在项目根目录启动：

```powershell
npm run dev
```

窗口出现后发送：

```text
/read README.md
```

正常现象：

1. 出现名为 `read_file` 的卡片。
2. 卡片从“工具执行中”变成“工具完成”，并显示毫秒耗时。
3. 展开“查看输入参数”能看到 `README.md`。
4. 卡片输出显示文件内容。
5. 霁雪最后回复“工具执行完成”，并展示内容预览。

再发送 `/read no-such-file.txt`。卡片应显示“工具失败”，但输入框仍可继续发送消息。

使用真实模型时，可以直接说“请读取 README.md 并总结”。真实请求可能产生费用，而且模型是否选择工具由模型自己决定。

自动检查：

```powershell
npm run test:all
conda run --no-capture-output -n mycoder ruff check .
conda run --no-capture-output -n mycoder mypy src tests
npm run typecheck
npm run test:electron
```

## 常见坑

- `path` 为空：参数校验返回失败结果，读取函数不会运行。
- 文件不存在：返回 `is_error=True`，程序不会退出。
- 使用 `../` 逃出项目目录：`read_file` 会拒绝。
- 工具被禁用或名字错误：Registry 返回“工具不存在或已禁用”。
- 同名工具注册两次：启动时立即报错，避免执行时选错。
- JSON 参数损坏：卡片显示解析错误，Bridge 仍会继续。
- 工具卡片一直“执行中”：检查 `tool_result.id` 是否与 `tool_use.id` 一致。
- 第二次 LLM 又请求工具：本章会拒绝，这是设计边界，不是完整 Agent Loop。

## 本章变更记录

- 建立 Tool、BaseTool、ToolResult 和 ToolRegistry。
- 新增只允许读取项目目录的 read_file。
- 工具定义接入 LLM，并保持稳定顺序以利于 Prompt Cache。
- 支持流式 tool_use JSON 拼接和错误降级。
- 支持一次工具执行、tool_result 回传和第二次 LLM 收尾。
- FakeLLM 新增 /read 手测入口，UI 新增结果与耗时。
- 修复 Grid 子项的最小高度约束，长对话现在可以上下滚动。

## 自测题与答案

**问：文件不存在为什么不让程序直接报内部错误？**

答：模型可以根据“文件不存在”换路径重试，所以它是有价值的工具结果。

**问：metadata 为什么不发给模型？**

答：它主要服务 UI，例如耗时和绝对路径。发给模型会浪费 Token，也可能泄露不必要的环境细节。

**问：为什么不能每收到一个 JSON 碎片就解析？**

答：单个碎片通常不是完整 JSON，必须等内容块结束后拼完整。

**问：调用工具后为什么还要请求一次 LLM？**

答：工具只返回原始内容，不负责回答用户。第二次请求让模型看到工具结果，再组织最终回答。

**问：现在已经是 Agent Loop 了吗？**

答：不是。现在最多两次 LLM 请求、一次工具往返。持续循环、停止条件、取消和异常状态检测属于第三章。
