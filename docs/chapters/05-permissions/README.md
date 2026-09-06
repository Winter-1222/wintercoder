# 第 5 章：五层权限防御

本章已完成。现在霁雪不会拿到工具就立刻执行，而是先经过硬拦截、路径沙箱、规则、权限模式和人工确认。

## 当前成果

- 灾难命令直接拒绝，任何权限模式都不能放行。
- 文件和搜索工具只能访问当前项目。
- 提供“修改需确认”“每次都询问”“自动允许”三种权限模式。
- write_file、edit_file、bash 需要确认时，会在工具卡片中显示参数和允许/拒绝按钮。
- 拒绝与校验失败都会变成 is_error=true 的 tool_result，模型仍能调整方案。
- Plan/Do 与权限模式彼此独立：前者决定模型能看见哪些工具，后者决定已开放工具是否询问。

## 推荐阅读顺序

1. src/jixue/permission.py：先看 PermissionMode、PermissionDecision 和 evaluate_permission()。
2. src/jixue/agent_runtime/execution.py：搜索 _check_tool_call()，看权限判断位于真正执行之前。
3. src/jixue/bridge/application.py：搜索 permission.mode 和 permission.respond。
4. apps/desktop/src/shared/protocol.ts：看 Electron 与 Python 约定了哪些方法。
5. apps/desktop/src/renderer/src/state.ts：看 reducer 如何保存权限模式和确认卡片状态。
6. apps/desktop/src/renderer/src/App.tsx：看选择框、允许按钮和拒绝按钮。
7. src/jixue/tools/write_tools.py 与 bash.py：最后看通过权限后真正执行副作用的代码。

## 先分清两个概念

PermissionDecision 是某一次工具调用的判断结果：

~~~text
ALLOW → 直接执行
ASK   → 暂停，等待用户选择
DENY  → 不询问，直接返回失败结果
~~~

PermissionMode 是用户选择的整体策略。它会影响大多数调用得到 ALLOW 还是 ASK，但不能把 DENY 改成 ALLOW。

## 五层防线怎样工作

~~~text
模型返回 tool_use
  ↓
第 1 层：危险命令硬拦截
  命中 → DENY，立即停止这次调用
  ↓
第 2 层：项目路径沙箱
  越过项目目录 → DENY
  ↓
第 3 层：细粒度规则
  默认模式下，少量精确匹配的只读 Git 命令 → ALLOW
  ↓
第 4 层：权限模式
  修改需确认 / 每次都询问 / 自动允许
  ↓
第 5 层：HITL 人工确认
  判断为 ASK → 页面显示允许/拒绝
  ↓
Tool.execute()
~~~

最重要的顺序是：硬拦截和路径沙箱永远在模式之前。因此选择“自动允许”只是省去确认，不是关闭安全底线。

## 三种权限模式

| 界面名称 | 只读工具 | 写入、编辑、普通命令 | 适合场景 |
| --- | --- | --- | --- |
| 修改需确认 | 自动执行 | 通常询问 | 默认；效率与安全较平衡 |
| 每次都询问 | 也要询问 | 询问 | 学习每次调用、严格观察 Agent |
| 自动允许 | 自动执行 | 自动执行 | 信任当前任务并追求速度 |

“每次都询问”和“自动允许”的区别，就是每一次普通工具调用前要不要停下来等你。两者都不能绕过硬拦截和项目路径沙箱。

自动允许仍然风险较高，尤其是 bash：命令字符串能力很广，本项目并不是完整的操作系统沙箱。不了解任务或命令时，使用默认的“修改需确认”。

## 细粒度规则

第五章只实现一小组容易验证的精确规则。默认“修改需确认”模式下，以下命令直接执行：

~~~text
git status
git status --short
git diff
git diff --staged
git log -1 --oneline
git branch --show-current
~~~

这是完整字符串匹配，不是 git * 通配规则。例如 git push 仍要询问，git status; 其他命令也不会误命中。

## 一次需要确认的消息怎样跑

假设用户让霁雪创建 notes/todo.md：

~~~text
用户发送消息
  → 第 1 轮 LLM 返回 write_file 的 tool_use
  → ToolExecutor._check_tool_call() 校验工具名和参数
  → evaluate_permission() 依次检查五层规则
  → 默认模式得到 ASK
  → Agent 创建 Future 并发出 permission_request
  → Bridge → Electron → App.handleEvent()
  → chatReducer 把原工具卡片改成 pending
  → 页面显示参数和允许/拒绝
  → Agent 暂停，文件此时还不存在

用户点击允许
  → 页面先改成 allowing，防止重复点击
  → Preload → Electron Main → PythonBridge
  → permission.respond 携带聊天 ID、工具调用 ID 和 true
  → BridgeApplication 核对两个 ID
  → Agent.respond_permission() 唤醒 Future
  → write_file 真正执行
  → tool_result 使用原 tool_use_id 返回模型
  → 第 2 轮 LLM 根据结果给最终回复
  → loop_complete 解锁输入框
~~~

点击拒绝时，Future 得到 false，write_file 不运行。Agent 把“用户拒绝”作为错误工具结果发给模型，而不是让程序崩溃。

## 切换权限模式的链路

~~~text
选择“自动允许”
  → App 调用 window.jixue.setPermissionMode()
  → Electron 校验值，只接受三种固定字符串
  → PythonBridge 发送 permission.mode
  → BridgeApplication 确认当前没有任务运行
  → Agent.set_permission_mode()
  → Python 回发 permission_mode.changed
  → reducer 更新选择框和底部说明
~~~

任务运行中选择框会禁用，防止同一轮执行到一半时规则突然变化。

## Plan/Do 与权限模式

这两个开关不是一回事：

~~~text
Plan / Do       → 决定哪些工具能进入本轮工具列表
权限模式         → 决定已进入列表的工具是否需要确认
~~~

Plan 只暴露只读工具，并且 Agent 执行入口还会再拦一次写工具。即使同时选择“自动允许”，Plan 也不能写文件。

## 启动与手动测试

在项目根目录运行：

~~~powershell
conda activate mycoder
npm run dev
~~~

### 1. 测试默认模式

1. 保持 Do 和“修改需确认”。
2. 让模型创建 manual-permission-test.txt，内容为 permission ok。
3. 卡片出现时先确认文件不存在。
4. 点击允许，文件才应出现，随后模型完成第 2 轮回复。
5. 再创建另一个文件并点击拒绝，文件不应出现。

### 2. 测试每次都询问

1. 选择“每次都询问”。
2. 让模型读取 README.md。
3. read_file 虽是只读工具，也应出现确认卡片。
4. 点击允许后才会显示读取结果。

### 3. 测试自动允许

1. 选择“自动允许”。
2. 让模型创建 manual-auto-test.txt。
3. 不应出现确认卡片，工具会直接执行。
4. 测完手动删除两个临时文件，不要提交。

不要用真实系统破坏命令做手测；硬拦截由自动化测试覆盖。

## 自动测试

~~~powershell
npm run test:all
npm run test:electron
~~~

test:all 检查三种模式、硬拦截、路径沙箱、Bridge 事件和 reducer。test:electron 启动真实桌面进程，依次验证默认确认、只读也询问、自动执行和无错误退出；它使用临时目录和离线模型，不读取 API Key。

测试文件由 .gitignore 排除，不加入 Git。

## 常见坑

- 把权限判断放在执行之后：文件已经修改，再询问没有意义。
- 认为自动允许等于关闭安全：DENY 必须优先于模式判断。
- 把“安全 Git 命令”写成 git *：git push、git reset 等操作会被误放行。
- 只在 UI 禁用按钮：真正规则必须由 Python 后端执行。
- 只核对 tool_use_id：旧任务的按钮可能误操作新任务，还要核对聊天 ID。
- 拒绝时抛异常：应返回错误 ToolResult，让模型能够继续。
- 把 Plan 和权限模式混在一起：一个管工具范围，一个管是否询问。

## 变更记录

- 第 1 步：实现 ALLOW、DENY、ASK、危险命令硬拦截和路径沙箱。
- 第 2 步：新增 write_file、edit_file、bash，打通 Agent 的异步权限等待。
- 第 3 步：接通 Electron、Preload、React reducer 和确认卡片。
- 第 4 步：完成三种权限模式、精确安全规则、界面切换和全链路测试。

## 自测题与答案

**问：自动允许为什么仍可能返回 DENY？**

答：硬拦截和路径沙箱先执行。自动允许只把通过安全底线的普通调用从 ASK 变成 ALLOW。

**问：每次都询问为什么连 read_file 也弹卡片？**

答：这个模式用于观察或审计每一次工具调用，所以通过安全检查的只读工具也返回 ASK。

**问：为什么 git status 可以直接执行，git push 不行？**

答：规则只精确列出少量只读命令。git push 不在规则中，默认模式下仍需用户确认。

**问：为什么页面切换后要等待 permission_mode.changed？**

答：真正执行规则在 Python。只有后端确认成功后更新界面，页面才不会显示一个实际上尚未生效的模式。

**问：拒绝后为什么还有第 2 轮 LLM？**

答：拒绝会成为 is_error=true 的 tool_result。模型需要看到结果，才能解释失败或调整方案。

**问：第五章结束了吗？**

答：结束了。下一章是 MCP：先抽象 transport，再接通一个最小 Server 和 ToolRegistry。
