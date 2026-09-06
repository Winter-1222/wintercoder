# 第 6 章：MCP 协议

本章已完成：霁雪能后台连接多个 stdio 或 Streamable HTTP MCP Server，显示连接状态，并让 Agent 像调用内置工具一样调用它们。

## 当前成果

- 使用官方 Python MCP SDK，不自己拼协议消息。
- 启动时读取 `config/mcp.json` 和本地的 `config/mcp.local.json`。
- 完成 `initialize`、`notifications/initialized`、`tools/list` 和 `tools/call`。
- MCP SDK 类型只留在 `src/jixue/mcp/client.py`，Agent 不依赖 SDK。
- MCP 工具包装后进入原来的 `ToolRegistry`，原有权限、工具卡片和 Agent Loop 全部复用。
- Bridge 不等待 MCP 就能先进入可聊天状态，多个 Server 各自并行连接。
- 单个 Server 失败只显示红色状态，不会阻止 Bridge、其他 Server 或内置工具。
- Electron Main 缓存每个 Server 的状态，页面晚一点订阅也不会丢失结果。
- 配置中的 `transport` 决定创建 `StdioMCPClient` 还是 `StreamableHTTPMCPClient`。
- 两种连接共用握手、分页发现、调用结果转换和关闭逻辑。
- HTTP 错误不回显完整 URL，避免查询参数里的 API Key 进入日志或模型上下文。
- HTTP URL 支持 `${变量名}`，真实 Key 只写在项目根目录 `.env`。
- MCP 工具 60 秒无响应会报错；用户停止时会立即放弃模型或工具等待并释放下一轮聊天。
- 启动连接失败会在后台再试两次；每次使用新客户端，最终失败才显示红色状态。
- 退出 Bridge 时关闭 MCP session 和它启动的子进程。

当前工具数量很少，直接把全部 Schema 发给模型。ToolSearch 已评估但暂不实现，原因见后文。

## 推荐阅读顺序

1. `config/mcp.json`：先看 Server 配置从哪里来。
2. `src/jixue/mcp/client.py`：看配置怎样选择 stdio/HTTP，以及两者怎样共用 MCP session。
3. `src/jixue/mcp/tool.py`：看外部工具怎样变成霁雪自己的 `Tool`。
4. `src/jixue/bridge/server.py`：看后台并行连接、状态事件和注册发生在哪里。
5. `apps/desktop/src/shared/protocol.ts` 与 `main/bridge-process.ts`：看 Main 如何缓存状态。
6. `apps/desktop/src/renderer/src/state.ts` 与 `App.tsx`：看状态怎样显示到侧边栏。
7. `src/jixue/tools/registry.py`：复习工具如何导出给模型、如何按名称执行。
8. `src/jixue/agent_runtime/execution.py`：最后看工具执行器怎样直接使用注册后的 MCP 工具。

## 先分清四个角色

~~~text
MCP Server
  提供工具的外部程序，例如地图、数据库或本地 echo 程序

transport
  Client 与 Server 的连接方式；当前支持 stdio 和 Streamable HTTP

StdioMCPClient / StreamableHTTPMCPClient
  一个启动本地进程，一个连接远程 URL；后续 MCP 步骤完全相同

MCPToolWrapper
  把 MCP 工具翻译成霁雪已有的 Tool 接口
~~~

`MCPTransport` 是很小的合同，只包含 `connect()`、`call_tool()` 和 `close()`。包装器和 Agent 不关心底层走进程管道还是网络。

## 启动时发生什么

~~~text
Electron 启动 Python Bridge
  → main() 创建内置 ToolRegistry
  → 立即创建 Agent 和 BridgeServer
  → Bridge 可以先回复 bridge.ready，输入框不必等待 MCP
  → 后台任务读取 mcp.json
  → mcp.local.json 覆盖同名本地配置
  → 每个 Server 建立独立异步连接任务
  → 发送 mcp.status(connecting)
  → create_mcp_client() 查看 transport
      stdio → 启动本地 MCP 子进程
      streamable_http → 连接远程 URL
  → 网络失败：关闭旧客户端，等待 1 秒、3 秒后重试
  → 最多 3 次仍失败：发送 mcp.status(failed)
  → 官方 SDK 发送 initialize
  → SDK 自动发送 notifications/initialized
  → list_tools() 获取 Server 的工具定义
  → MCPToolWrapper 把 echo 包装成 mcp__demo__echo
  → ToolRegistry.register() 注册包装后的工具
  → 发送 mcp.status(connected)
  → Electron Main 缓存状态
  → Renderer 的 BridgeState 更新侧边栏
~~~

工具名增加 `mcp__Server名__工具名` 前缀，是为了避免两个 Server 都有 `search` 时发生重名。

如果一个 Server 连接失败，最后一段会变成：

~~~text
connect() 失败
  → 转成 mcp.status(failed)
  → 侧边栏显示红点和“连接失败”
  → 后台继续等待其他 Server
  → Bridge 仍是 ready，内置工具仍可使用
~~~

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

注意：`agent_runtime/execution.py` 没有增加“如果是 MCP 就怎样”的判断。Agent 只认识统一的 `Tool`，这就是包装器存在的意义。

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

`command` 是要启动的程序，`args` 是传给它的参数，`enabled=false` 可以暂时关闭这个 Server。

Streamable HTTP 只需要 URL。例如高德官方提供的地址应写进不会提交的 `config/mcp.local.json`：

~~~json
{
  "servers": {
    "amap-maps": {
      "transport": "streamable_http",
      "url": "https://mcp.amap.com/mcp?key=${AMAP_MCP_KEY}",
      "enabled": true
    }
  }
}
~~~

真实 Key 只在项目根目录 `.env` 中写一次：`AMAP_MCP_KEY=你的Key`。配置文件保存的是变量名占位符，Bridge 启动时才替换；缺少变量时只报告变量名，不显示 URL。高德要求使用“Web 服务”类型的 Key；配置格式可对照[高德官方 MCP 接入说明](https://developer.amap.com/api/mcp-server/gettingstarted)。

## 启动与手动测试

本机已经放好了 Git 忽略的 echo Server 和 `mcp.local.json`。

### 1. 先测 MCP 本身

~~~powershell
conda activate mycoder
pytest -q tests/mcp
~~~

看到 `5 passed`，表示 stdio、HTTP、配置选择、失败隔离和后台重连都成功：

~~~text
启动子进程 → 握手 → tools/list → 注册 → tools/call → 关闭子进程
连接 HTTP → 握手 → tools/list → tools/call → 关闭远程会话
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

### 3. 用高德测试 Streamable HTTP

先按上一节把高德配置写进 `config/mcp.local.json`，再重启 `npm run dev`。侧栏应出现：

~~~text
amap-maps  已连接 · N 个工具
~~~

然后发送一个容易触发地图工具的问题，例如：

~~~text
请使用高德地图工具查询北京故宫附近的咖啡店，并告诉我前三个结果。
~~~

预期页面显示 `mcp__amap-maps__...` 工具卡片，工具完成后模型再给最终回答。工具名由 Server 实际返回，后半段可能随高德版本变化。

工具执行过程中点击停止，页面应很快显示“已停止”，并且可以立刻发送下一条消息。已经发到远端的请求不保证服务端撤销，但本地 Agent 不会继续被它卡住。

### 4. 手动测试失败隔离

在 Git 忽略的 `config/mcp.local.json` 中临时增加：

~~~json
"broken": {
  "transport": "stdio",
  "command": "jixue-command-that-does-not-exist"
}
~~~

重新运行 `npm run dev`。预期 Python Bridge 仍为绿色，demo 显示“已连接”；broken 会保持橙色并提示两次重试，第三次仍失败才变红。聊天和内置工具始终可用。测完删除这段临时配置。

## 为什么现在不做 ToolSearch

Claude 原生 ToolSearch 在服务端搜索：客户端仍发送全部 Schema，但服务端只把命中的定义放进模型上下文。DeepSeek 的 Anthropic 兼容接口没有声明支持这套专有字段。

我们也可以自己做客户端搜索，但第一次调用会变成“搜索一轮、调用一轮、回答一轮”，还要动态修改 API 的工具列表。当前只有几个内置工具和少量 MCP 工具，这些复杂度不值得。因此本章先直接发送全部 Schema；以后工具达到几十个时，再按供应商能力选择原生搜索或本地预筛选。

## 权限与并发怎样判断

- `readOnlyHint=true`：包装为只读工具，Plan 模式可见。
- `destructiveHint` 未提供：采用保守规则；只有明确只读才视为非破坏性。
- MCP 标准没有 `concurrencySafe` 字段，因此当前所有 MCP 工具的 `is_concurrency_safe()` 都返回 `False`。

最后一条意味着模型一次请求两个 MCP 工具时会串行执行。不能因为“只读”就擅自推断 Server 支持并发；后续只有得到明确的本地配置声明，才考虑开放并发。

## 常见坑

- MCP Server 往 stdout 打日志：stdio 的 stdout 只能传协议数据，日志必须写 stderr。
- 把 `streamable_http` 写成 `streamableHTTP`：当前配置值固定使用小写加下划线。
- HTTP URL 少写 `/mcp`：普通网站首页不是 MCP 端点，必须使用服务商给出的完整地址。
- 把真实 Key 写进 MCP 配置或错误日志：Key 只放 `.env`，配置使用 `${AMAP_MCP_KEY}`，HTTP 错误只显示异常类型。
- 只在 `.env` 写 Key、配置却没有 `${AMAP_MCP_KEY}`：环境变量不会猜测自己应该放进 URL，配置里仍需保留这个占位符。
- 忘记 `initialize()`：未完成握手就调用 `tools/list` 会失败。
- 自己再发一次 `initialized`：官方 SDK 的 `initialize()` 已经自动发送，不要重复。
- 直接把 MCP SDK 类型传进 Agent：这样以后升级 SDK 或增加 HTTP 时会污染核心代码。
- 使用相对工作目录启动：本项目固定把 MCP 子进程 cwd 设为项目根目录。
- 把未知 MCP 工具默认当安全：Server 没有 annotations 时应保守判断。
- 忘记关闭 client：桌面退出后会残留子进程。
- 在连接 MCP 之前才启动 Bridge：一个慢 Server 会让整个界面一直停在“正在连接”。
- 只把状态直接推给 Renderer：页面尚未订阅时可能丢事件，所以 Main 还要缓存状态。
- 串行连接多个 Server：第一个超时会挡住后面的正常 Server，本项目为每个 Server 建立独立任务。
- 复用失败连接继续重试：HTTP session 可能已经损坏，所以每一次都创建全新客户端。
- 后台进程继承 Bridge 的 stdin：它可能干扰后续聊天命令；读取 Git 状态的子进程已经固定使用 DEVNULL。
- 期待 Fake 自动选择 MCP 工具：Fake 只用于自动测试；桌面全链路请用真实模型。

## 变更记录

- 第 1 步：完成 stdio transport、握手、工具发现、包装注册、调用、结果转换和退出清理。
- 第 2 步：完成并行后台连接、失败隔离、`mcp.status` 事件、Main 状态缓存和侧边栏展示。
- 第 3 步：完成 Streamable HTTP、transport 配置选择、本地真实 HTTP 测试和高德手测说明；ToolSearch 经评估后暂缓。
- 修复：模型或远程工具卡住时可以立即停止并继续聊天；MCP Key 改为从项目 `.env` 占位替换。
- 后台重连：单次连接限时 20 秒，失败后间隔 1 秒、3 秒重试，三次失败才结束。
- 稳定性修复：隔离 Git 子进程与 Bridge 输入管道；取消后的消息不再显示流式光标，并可继续发送新消息。

## 自测题与答案

**问：MCP Server 和 Agent 是同一个进程吗？**

答：不是。stdio 模式下，Bridge 会启动一个独立子进程，并通过输入输出管道与它通信。

**问：为什么工具叫 `mcp__demo__echo`，调用 Server 时却传 `echo`？**

答：带前缀的名字用于霁雪内部防重名；Wrapper 保存着远端原名，真正 `tools/call` 时会还原成 `echo`。

**问：MCP 工具为什么能直接出现在原来的工具卡片中？**

答：Wrapper 实现了与内置工具相同的 `Tool` 接口，Agent 产生的仍是原来的 `tool_use` 和 `tool_result` 事件。

**问：这一小步修改了 Agent Loop 吗？**

答：没有。后台任务直接向 Agent 持有的同一个 Registry 注册工具；Agent 下一次开始任务时会读取最新工具列表。

**问：只读 MCP 工具一定能并发吗？**

答：不一定。只读只说明没有副作用，不代表 Server 的 session 或内部状态支持并发，所以本步默认串行。

**问：第六章结束了吗？**

答：结束了。下一章开始做上下文管理，第一步处理超大工具结果落盘。

**问：HTTP 和 stdio 为什么能共用同一个 `MCPToolWrapper`？**

答：两种客户端都实现 `MCPTransport`，对外只暴露连接、调用和关闭。Wrapper 看不到 URL 或子进程，只拿统一的工具定义与结果。

**问：Claude 原生 ToolSearch 能减少客户端发送的数据吗？**

答：不能。完整 Schema 仍会发给 Anthropic 服务器，它减少的是实际进入模型上下文的定义。当前项目以 DeepSeek 为默认模型，所以不依赖这项专有能力。

**问：`.env` 已经有高德 Key，为什么配置里还要写 `${AMAP_MCP_KEY}`？**

答：`.env` 负责保存秘密值，配置负责说明这个值应该放在 URL 的哪个位置。启动时才把两者组合，因此 Key 不需要重复，也不会进入 Git。

**问：为什么重连时不复用原来的 `StreamableHTTPMCPClient`？**

答：失败可能已经关闭内部消息通道。新建客户端能拿到全新的 HTTP session，不会把第一次连接留下的坏状态带进下一次。
