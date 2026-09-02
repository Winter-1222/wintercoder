# 第 2 章：工具系统

本章完成了三个最小只读工具：`read_file` 读取文件、`glob` 查找路径、`grep` 搜索内容。它们都通过同一个 `BaseTool` 和 `ToolRegistry` 接入 Agent。

第三章已经实现真正的 Agent Loop。本章只聚焦一件事：模型怎样知道有哪些工具，以及一次工具调用怎样从模型跑到文件系统再回到模型。

## 当前成果

- `Tool`：规定所有工具必须提供的方法。
- `BaseTool`：统一校验参数，并把普通失败包装成 `ToolResult`。
- `ToolRegistry`：注册、启用、禁用、导出和执行工具。
- `read_file`：读取项目目录内的 UTF-8 文本文件。
- `glob`：使用 `**/*.py` 之类的模式查找文件路径。
- `grep`：在 UTF-8 文件中逐行查找字面文本，返回路径、行号和内容。
- 三个工具都限制在项目目录内；`glob` 和 `grep` 还会跳过 `.git`、`.env`、`node_modules`、`out` 和 `__pycache__`。
- Agent 发出 `tool_use`、`tool_result` 事件，页面用同一个调用 ID 更新工具卡片。

## 推荐阅读顺序

1. `src/jixue/tools/base.py`：先认识工具合同、上下文和结果。
2. `src/jixue/tools/read_file.py`：看最短的具体工具。
3. `src/jixue/tools/glob.py`：看怎样按模式查找路径。
4. `src/jixue/tools/grep.py`：看怎样遍历文件并返回行号。
5. `src/jixue/tools/registry.py`：看工具怎样集中管理。
6. `src/jixue/agent.py`：看 `Agent.run()` 怎样执行工具并继续循环。
7. `src/jixue/llm/adapters/anthropic_client.py`：最后看领域类型怎样转成 SDK 类型。

如果只抓主线，先读第 1、2、5、6 个文件。

## 工具接口是什么意思

Python 使用蛇形命名，所以设计稿里的 `inputSchema` 在代码中叫 `input_schema`。

| 方法 | 初学者可以这样理解 |
| --- | --- |
| `name` | 模型调用工具时使用的名字 |
| `description` | 告诉模型什么时候应该调用它 |
| `input_schema` | 用 JSON Schema 规定参数格式 |
| `execute` | 校验通过后真正执行工作 |
| `is_read_only` | 是否只读取、不修改外部状态 |
| `is_destructive` | 是否可能造成难恢复的改变 |
| `is_concurrency_safe` | 相同时间执行多个调用是否安全 |
| `category` | UI 分类，例如 file、search、shell |
| `validate_input` | 执行前尽早拒绝错误参数 |

`ToolResult` 只有三个核心字段：

```text
content   给模型看的成功内容或错误信息
is_error  本次工具是否失败
metadata  只给 UI 的附加信息，不发给模型
```

文件不存在是模型可以利用的反馈，不是程序崩溃。因此工具返回 `is_error=True`，让模型换路径重试。

## 三个工具分别做什么

| 工具 | 输入 | 输出 | 是否读取内容 |
| --- | --- | --- | --- |
| `read_file` | `path` | 完整文件内容 | 是 |
| `glob` | `pattern` | 匹配到的相对路径 | 否 |
| `grep` | `pattern`、可选 `path` | 路径、行号、匹配行 | 是 |

`grep` 当前使用字面匹配，不是正则表达式。例如搜索 `Agent.run` 就只找完全相同的字符。这样代码更短，也避免复杂正则让搜索长时间卡住。

`glob` 最多返回 200 个文件，`grep` 最多返回 100 条结果；超出时会给模型一个截断提示，防止一条工具结果占用太多 Token。

## 一次工具调用怎样跑完

下面是当前真实链路，不依赖测试模型：

```text
用户说“查找所有 Python 文件”
  → Electron 发送 chat.send
  → Agent.run() 取得对话历史
  → ToolRegistry.to_api_format() 导出 read_file、glob、grep 的 Schema
  → LLM 返回 tool_use(name="glob", input={pattern: "**/*.py"})
  → Agent 发出 tool_use，页面创建“执行中”工具卡片
  → ToolRegistry.execute() 按名字找到 glob
  → BaseTool.execute() 先调用 validate_input()
  → glob 在项目目录内查找并返回 ToolResult
  → Agent 发出 tool_result，页面更新原工具卡片
  → Agent 把 assistant tool_use + user tool_result 追加到临时历史
  → 下一轮 LLM 根据文件列表继续调用工具或生成最终回答
  → 没有新的 tool_use 时发出 loop_complete
```

核心代码可以缩成：

```python
工具定义 = registry.to_api_format()

while True:
    模型响应 = 调用 LLM(历史, 工具定义)
    if 模型没有请求工具:
        break

    for 工具调用 in 模型响应:
        结果 = await registry.execute(名称, context, 参数)
        把结果发给 UI
        把 tool_use 和 tool_result 加入历史
```

Agent 现在会读取 `is_concurrency_safe()`：连续安全调用组成一个并发批次，不安全调用各自单独执行。分批算法和事件顺序放在第三章讲。

## is_concurrency_safe 怎样判断

这个方法不是 Python 自动分析出来的，也不是看见 `read_only=True` 就自动返回 True。它是工具作者根据副作用主动做出的承诺。

判断一个调用能否安全并发，至少检查四件事：

1. 会不会修改文件、数据库或进程状态？
2. 会不会和另一个调用共同修改同一份可变数据？
3. 执行顺序变化会不会改变最终结果？
4. 同时执行失败时，会不会留下半完成状态？

当前三个工具都只读取磁盘，每次调用只使用自己的局部变量，不修改共享状态，结果也不依赖执行顺序，因此它们都这样声明：

```python
concurrency_check=lambda _tool_input: True
```

未来工具的保守默认值：

| 工具 | 默认并发判断 | 原因 |
| --- | --- | --- |
| `read_file`、`glob`、`grep` | True | 只读、无共享可变状态 |
| `write_file` | False | 两次写入可能覆盖同一文件 |
| `edit_file` | False | 后一次编辑依赖前一次文件内容 |
| `bash` | False | 命令可能有任意副作用 |

方法接收 `tool_input`，是为了将来可以按本次参数判断。例如一个工具的只读模式可以并发，写入模式必须串行。当前三个只读工具不需要查看参数，所以使用 `_tool_input` 表示“参数存在，但这里不会使用”。

## JSON 参数为什么需要等到完整后再解析

模型可能把工具参数拆成多个流式碎片：

```text
content_block_start:  id=tool_1, name=glob
content_block_delta:  {"pattern":
content_block_delta:  "**/*.py"}
content_block_stop
```

单个 delta 不是完整 JSON。适配器先把 `partial_json` 追加到缓冲区，收到 `content_block_stop` 后再调用 `json.loads()`。解析失败也会产生错误 `ToolResult`，不会让 Agent Loop 崩溃。

## 启动和手动测试

确认项目根目录 `.env` 使用真实配置：

```env
JIXUE_LLM_MODE=configured
```

启动：

```powershell
conda activate mycoder
npm run dev
```

依次发送：

```text
请使用 glob 找到 src 目录下所有 Python 文件，只列出路径。
```

预期出现 `glob` 工具卡片，输入中有 `src/**/*.py`，输出是排序后的相对路径。

```text
请使用 grep 在 src 目录搜索 create_read_file_tool，并告诉我它在哪个文件的第几行。
```

预期出现 `grep` 工具卡片，结果格式类似：

```text
src/jixue/tools/read_file.py:6: def create_read_file_tool() -> BaseTool:
```

自动检查：

```powershell
conda run --no-capture-output -n mycoder pytest tests/tools/test_tools.py -v
npm run test:all
conda run --no-capture-output -n mycoder ruff check .
conda run --no-capture-output -n mycoder mypy src tests
npm run test:electron
```

## 常见坑

- `pattern` 或 `path` 为空：参数校验返回失败结果，工具函数不会运行。
- 使用绝对路径或 `../`：工具会拒绝访问项目目录之外的位置。
- 把 grep 的 `pattern` 当成正则：当前只做字面匹配。
- 搜索整个 `node_modules`：内置工具会跳过依赖和构建目录。
- 搜索 `.env`：为了避免密钥进入模型上下文，glob 和 grep 会跳过它。
- 工具被禁用或名字错误：Registry 返回“工具不存在或已禁用”。
- 工具卡片一直“执行中”：检查 `tool_result.id` 是否等于 `tool_use.id`。
- 只要 `is_read_only=True` 就认为可并发：只读工具仍可能共享游标或受严格限流，并发安全必须单独声明。

## 本章变更记录

- 建立 `Tool`、`BaseTool`、`ToolResult` 和 `ToolRegistry`。
- 新增限制在项目目录内的 `read_file`。
- 支持流式 `tool_use` JSON 拼接和错误降级。
- 支持工具结果回传、UI 卡片和 Agent Loop。
- 新增 `glob` 和 `grep`，并在真实 Bridge 启动时注册。
- 三个只读工具声明可以安全并发。
- 文件扫描通过 `asyncio.to_thread()` 进入工作线程，多个安全调用可以真正重叠。

## 自测题与答案

**问：文件不存在为什么不让程序直接报内部错误？**

答：模型可以根据错误更换路径，所以它是有价值的 `ToolResult`。

**问：glob 和 grep 有什么区别？**

答：glob 按路径模式找文件，不读取内容；grep 打开文本文件并查找具体文字。

**问：metadata 为什么不发给模型？**

答：它主要服务 UI，例如扫描文件数。发给模型会浪费 Token。

**问：为什么 `is_read_only()` 和 `is_concurrency_safe()` 不能合并？**

答：只读只说明不修改外部状态，不代表没有共享游标、连接或限流；是否能同时运行需要独立判断。

**问：为什么并发判断要接收 input？**

答：同一个工具可能根据参数进入只读或写入模式。执行引擎需要针对这一次具体调用做决定。
