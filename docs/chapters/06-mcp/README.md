# 第 6 章：MCP 协议

本章正在开发。第一步已经完成：霁雪能启动一个本地 stdio MCP Server，发现它的工具，并让现有 Agent Loop 像调用内置工具一样调用它。

## 当前成果

- 使用官方 Python MCP SDK，不自己拼协议消息。
- 启动时读取 `config/mcp.json` 和本地的 `config/mcp.local.json`。
- 完成 `initialize`、`notifications/initialized`、`tools/list` 和 `tools/call`。
- MCP SDK 类型只留在 `src/jixue/mcp/client.py`，Agent 不依赖 SDK。
- MCP 工具包装后进入原来的 `ToolRegistry`，原有权限、工具卡片和 Agent Loop 全部复用。
- 退出 Bridge 时关闭 MCP session 和它启动的子进程。

这一步只支持 stdio。后台连接状态、延迟加载、ToolSearch 和 Streamable HTTP 留到后续小步。

## 推荐阅读顺序

1. `config/mcp.json`：先看 Server 配置从哪里来。
2. `src/jixue/mcp/client.py`：看 `MCPTransport` 和 `StdioMCPClient`。
3. `src/jixue/mcp/tool.py`：看外部工具怎样变成霁雪自己的 `Tool`。
4. `src/jixue/bridge/server.py`：搜索 `_run_bridge()`，看连接和注册发生在哪里。
5. `src/jixue/tools/registry.py`：复习工具如何导出给模型、如何按名称执行。
6. `src/jixue/agent.py`：最后看现有循环怎样直接使用注册后的 MCP 工具。

## 先分清四个角色

~~~text
MCP Server
  提供工具的外部程序，例如地图、数据库或本地 echo 程序

transport
  Client 与 Server 的连接方式；本步只有 stdio，后续会增加 HTTP

StdioMCPClient
  负责启动进程、握手、发现工具和发起调用

MCPToolWrapper
  把 MCP 工具翻译成霁雪已有的 Tool 接口
~~~

`MCPTransport` 是很小的合同，只包含 `connect()`、`call_tool()` 和 `close()`。以后新增 HTTP transport 时，包装器和 Agent 都不用改。

## 启动时发生什么

~~~text
Electron 启动 Python Bridge
  → main() 创建内置 ToolRegistry
  → load_stdio_server_configs() 读取 mcp.json
  → mcp.local.json 覆盖同名本地配置
  → StdioMCPClient.connect() 启动 MCP 子进程
  → 官方 SDK 发送 initialize
  → SDK 自动发送 notifications/initialized
  → list_tools() 获取 Server 的工具定义
  → MCPToolWrapper 把 echo 包装成 mcp__demo__echo
  → ToolRegistry.register() 注册包装后的工具
  → 创建 Agent 并开始读取桌面消息
~~~

工具名增加 `mcp__Server名__工具名` 前缀，是为了避免两个 Server 都有 `search` 时发生重名。

## 一条消息怎样调用 MCP

假设用户要求模型调用 demo Server 的 echo：

~~~text
用户发送消息
  → Agent 从 ToolRegistry 取得全部工具 schema
  → self._llm.stream(system, history, tools)
  → 模型返回 tool_use
       name = mcp__demo__echo
       input = {"text": "你好"}
  → Agent 沿用第五章权限检查
  → ToolRegistry.execute("mcp__demo__echo", ...)
  → MCPToolWrapper.execute()
  → transport.call_tool("echo", {"text": "你好"})
  → 官方 MCP ClientSession 发送 tools/call
  → demo Server 返回 "MCP echo：你好"
  → StdioMCPClient 转成 MCPCallResult
  → Wrapper 转成霁雪 ToolResult
  → Agent 发出 tool_result 事件，页面更新工具卡片
  → Agent 把同一个 tool_use_id 的结果加入历史
  → 第 2 轮 LLM 生成最终回答
  → loop_complete，输入框恢复
~~~

注意：`agent.py` 没有增加“如果是 MCP 就怎样”的判断。Agent 只认识统一的 `Tool`，这就是包装器存在的意义。

核心伪代码只有这些：

~~~python
definitions = await client.connect()
for definition in definitions:
    registry.register(MCPToolWrapper(client, definition))

# Agent 仍走原来的入口
result = await registry.execute(tool_name, context, tool_input)
~~~

## 配置文件

仓库中的 `config/mcp.json` 是可提交的公共配置，目前为空。本机配置写在会被 Git 忽略的 `config/mcp.local.json`：

~~~json
{
  "servers": {
    "demo": {
      "transport": "stdio",
      "command": "python",
      "args": ["tests/mcp/demo_server.py"],
      "enabled": true
    }
  }
}
~~~

`command` 是要启动的程序，`args` 是传给它的参数，`enabled=false` 可以暂时关闭这个 Server。本步不在配置中保存 API Key。

## 启动与手动测试

本机已经放好了 Git 忽略的 echo Server 和 `mcp.local.json`。

### 1. 先测 MCP 本身

~~~powershell
conda activate mycoder
pytest -q tests/mcp/test_stdio_mcp.py
~~~

看到 `1 passed`，表示下面这条真实链路成功：

~~~text
启动子进程 → 握手 → tools/list → 注册 → tools/call → 关闭子进程
~~~

### 2. 再从桌面测完整 Agent

~~~powershell
conda activate mycoder
npm run dev
~~~

确保 `.env` 使用真实模型，然后发送：

~~~text
请调用 mcp__demo__echo，把“你好，霁雪”传给它，再告诉我结果。
~~~

预期看到名为 `mcp__demo__echo` 的工具卡片，结果是 `MCP echo：你好，霁雪`，随后模型在第 2 轮给出最终回复。demo 工具声明为只读，所以默认“修改需确认”模式不会弹确认框。

关闭窗口后不应出现 JavaScript 错误弹窗，也不应残留 demo Server 进程。

## 权限与并发怎样判断

- `readOnlyHint=true`：包装为只读工具，Plan 模式可见。
- `destructiveHint` 未提供：采用保守规则；只有明确只读才视为非破坏性。
- MCP 标准没有 `concurrencySafe` 字段，因此当前所有 MCP 工具的 `is_concurrency_safe()` 都返回 `False`。

最后一条意味着模型一次请求两个 MCP 工具时会串行执行。不能因为“只读”就擅自推断 Server 支持并发；后续只有得到明确的本地配置声明，才考虑开放并发。

## 常见坑

- MCP Server 往 stdout 打日志：stdio 的 stdout 只能传协议数据，日志必须写 stderr。
- 忘记 `initialize()`：未完成握手就调用 `tools/list` 会失败。
- 自己再发一次 `initialized`：官方 SDK 的 `initialize()` 已经自动发送，不要重复。
- 直接把 MCP SDK 类型传进 Agent：这样以后升级 SDK 或增加 HTTP 时会污染核心代码。
- 使用相对工作目录启动：本项目固定把 MCP 子进程 cwd 设为项目根目录。
- 把未知 MCP 工具默认当安全：Server 没有 annotations 时应保守判断。
- 忘记关闭 client：桌面退出后会残留子进程。
- 期待 Fake 自动选择 MCP 工具：Fake 只用于自动测试；桌面全链路请用真实模型。

## 变更记录

- 第 1 步：完成 stdio transport、握手、工具发现、包装注册、调用、结果转换和退出清理。

## 自测题与答案

**问：MCP Server 和 Agent 是同一个进程吗？**

答：不是。stdio 模式下，Bridge 会启动一个独立子进程，并通过输入输出管道与它通信。

**问：为什么工具叫 `mcp__demo__echo`，调用 Server 时却传 `echo`？**

答：带前缀的名字用于霁雪内部防重名；Wrapper 保存着远端原名，真正 `tools/call` 时会还原成 `echo`。

**问：MCP 工具为什么能直接出现在原来的工具卡片中？**

答：Wrapper 实现了与内置工具相同的 `Tool` 接口，Agent 产生的仍是原来的 `tool_use` 和 `tool_result` 事件。

**问：这一小步修改了 Agent Loop 吗？**

答：没有。只在创建 Agent 之前向 Registry 注册新工具，证明现有循环不关心工具来源。

**问：只读 MCP 工具一定能并发吗？**

答：不一定。只读只说明没有副作用，不代表 Server 的 session 或内部状态支持并发，所以本步默认串行。

**问：第六章结束了吗？**

答：没有。下一步是后台连接与状态：某个 MCP Server 失败时不阻止霁雪启动，并把连接状态显示给 UI。
