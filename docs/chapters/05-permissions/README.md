# 第 5 章：五层权限防御

本章让 Agent 在工具真正运行前先判断风险。共分三步；现在已经完成第 2 步：后端能拦截、询问、等待决定，再把结果交还给模型。确认卡片界面留到第 3 步。

## 当前成果

- permission.py 给每个工具调用返回 ALLOW、DENY 或 ASK。
- Agent 在工具处理函数运行前依次检查：JSON 错误、工具存在性、参数、Plan 模式、权限。
- 新增 write_file、edit_file、bash 三个会改变状态的工具。
- ASK 会产生 permission_request；Bridge 接收 permission.respond 后唤醒 Agent。
- 用户拒绝、路径越界和危险命令都变成 is_error=true 的工具结果，Agent 不会崩溃。
- 点击停止也能唤醒正在等待确认的 Agent。
- 为避免当前页面没有确认按钮时卡住，三个新工具暂未注册到正式桌面端；第 3 步连 UI 时再注册。

## 推荐阅读顺序

1. src/jixue/permission.py：看 evaluate_permission() 怎样得到三种决定。
2. src/jixue/tools/write_tools.py：看完整写入和精确替换。
3. src/jixue/tools/bash.py：看命令怎样在项目目录执行。
4. src/jixue/agent.py：搜索 _check_tool_call()，再从 permission_request 往下读。
5. src/jixue/bridge/application.py：搜索 permission.respond，看 UI 的决定怎样送回来。
6. src/jixue/tools/base.py：最后理解 BaseTool 为什么能统一校验和包装错误。

## 三种权限结果

| 结果 | Agent 怎么做 | 例子 |
| --- | --- | --- |
| ALLOW | 直接执行 | 项目内 read_file |
| DENY | 不执行，生成失败的 tool_result | 路径越界、rm -rf / |
| ASK | 暂停当前调用，等待用户决定 | write_file、edit_file、普通 bash |

DENY 和用户拒绝都不是程序异常。模型会在下一轮看到失败原因，可以换方案或向用户解释。

## 一条需要确认的消息怎样跑起来

假设模型想创建 notes/todo.md，完整链路是：

~~~text
用户发送“创建一个待办文件”
  → 第 1 轮 LLM 返回 tool_use
       id = tool_123
       name = write_file
       input = {path, content}
  → Agent._check_tool_call()
       1. tool_use JSON 是否完整
       2. 工具是否存在
       3. 参数是否合法
       4. Plan 模式是否允许
       5. evaluate_permission() 返回 ASK
  → Agent 先创建一个 Future，再发 permission_request
  → Bridge 给 Electron 发送 permission_request
  → Agent 暂停在 await，不会偷偷执行 write_file
  → 用户选择允许或拒绝
  → Electron 发送 permission.respond
       target_request_id = 当前聊天请求
       tool_use_id = tool_123
       allow = true 或 false
  → Bridge 同时核对聊天 ID 和工具调用 ID
  → Agent.respond_permission() 填入 Future，Agent 被唤醒
       允许：执行 write_file
       拒绝：生成“用户拒绝”的错误 ToolResult
  → tool_result 使用原来的 tool_123
  → 第 2 轮 LLM 收到工具结果并给最终回复
  → loop_complete，输入框恢复
~~~

Future 可以先理解成一个“暂时没有答案的盒子”。Agent await 这个盒子时不会占着 CPU 空转；Bridge 收到用户选择后，把 true 或 false 放进去，Agent 才继续。

一定要先创建 Future 再发送 permission_request。否则页面回复很快时，回复可能先到，而 Agent 还没有准备好接收。

## ALLOW、DENY、ASK 三条分支

核心逻辑可以缩成：

~~~python
check = evaluate_permission(project_root, tool, tool_input)

if check.decision is DENY:
    result = ToolResult(check.reason, is_error=True)
elif check.decision is ASK:
    allowed = await wait_for_user()
    result = await tool.execute(...) if allowed else denied_result
else:
    result = await tool.execute(...)
~~~

真正代码还会保持多个工具结果的原顺序。只读安全工具仍可并发；write_file、edit_file 和 bash 默认 is_concurrency_safe() 为 false，所以各自串行执行。

## 三个新增工具

### write_file

接收 path 和 content，创建父目录后写入 UTF-8 文件。目标已存在时会整体覆盖，所以必须确认。单次内容上限为 1,000,000 字符。

### edit_file

接收 path、old_text、new_text。只有 old_text 在文件中恰好出现一次才替换；出现 0 次或多次都返回错误，文件保持原样。这样 Agent 不会猜错修改位置。

### bash

在项目根目录执行命令。Windows 使用 PowerShell，macOS/Linux 使用 Bash；最长运行 30 秒，返回内容最多约 50,000 字符。子进程会移除名称中包含 API_KEY、TOKEN、SECRET、PASSWORD 的环境变量。

bash 仍然不是完整操作系统沙箱。第五章的权限判断负责在执行前拦截和询问，不能把工具本身当成绝对安全边界。

## 五层防线进度

| 层级 | 当前状态 |
| --- | --- |
| 1. 灾难命令硬拦截 | 已接入 Agent，命中后不询问也不执行 |
| 2. 项目路径沙箱 | 已接入 Agent，文件和 glob 不能越界 |
| 3. 细粒度权限规则 | 下一步和权限模式一起做最小配置 |
| 4. 整体权限模式 | 下一步先完成默认“修改需确认”界面 |
| 5. HITL 人工确认 | 后端链路已完成，页面按钮待第 3 步 |

grep 的 pattern 是要搜索的文字，不是文件路径。本步修复了把 ../误当成 grep 越界路径的问题；glob 的 pattern 才需要做路径模式检查。

## 启动与手动测试

先验证原有桌面端没有退化：

~~~powershell
npm run dev
~~~

让模型读取 README.md。应正常显示读取工具卡片并完成回复。此时页面还看不到三个写工具，这是刻意的：没有允许/拒绝按钮前，正式注册会让消息一直等待。

查看本步后端闭环的逐项结果：

~~~powershell
conda run --no-capture-output -n mycoder pytest tests/bridge/test_permission_flow.py -v
~~~

应看到 7 个测试通过，分别覆盖：

- 允许后才写入，并把相同 tool_use_id 送回模型；
- 拒绝后不写入，Agent 仍能进入下一轮；
- 灾难命令不询问且处理函数从未运行；
- 等待确认时可以取消；
- write、edit、bash 的最小成功路径；
- edit 遇到重复文字时不修改；
- grep 搜索文字不会被误判成路径。

测试产生的文件只在 pytest 临时目录，测试结束后不会污染项目。

## 常见坑

- 先执行再询问：确认失去意义；权限判断必须在工具处理函数之前。
- 只核对 tool_use_id：旧任务可能碰巧留下过期按钮，还要核对 target_request_id。
- 拒绝时抛异常：这会中断 Agent；应返回 is_error=true 的 tool_result。
- 拒绝后换一个新 ID：Claude 协议要求 tool_result 对应原 tool_use_id。
- 参数无效也弹确认：用户允许后仍会失败；所以参数校验放在 ASK 之前。
- 等待确认时无法停止：cancel() 必须同时设置取消标记并唤醒 Future。
- 现在就注册写工具：当前 UI 还不能回复确认，会让真实聊天一直卡住。

## 变更记录

- 第 1 步：实现 ALLOW/DENY/ASK、灾难命令硬拦截和项目路径沙箱。
- 第 2 步：新增 write_file、edit_file、bash；Agent 和 Bridge 打通权限等待、回复、拒绝、取消及 tool_result 回传。

## 自测题与答案

**问：为什么用户拒绝后还要再调用一次 LLM？**

答：拒绝也是有价值的工具结果。模型知道操作没有发生后，才能解释原因、改用只读方案或询问用户。

**问：为什么 permission.respond 要带两个 ID？**

答：target_request_id 指向哪一条聊天任务，tool_use_id 指向其中哪一次工具调用。两者都匹配才能防止过期按钮误操作。

**问：为什么 edit_file 要求 old_text 只出现一次？**

答：出现多次时无法确定用户想改哪一处。保守地返回错误，比静默改错文件内容更安全。

**问：ALLOW 是否代表工具一定成功？**

答：不是。ALLOW 只代表可以尝试执行；文件不存在、命令退出码非零等仍会产生普通工具错误。

**问：这一章现在结束了吗？**

答：还没有。第 3 步要在 Electron 页面展示确认卡片、发送 permission.respond，并在那时把三个新工具注册到正式运行入口。