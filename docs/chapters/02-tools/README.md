# 第 2 章：工具系统

本章正在开发。当前只完成最小工具底座，不接 LLM、不改 UI，也没有 Agent Loop。

## 当前已经实现

- `Tool`：规定每个工具必须提供哪些方法。
- `BaseTool`：统一处理参数校验和可修复错误。
- `ToolResult`：把成功或失败都作为结果返回。
- `ToolRegistry`：注册、启用、禁用和导出工具定义。
- `read_file`：读取项目目录内 UTF-8 文件的第一个真实工具。

下一小步才会扩展 LLM 流事件，接收 `tool_use` 的 JSON 碎片。第三章才会执行完整 Agent Loop。

## 推荐阅读顺序

1. `src/jixue/tools/base.py`：先认识 Tool、BaseTool 和 ToolResult。
2. `src/jixue/tools/read_file.py`：看一个具体工具怎样用工厂函数创建。
3. `src/jixue/tools/registry.py`：看工具怎样集中管理。

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

## 一次工具执行怎么跑

以读取 `README.md` 为例：

1. 启动时创建 `ToolRegistry`。
2. 调用 `registry.register(create_read_file_tool())` 注册工具。
3. 将来模型请求 `read_file` 时，Agent 调用 `registry.get("read_file")`。
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

## 手动测试

在项目根目录执行：

```powershell
conda run --no-capture-output -n mycoder python -c "import asyncio; from pathlib import Path; from jixue.tools import ToolContext, create_read_file_tool; tool=create_read_file_tool(); result=asyncio.run(tool.execute(ToolContext(Path.cwd()), {'path':'README.md'})); print(result.content[:100])"
```

正常现象：终端打印根 README 的前 100 个字符。

当前 UI 不会显示工具调用，这是尚未实现，不是故障。

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

## 本章变更记录

- 新增最小工具合同、BaseTool 和 ToolResult。
- 新增 ToolRegistry。
- 新增 read_file 工具和 5 项本地测试。

## 自测题与答案

**问：文件不存在为什么不直接让 Agent 崩溃？**

答：这是模型可以修正的执行反馈。返回错误结果后，模型可以换一个文件名重试。

**问：为什么 Registry 只导出启用的工具？**

答：模型只能调用当前允许使用的工具；禁用后就不应继续出现在 API 请求中。

**问：`read_file` 为什么可以并发？**

答：它只读取文件，不修改共享状态，同时读取多个文件通常互不影响。
