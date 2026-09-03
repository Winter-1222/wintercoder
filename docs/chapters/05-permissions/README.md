# 第 5 章：五层权限防御

本章让 Agent 在工具真正运行前先判断风险，不能只依赖提示词。本章调整为四个小步骤；当前完成第 3 步：桌面端已经能让用户允许或拒绝一次工具调用。

## 当前成果

- 危险命令命中硬黑名单后直接拒绝。
- 文件工具和 glob 不能访问项目目录之外。
- 项目内只读工具自动执行，写入和命令工具默认询问。
- write_file、edit_file、bash 已注册到正式 Bridge。
- 页面收到 permission_request 后，在原工具卡片中显示输入参数、风险原因和允许/拒绝按钮。
- 重复点击、过期按钮、IPC 发送失败和等待期间取消都有明确状态。
- 用户拒绝不会让 Agent 崩溃，而会成为 is_error=true 的 tool_result 返回模型。
- 第 4 步还要补最小权限规则和权限模式，完成第 3、4 层防线。

## 推荐阅读顺序

1. src/jixue/permission.py：看 ALLOW、DENY、ASK 怎样产生。
2. src/jixue/agent.py：搜索 _check_tool_call() 和 PERMISSION_REQUEST。
3. src/jixue/bridge/application.py：看 permission.respond 怎样唤醒 Agent。
4. apps/desktop/src/shared/protocol.ts：看页面可以调用的 respondPermission()。
5. apps/desktop/src/main/bridge-process.ts：看决定怎样变成一行 JSON。
6. apps/desktop/src/renderer/src/state.ts：看 reducer 怎样改变权限卡片状态。
7. apps/desktop/src/renderer/src/App.tsx：看事件翻译和允许/拒绝按钮。
8. src/jixue/tools/write_tools.py 与 bash.py：最后看真正产生副作用的代码。

## 一条需要确认的消息怎样跑起来

假设用户要求创建 notes/todo.md：

~~~text
用户点击发送
  → Electron 调用 chat.send
  → Python Agent 发起第 1 轮 LLM 请求
  → 模型返回 tool_use
       id = tool_123
       name = write_file
       input = {path, content}
  → Agent._check_tool_call()
       JSON、工具名、参数、Plan 模式、路径依次通过
  → evaluate_permission() 返回 ASK
  → Agent 创建 Future
  → Agent 发出 permission_request
  → BridgeServer 写出一行 JSON
  → PythonBridge 转发给 Electron 页面
  → App.handleEvent() 翻译成 permission_requested action
  → chatReducer 找到 id=tool_123 的工具卡片
  → 页面展示输入参数和允许/拒绝按钮
  → Agent 暂停在 await，write_file 还没有运行

用户点击“允许”
  → reducer 先把卡片设为 allowing，防止重复点击
  → Preload 调用 jixue:respond-permission
  → Electron Main 校验 requestId、toolUseId 和 allow
  → PythonBridge 发送 permission.respond
  → BridgeApplication 同时核对聊天 ID 和工具调用 ID
  → Agent.respond_permission() 把 true 放入 Future
  → Agent 被唤醒，开始执行 write_file
  → Agent 发出 tool_result，仍使用 tool_123
  → 第 2 轮 LLM 收到工具结果并生成最终回复
  → loop_complete 解锁输入框
~~~

点击“拒绝”时，前半段完全相同。区别只是 Future 得到 false，工具处理函数不会运行；Agent 生成“用户拒绝了本次工具调用”的错误结果，再让模型收尾。

Future 可以先理解成一个暂时没有答案的盒子。Agent await 这个盒子时不会空转；用户决定到达后，Agent 才从暂停位置继续。

## 为什么要核对两个 ID

permission.respond 包含：

~~~text
target_request_id  → 这是哪一条聊天任务
tool_use_id        → 这是任务中的哪一次工具调用
allow              → true 允许，false 拒绝
~~~

只核对 tool_use_id 不够。旧任务留下的按钮可能晚到，因此 Bridge 必须先确认聊天任务仍在运行，再确认 Agent 正在等待这一次工具调用。任一不匹配都会返回 accepted=false，工具不会执行。

## 页面状态怎样变化

工具卡片的权限状态是：

~~~text
pending
  → 点击允许：allowing
  → 点击拒绝：denying
  → Bridge 接受：allowed 或 denied
  → Bridge 不接受：expired
  → IPC 发送失败：回到 pending，可重新选择
~~~

tool_result 到达后，原工具卡片从 streaming 变成 complete 或 failed。它不会新建另一张卡片，因为 reducer 用同一个 tool_use_id 定位。

## 三个会询问的工具

### write_file

用 path 和 content 创建或整体覆盖 UTF-8 文件。它会创建缺失的父目录，内容最多 1,000,000 字符。

### edit_file

用 path、old_text、new_text 做一次精确替换。old_text 必须恰好出现一次；出现零次或多次都不修改文件。

### bash

在项目根目录运行命令。Windows 使用 PowerShell，macOS/Linux 使用 Bash；最长 30 秒，输出最多约 50,000 字符。传给子进程前会移除常见密钥环境变量。

bash 可以运行很多操作，因此本章只把它作为需要确认的串行工具。它不是完整的操作系统沙箱。

## 五层防线进度

| 层级 | 当前状态 |
| --- | --- |
| 1. 危险命令硬拦截 | 已完成并接入 Agent |
| 2. 项目路径沙箱 | 已完成并接入 Agent |
| 3. 细粒度权限规则 | 第 4 步实现最小规则 |
| 4. 整体权限模式 | 第 4 步实现最小模式 |
| 5. HITL 人工确认 | 后端、IPC 和页面已全部打通 |

## 启动和手动测试

在项目根目录启动：

~~~powershell
conda activate mycoder
npm run dev
~~~

### 测试允许

1. 对真实模型说：“请创建 manual-permission-test.txt，内容是 permission ok。”
2. 页面应出现 write_file 工具卡片，并显示“需要确认”。
3. 展开的输入参数中应能看到目标路径和内容。
4. 此时先检查项目目录，文件不应该存在。
5. 点击“允许”。
6. 卡片应变成“工具完成”，文件此时才出现，模型随后给出最终回复。

### 测试拒绝

1. 再让模型创建 manual-denied-test.txt。
2. 权限卡片出现后点击“拒绝”。
3. 卡片应显示失败，模型应知道用户拒绝了操作。
4. 项目目录中不应该出现 manual-denied-test.txt。
5. 输入框恢复后仍能继续发送消息。

手测产生的 manual-permission-test.txt 是临时文件，测试后可以手动删除，不要提交到 Git。

## 自动测试

完整检查：

~~~powershell
npm run test:all
npm run test:electron
~~~

Electron 测试使用临时项目和离线模型，不读取真实 API Key，也不产生 API 费用。它会验证：

~~~text
请求 write_file
→ 看见权限卡片
→ 允许前临时文件不存在
→ 点击允许
→ 工具完成
→ 文件内容正确
→ 关闭 Electron 不出现主进程错误
~~~

自动化测试文件由 .gitignore 排除，不加入 Git。

## 常见坑

- 先执行再询问：权限确认必须位于工具处理函数之前。
- 页面只显示按钮却没有暂停 Agent：后端必须 await Future。
- 连续点击发送两次决定：点击后要立刻禁用按钮。
- permission.resolved 使用自己的 request_id：更新工具卡片时应读取 payload 中的 target_request_id。
- 拒绝时抛程序异常：应返回 is_error=true 的工具结果，让模型继续。
- tool_result 换了新 ID：必须沿用原 tool_use_id。
- Plan 模式漏拦写工具：即使模型猜出隐藏工具名，执行入口仍要拒绝。
- 开放工具早于 UI：会让 Agent 永久等待无法到达的确认回复。

## 变更记录

- 第 1 步：实现 ALLOW、DENY、ASK、硬拦截和路径沙箱。
- 第 2 步：新增 write_file、edit_file、bash，打通 Agent 与 Bridge 的权限等待。
- 第 3 步：接通 Electron Main、Preload、React reducer 和确认卡片，并正式注册三个工具。

## 自测题与答案

**问：为什么 permission_request 要更新已有工具卡片，而不是新建消息？**

答：tool_use 和 permission_request 描述的是同一次调用。使用同一个 tool_use_id 更新原卡片，用户更容易看懂，也不会出现两张重复卡片。

**问：为什么点击按钮后不能立刻认为工具执行成功？**

答：按钮只代表用户作出决定。Bridge 可能拒绝过期决定，工具本身也可能执行失败；最终状态要以 permission.resolved 和 tool_result 为准。

**问：用户拒绝后为什么还会有下一轮 LLM 请求？**

答：拒绝是有价值的工具结果。模型需要知道操作没有发生，才能解释、调整方案或询问用户。

**问：点击停止时 Agent 正在等待权限会怎样？**

答：cancel() 除了设置取消标记，还会唤醒 Future。Agent 退出当前循环、关闭工具卡片，但程序仍可继续聊天。

**问：第五章结束了吗？**

答：还差第 4 步：加入简单的细粒度权限规则和整体权限模式，完成五层防线中的第 3、4 层。