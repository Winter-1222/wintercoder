# 第七章：上下文管理

本章解决一个问题：Agent 工作久了以后，怎样避免把越来越多的内容全部塞给模型。

当前只完成第 1 步：**单个大工具结果落盘**。旧结果清理、手动压缩和自动压缩还没有实现。

## 1. 这一小步完成了什么

- 工具结果不超过 50,000 字符：保持原样，不写文件。
- 工具结果超过 50,000 字符：完整内容写入 `.jixue/tool-results/`。
- 对话历史和界面只接收“开头预览 + 结尾预览 + artifact_id”。
- 新增 `read_artifact`：模型可以按关键词搜索，也可以按字符范围分段读取。
- `read_file` 新增行号范围，适合只重读大文件中真正需要的部分。
- `.jixue`、`.env` 和 `.env.*` 不允许被文件读取与搜索工具访问。

这里的 artifact 可以先理解成“工具大结果的临时档案”。它不是长期记忆，也不是聊天记录。

## 2. 推荐阅读顺序

1. `src/jixue/context.py`
   先看 50,000 字符判断、落盘和预览是怎样生成的。
2. `src/jixue/agent.py`
   搜索 `_execute_tool_call`，看所有工具结果怎样统一经过上下文保护。
3. `src/jixue/tools/read_artifact.py`
   看模型怎样按需取回档案中的小片段。
4. `src/jixue/tools/read_file.py`
   看普通文件怎样按起止行读取。
5. `src/jixue/bridge/server.py`
   看 `read_artifact` 怎样注册进 Agent 的工具列表。

第一遍只顺着上面的链路读，不必先研究每个校验分支。

## 3. 一条大工具结果怎样跑起来

```text
用户发送任务
  ↓
模型返回 tool_use
  ↓
ToolRegistry 执行对应工具，得到 ToolResult
  ↓
Agent._execute_tool_call 调用 ToolResultStore.prepare
  ↓
结果是否超过 50,000 字符？
  ├─ 否：原样返回
  └─ 是：完整内容写入 .jixue/tool-results
           ↓
         生成有界预览和 artifact_id
           ↓
         同一个预览发往两个地方
           ├─ tool_result 事件 → Electron 界面
           └─ APIToolResultBlock → 下一轮模型请求
                                      ↓
                         模型信息够用：直接回答
                         模型需要细节：调用 read_artifact
```

关键代码可以缩成下面几句伪代码：

```python
result = await registry.execute(tool_name, tool_input)
safe_result = await result_store.prepare(tool_use_id, tool_name, result)

yield tool_result_event(safe_result)       # 给界面
history.append(api_tool_result(safe_result))  # 给下一轮模型
```

为什么放在 Agent 中统一处理，而不是每个工具各写一次？

因为内置工具和 MCP 工具最后都会经过 `_execute_tool_call`。只守住这一个出口，就不会漏掉某个工具。

## 4. 落盘以后为什么仍可能占上下文

落盘不是让内容“永远免费”，而是把一次性塞入 60,000 字符，改成模型按需要取 8,000 字符。

例如一个 100,000 字符结果：

```text
旧做法：下一轮直接携带约 100,000 字符
新做法：下一轮先携带约 8,000 字符预览
        如果预览够用，剩余内容永远不进入上下文
        如果不够，再搜索或读取一个不超过 20,000 字符的片段
```

所以 `read_artifact` 返回的片段仍会占用上下文。真正的收益是：**由模型选择需要的少量内容，而不是无条件装入全部内容。**

## 5. 怎样按需读取

`read_artifact` 不接受任意文件路径，只接受落盘提示中的 `artifact_id`。

优先搜索关键词：

```json
{
  "artifact_id": "toolu_abc-1a2b3c4d5e6f",
  "search": "报错关键词"
}
```

搜索不到或需要上下文时，再分段读取：

```json
{
  "artifact_id": "toolu_abc-1a2b3c4d5e6f",
  "offset": 8000,
  "limit": 8000
}
```

- `offset`：从第几个字符开始，0 表示开头。
- `limit`：本次最多返回多少字符，默认 8,000，最大 20,000。
- 结果没读完时，会提示下一次该使用的 offset。

`read_artifact` 虽然是只读工具，但目前会先把一份档案加载到内存，所以它声明为“不允许并发”。这是 `is_concurrency_safe` 根据具体资源开销做判断的例子：只读不等于一定适合并发。

普通项目文件则继续使用 `read_file`。它现在可以按行读取：

```json
{
  "path": "src/jixue/agent.py",
  "start_line": 200,
  "end_line": 260
}
```

不写行号仍可读取整个文件；如果结果过大，仍会经过统一落盘保护。

## 6. 文件为什么不会反复写

档案名来自唯一的 `tool_use_id`，并附加短哈希。写入使用 Python 的 `x` 模式：

- 文件不存在：创建并写入。
- 文件已经存在：不覆盖，也不重复写。

这样同一条工具结果在后续轮次重新整理历史时，不会反复写磁盘。

`.jixue/` 已被 Git 忽略，工具大结果不会进入提交记录。

## 7. 启动与手动测试

在项目根目录启动：

```powershell
conda activate mycoder
npm run dev
```

为了稳定制造一个大文件，可在项目根目录的 PowerShell 中执行：

```powershell
New-Item -ItemType Directory -Force tests/manual
$content = "BEGIN`n" + ("中" * 60000) + "`nJIXUE-CONTEXT-777`nEND"
Set-Content -LiteralPath tests/manual/large.txt -Value $content -Encoding utf8
```

使用真实模型发送：

```text
请读取 tests/manual/large.txt。如果结果被保存为 artifact，请使用 read_artifact
搜索 JIXUE-CONTEXT-777，然后告诉我是否找到了。
```

应该看到：

1. 第一轮调用 `read_file`。
2. 工具卡片只显示预览和 `artifact_id`，不会展示 60,000 个“中”。
3. 项目下出现 `.jixue/tool-results/` 和一个 `.txt` 档案。
4. 下一轮模型调用 `read_artifact` 搜索标记。
5. 模型最终回答找到了标记，界面仍能继续发送消息。

FakeLLM 仍可用于免费验证 `/read tests/manual/large.txt` 是否触发落盘，但它不会自主继续调用 `read_artifact`；完整 Agent 决策链要用真实模型测试。

## 8. 自动化检查

```powershell
conda run --no-capture-output -n mycoder pytest
conda run --no-capture-output -n mycoder ruff check .
conda run --no-capture-output -n mycoder mypy src
npm run test:frontend
npm run typecheck
npm run test:electron
```

测试文件位于本机 `tests/`，被 Git 忽略，不会提交。

## 9. 常见问题

### 为什么不把完整内容放进 metadata？

metadata 会交给界面。如果偷偷把完整结果放进去，内存和进程通信压力仍然存在，也容易意外泄漏，因此 metadata 只保存编号、长度和本地路径等小信息。

### 为什么 `read_file` 不能直接读取 `.jixue`？

否则模型可以绕过每次最多 20,000 字符的限制，一次读取完整档案。档案只能通过 `read_artifact` 受控访问。

### Bash 输出还会在工具内部截断吗？

不会。Bash 把完整结果交给统一的 `ToolResultStore`，由它落盘并生成预览。这样磁盘里保存的是完整结果，不是已经丢失尾部的截断结果。

用户在 Bash 执行期间点击停止时，Agent 的取消信号也会终止正在等待的命令进程，避免界面停了但命令仍在后台继续。

### 这一步已经解决所有上下文增长了吗？

没有。它只解决“单个工具结果特别大”。很多个中小结果、旧轮次和长聊天仍会累积，下一步才处理“当前活动上下文中该保留哪些内容”。

另外，当前工具仍是先在 Python 进程里得到完整结果，再判断是否落盘；这一小步保护的是模型上下文，不是无限大的进程内存。真正的超大命令输出以后可再改成边产生边写盘，本章先保持代码简单。

## 10. 变更记录

- 第 1 步：加入大工具结果落盘、有界预览、`read_artifact` 和 `read_file` 行范围读取。

## 11. 自测题与答案

### 1）50,000 字符的结果会落盘吗？

不会。只有“大于 50,000 字符”才落盘。

### 2）落盘之后，完整内容还会立刻发给模型吗？

不会。模型先收到预览和 `artifact_id`。

### 3）模型调用 `read_artifact` 后，返回片段占不占上下文？

占，但单次有上限，而且模型可以先搜索关键词，减少无关内容。

### 4）为什么落盘逻辑放在 Agent，不放在 `read_file`？

因为 Agent 是所有内置工具和 MCP 工具结果的共同出口，统一处理最不容易遗漏。

### 5）`read_artifact` 为什么不用文件路径？

只接受安全编号可以把访问范围固定在 `.jixue/tool-results`，避免它变成另一个任意文件读取工具。

### 6）本步骤和自动压缩有什么区别？

本步骤缩小单个工具结果；自动压缩会在整个活动上下文接近模型窗口上限时，把较旧对话整理成结构化摘要。
