# 第 2 章：工具系统

本章正在开发。工具定义已经接入 LLM，流式 `tool_use` 已能显示在 UI；工具还不会自动执行，也没有 Agent Loop。

## 当前已经实现

- `Tool`：规定每个工具必须提供哪些方法。
- `BaseTool`：统一处理参数校验和可修复错误。
- `ToolResult`：把成功或失败都作为结果返回。
- `ToolRegistry`：注册、启用、禁用和导出工具定义。
- `read_file`：读取项目目录内 UTF-8 文件的第一个真实工具。
- Anthropic 适配器：拼接工具输入的 JSON 碎片，转换成霁雪事件。
- Bridge 和 UI：转发 `tool_use`，显示工具名、输入或解析错误。

下一小步会执行一次工具并返回 `tool_result`。第三章才会把它变成可持续循环的 Agent Loop。

## 推荐阅读顺序

1. `src/jixue/tools/base.py`：先认识 Tool、BaseTool 和 ToolResult。
2. `src/jixue/tools/read_file.py`：看一个具体工具怎样用工厂函数创建。
3. `src/jixue/tools/registry.py`：看工具怎样集中管理。
4. `src/jixue/llm/adapters/anthropic_client.py`：看 JSON 碎片怎样拼接。
5. `src/jixue/bridge/application.py` 和 `App.tsx`：看事件怎样到达工具卡片。

## 接口方法是什么意思

Python 使用蛇形命名，所以设计稿里的 `inputSchema` 在代码里叫 `input_schema`。

| 方法 | 作用 |
| --- | --- |
| `name` | API 看到的工具名 |
| `description` | 告诉模型何时使用工具 |
| `input_schema` | 用 JSON Schema 描述参数 |
| `execute` | 校验后执行工具 |
| `is_read_only` | 是否只读取数据 |
| `is_destructive` | 是否可能造成难恢复的改变 |
| `is_concurrency_safe` | 是否能与其他安全工具并发 |
| `category` | 给 UI 分类，如 file、shell |
| `validate_input` | 执行前拒绝错误参数 |

## 工具自身怎样执行

以读取 `README.md` 为例：

1. 启动时创建 `ToolRegistry`。
2. 调用 `registry.register(create_read_file_tool())` 注册工具。
3. 执行器通过 `registry.get("read_file")` 取得已启用工具。
4. `BaseTool.execute()` 先调用 `validate_input()`。
5. 参数合法后才进入 `read_file()` 执行函数。
6. 文件内容放进 `ToolResult.content`，字符数和路径放进 `metadata`。
7. 文件不存在等可修复问题返回 `is_error=True`，不会让程序崩溃。

最核心的伪代码：

```python
tool = registry.get(模型请求的工具名)
result = await tool.execute(context, 模型给的参数)

if result.is_error:
    把错误结果发回模型，让它修改参数
else:
    把 content 发回模型，把 metadata 留给 UI
```

`metadata` 不发给模型，避免路径、耗时等界面信息浪费 Token。

## Registry 怎样生成 API 工具定义

`registry.to_api_format()` 只输出普通字典：

```python
{
    "name": "read_file",
    "description": "...",
    "input_schema": {"type": "object", "properties": {...}}
}
```

这里没有导入 Anthropic SDK。后续由适配器把普通数据交给 SDK，这样更换供应商不会影响工具本身。工具按名称排序，确保 API 前缀稳定，更容易命中 Prompt Cache；这是本次 `Codex-api` 技能对实现产生的直接影响。

## 模型请求工具的链路

模型的工具参数不是一次到齐，而是多个 JSON 字符串碎片：

```text
content_block_start:  id=tool_1, name=read_file
content_block_delta:  {"path":
content_block_delta:  "README.md"}
content_block_stop
```

霁雪按 `index` 找到对应缓冲区，依次追加 `partial_json`，结束时才调用 `json.loads()`。成功后产生：

```text
LLM TOOL_USE → Bridge tool_use → reducer tool_received → UI 工具卡片
```

若 JSON 不完整或不是对象，适配器仍产生带 `tool_error` 的事件。程序继续接收 Token 用量和完成事件，不会因为一次参数错误崩溃。

## 手动测试

在项目根目录执行：

```powershell
conda run --no-capture-output -n mycoder python -c "import asyncio; from pathlib import Path; from jixue.tools import ToolContext, create_read_file_tool; tool=create_read_file_tool(); result=asyncio.run(tool.execute(ToolContext(Path.cwd()), {'path':'README.md'})); print(result.content[:100])"
```

正常现象：终端打印根 README 的前 100 个字符。

要手动观察工具卡片，需要使用真实模型并发送“请读取 README.md”。当前阶段只会显示请求，不会返回文件内容；请注意真实请求可能产生费用。FakeLLM 不会主动请求工具。

自动测试：

```powershell
conda run --no-capture-output -n mycoder pytest tests/tools -q
conda run --no-capture-output -n mycoder ruff check src tests/tools
conda run --no-capture-output -n mycoder mypy src tests/tools
```

## 常见坑

- `path` 为空：返回错误 ToolResult，执行函数不会运行。
- 文件不存在：返回 `is_error=True`，Agent 将来可以换路径重试。
- 使用 `../` 逃出项目目录：`read_file` 会拒绝。
- 工具被禁用：`registry.get()` 返回 `None`，对 Agent 来说等同于不存在。
- 同名工具注册两次：Registry 立即报错，避免实际执行时选错工具。
- 工具卡片出现但没有结果：当前尚未接入 `tool_result`，属于本步预期。
- JSON 参数损坏：卡片显示解析错误，但 Bridge 会继续运行。

## 本章变更记录

- 新增最小工具合同、BaseTool 和 ToolResult。
- 新增 ToolRegistry。
- 新增 read_file 工具和 5 项本地测试。
- 工具定义接入 LLM 请求，并保持 Prompt Cache。
- 新增流式 JSON 拼接、错误降级、Bridge 事件和 UI 工具卡片。

## 自测题与答案

**问：文件不存在为什么不直接让 Agent 崩溃？**

答：这是模型可以修正的执行反馈。返回错误结果后，模型可以换一个文件名重试。

**问：为什么 Registry 只导出启用的工具？**

答：模型只能调用当前允许使用的工具；禁用后就不应继续出现在 API 请求中。

**问：`read_file` 为什么可以并发？**

答：它只读取文件，不修改共享状态，同时读取多个文件通常互不影响。

**问：为什么不能每收到一个 JSON 碎片就解析？**

答：单个碎片通常不是完整 JSON。必须等 `content_block_stop` 后拼完整再解析。
