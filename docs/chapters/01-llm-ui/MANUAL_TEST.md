# 第 1 章手动测试

> 本记录已验收 A 步 FakeLLM 流式链路、B 步模型配置加载、C 步本地假 SDK 适配器和 D 步 Bridge 模型模式接线；真实 DeepSeek 网络请求与多轮历史尚未验收。

## 测试环境

- 日期：2026-08-31
- 操作系统：Windows
- Conda 环境：`mycoder`
- Python 版本：3.12.13
- Node/Electron 版本：Node.js 24.15.0 / Electron 44.0.0
- 桌面默认模型：`fake-jixue`
- 适配器测试：本地假 SDK 流，不访问网络

## 准备

1. 完成第 0 章依赖安装。
2. 执行 `npm run dev`。
3. 等待界面显示“fake-jixue / Bridge 在线”。

## 用例 1：流式文本与完成后 Markdown

- 前置条件：Bridge 在线，输入框可用。
- 操作步骤：
  1. 输入“请验证第一条流式消息”。
  2. 点击“发送”。
  3. 在输出过程中观察标题前的 `##` 和加粗标记。
  4. 等回复完成后再次观察同一消息。
- 预期结果：生成中显示原始 Markdown 文本和光标；完成后 `##` 变成标题、列表分行、加粗和行内代码正确渲染。
- 实际结果：文本按不规则碎片持续出现，结束后一次性变成 Markdown，没有闪烁或重复文本。
- 结论：通过。

## 用例 2：状态栏与再次发送

- 前置条件：用例 1 已完成。
- 操作步骤：
  1. 观察 MODEL、INPUT、OUTPUT 和 ELAPSED。
  2. 再发送一条不同消息。
- 预期结果：模型显示 `fake-jixue`；Token 大于零；耗时在生成时变化、完成后停止；第二次发送正常开始。
- 实际结果：四项状态均更新，第二个请求在首个请求结束后可发送。
- 结论：通过。
- 注意：这只证明 UI 支持连续多次请求，不代表完整对话历史已经发送给模型。

## 用例 3：真实 Electron 自动化回归

- 前置条件：Electron 二进制完整，端口没有残留开发服务器。
- 操作步骤：
  1. 执行 `npm run test:electron`。
  2. 等待测试自动构建、打开窗口、发送消息并关闭。
- 预期结果：命令退出码为 0；能找到“霁雪已经醒来”“Python Bridge 正常”和 `fake-jixue`；Renderer 无控制台错误。
- 实际结果：2026-08-30 执行通过，截图写入 `artifacts/ui/electron-smoke.png`。
- 结论：通过。

## 用例 4：统一自动化回归

- 操作步骤：
  1. 执行 `npm run test:all`。
  2. 执行 `npm run typecheck`。
  3. 执行 `conda run --no-capture-output -n mycoder ruff check .`。
  4. 执行 `conda run --no-capture-output -n mycoder mypy src`。
- 预期结果：所有命令退出码为 0。
- 实际结果：Python 33 项、前端 2 项、Ruff、Mypy 和 TypeScript 全部通过。
- 结论：通过。

## 用例 5：Codex 风格布局

- 操作步骤：
  1. 执行 `npm run dev`。
  2. 检查左侧项目/对话侧栏、顶部面包屑、居中对话区和底部输入框。
  3. 发送一条消息，确认用户气泡与霁雪回复在同一内容列中。
- 预期结果：界面为克制的浅灰/白色工作台；状态信息位于输入框工具栏；没有旧版网格背景和大面积青色装饰。
- 实际结果：浏览器与真实 Electron 截图均符合新版布局，功能元素无重叠。
- 结论：通过。

## 用例 6：默认三模型目录与缺 Key 降级

- 前置条件：位于项目根目录，`mycoder` 已安装当前项目依赖。
- 操作步骤：
  1. 不设置 `DEEPSEEK_API_KEY`。
  2. 执行 `conda run --no-capture-output -n mycoder python -m jixue.llm.config`。
  3. 检查输出中是否出现 `flash`、`pro`、`vision_exp`。
  4. 检查默认模型是否为 `flash`。
- 预期结果：命令退出码为 0；三个模型均显示 `credentials_missing`；终端没有输出 Key；没有网络请求。
- 实际结果：2026-08-30 执行通过，正确显示三个模型、默认 Flash 和缺凭据状态。
- 结论：通过。
- 注意：配置可读不等于真实 API 已接通；默认 Electron 使用 FakeLLM，只有 configured 模式才选择目录客户端。

## 用例 7：Anthropic 适配器假 SDK 流

- 前置条件：位于项目根目录，`mycoder` 已安装当前项目及开发依赖。
- 操作步骤：
  1. 不设置真实 `DEEPSEEK_API_KEY`。
  2. 执行 `conda run --no-capture-output -n mycoder python -m pytest tests/llm/test_anthropic_client.py tests/bridge/test_application.py -vv`。
  3. 观察测试名称中是否包含 stream、lazy、credentials、typed errors 和 factory。
- 预期结果：
  1. 13 项测试全部通过。
  2. 测试不打开 Electron，不访问 DeepSeek，不产生费用。
  3. 事件顺序是若干 `TEXT`，然后 `USAGE`，最后 `COMPLETE`。
  4. 请求包含配置里的 model、base_url、api_key，以及 `cache_control={"type": "ephemeral"}`。
  5. 认证、限流、连接和服务端错误被转换成安全的 `LLMClientError`。
- 实际结果：2026-08-30 执行通过，13 项测试全部通过。
- 结论：通过。
- 注意：假 SDK 测试证明“我们的封装逻辑正确”，不证明真实 Key、网络、账户权限或服务端当前可用。

## 用例 8：项目 `.env` 选择 fake

- 前置条件：项目根目录 `.env` 中写有 `JIXUE_LLM_MODE=fake`，Key 可留空。
- 操作步骤：
  1. 执行 `npm run dev`。
  2. 等待 Bridge 就绪。
  3. 检查界面连接状态。
  4. 发送消息并关闭窗口。
- 预期结果：状态显示 `fake-jixue / Bridge 在线`；回复仍为 FakeLLM；不访问网络；退出无错误弹窗。
- 实际结果：真实 Python 子进程握手返回 `model=fake-jixue`；隔离后的 Electron 自动化使用临时 fake `.env`，不会读取用户真实 Key。
- 结论：通过。

## 用例 9：configured 缺 Key 安全降级

- 前置条件：只在尚未保存真实 Key 时执行；已有真实 Key 的用户可跳过，避免为了测试修改密钥。
- 操作步骤：
  1. 在项目根目录 `.env` 写 `JIXUE_LLM_MODE=configured`。
  2. 写 `JIXUE_MODEL_ID=flash`。
  3. 保持 `DEEPSEEK_API_KEY=` 为空。
  4. 执行 `npm run dev`。
  5. 等待“deepseek-v4-flash / Bridge 在线”，发送“你好”。
- 预期结果：握手成功；发送后显示 `credentials_missing` 对应中文提示；输入框恢复；不联网、不产生费用。
- 实际结果：真实 Python 子进程已完成相同握手和 `chat.send`，返回 `credentials_missing`，退出码为 0。
- 结论：Python 进程边界通过；用户可按以上 UI 步骤手动复核。

## 用例 10：真实 DeepSeek Flash

- 状态：**待用户手动执行，自动化没有调用真实 API。**
- 前置条件：用户自己的 DeepSeek Key，确认愿意产生一次真实请求费用。
- 操作步骤：
  1. 确认项目根目录文件名恰好是 `.env`，不是 `.env.txt` 或 `.env.example`。
  2. 确认其中写 `JIXUE_LLM_MODE=configured`。
  3. 确认写 `JIXUE_MODEL_ID=flash`。
  4. 确认 `DEEPSEEK_API_KEY=` 后有自己的真实 Key；不要把值复制到测试记录。
  5. 完全关闭旧 Electron，再执行 `npm run dev`。
  6. 等待“deepseek-v4-flash / Bridge 在线”。
  7. 发送“请用一句话介绍霁雪”。
- 预期结果：收到真实流式文本；状态栏模型为 `deepseek-v4-flash`；Token 来自服务端最终 Usage；完成后 Markdown 渲染；关闭窗口无异常。
- 实际结果：当前 `.env` 已通过只读装配和真实 Bridge 握手，返回 `deepseek-v4-flash`；尚未发送 `chat.send`，真实流式回复仍待用户手动验收。
- 结论：待验收。
- 安全提醒：不要把 Key 贴进聊天、YAML、测试、截图或 Git；`.env` 虽被 Git 忽略，仍应只保存在自己的电脑。

## 用例 11：项目 `.env` 优先于系统环境

- 状态：自动化已通过，用户无需改动自己真实 `.env`。
- 测试设计：
  1. pytest 在临时目录写一份 `.env`，其中选择 `configured + pro`。
  2. 同时给 bootstrap 传一份故意冲突的“系统环境”，其中选择 `fake + flash`。
  3. 调用 `create_runtime_llm(temp_root, environ=fake_environment)`。
- 预期结果：得到 `AnthropicLLMClient`，模型为 `deepseek-v4-pro`；证明项目文件覆盖系统环境。
- 实际结果：2026-08-31 执行通过，bootstrap 文件共 7 项测试通过。
- 结论：通过。
- 安全说明：临时 `.env` 只含 `dotenv-test-key` 假字符串，不读取当前项目的真实 Key，也不调用网络。

## 异常用例

- 空白消息：发送按钮保持禁用，不创建空消息。
- Bridge 未就绪：输入框禁用并显示等待提示。
- 非法 Bridge 命令：收到 `error` 事件，服务器继续运行。
- 窗口关闭：Electron 自动回收 Python Bridge。

## 用例 12：两层消息与历史清洗

- 前置条件：位于项目根目录，`mycoder` 已安装开发依赖。
- 操作步骤：
  1. 执行 `conda run --no-capture-output -n mycoder pytest tests/domain/test_conversation.py -vv`。
  2. 打开 `src/jixue/domain/conversation.py`，按 `to_api_format()` 注释对照测试输入。
- 预期结果：
  1. 7 项测试通过。
  2. streaming、failed、cancelled 和空白消息不进入 API 历史。
  3. 相邻 user 消息合并，内部原始列表保持不变。
  4. 重复 ID、空结果和 assistant 开头得到中文 `ConversationError`。
- 实际结果：2026-08-31 定向测试 7 项通过。
- 结论：通过。
- 注意：本步尚未接入 Bridge；Electron 连续发送仍不是多轮对话。

缺失 Key 的“目录仍可加载”和“适配器发送前返回 `credentials_missing`”已经用本地测试验证。认证、限流、连接和 5xx 的**翻译逻辑**使用官方异常类型构造测试完成；真实断网、真实限流、半截 assistant 消息和账户权限仍要等网络手测，本记录不写成已通过。

## 本章常见坑

| 现象 | 常见原因 | 排查方法 | 修复方式 |
| --- | --- | --- | --- |
| 回复直到最后才出现 | stdout 被缓冲 | 检查 Python 是否带 `-u` | 使用无缓冲模式并逐事件 flush |
| Markdown 生成时抖动 | 每个 delta 都重新解析 | 查看消息状态与渲染分支 | 流中用纯文本，完成后再解析 |
| 错误后输入框一直禁用 | 长期监听器读取旧 React 状态 | 检查闭包和 request_id | 派发自包含事件，由 reducer 校验 |
| Python 测试无法启动 Electron | Python Playwright 不提供 Electron 接口 | 检查 Playwright 对象能力 | Electron 用 Node `playwright-core` 测试 |
| 模型名或 Token 不更新 | usage/complete 事件未转发 | 检查 Bridge 信封序号 | 修复事件映射，不从文本猜状态 |
| 模型目录提示缺字段 | YAML 缩进错误或本地覆盖条目不完整 | 运行 `python -m jixue.llm.config` | 按错误路径补齐整个模型条目 |
| 三个模型都显示 credentials_missing | 项目 `.env` 没有有效 `DEEPSEEK_API_KEY` | 检查文件位置、变量名和等号后的值是否非空 | 修正项目 `.env` 后完全重启 Electron；不要把 Key 写进 Git |
| 适配器测试拿不到最终 Usage | 忘记 `await get_final_message()` | 运行 Mypy，检查返回值是不是协程 | 等待异步最终消息后再读取字段 |
| Bridge 或领域层出现 `import anthropic` | SDK 边界被绕过 | 运行 `tests/test_architecture.py` | 把 SDK 类型和转换逻辑移回 `llm/adapters` |
| 已传 cache_control 却没有命中数字 | 当前是短单轮且 Usage 未扩展缓存字段 | 检查消息历史与供应商 Usage | 完成 ConversationManager 后再做真实缓存验收 |
| 一直显示 fake-jixue | 项目 `.env` 不存在、名字错误或模式仍为 fake | 只检查文件路径与 `JIXUE_LLM_MODE`，不要打印 Key | 修正项目根目录 `.env` 后完全重启 Electron |
| configured 启动后立刻退出 | 模式、模型 ID 或目录结构无效 | 查看带 `[jixue-python]` 前缀的 stderr | 按中文错误修正变量或 YAML |
| 改了 .env.example 但没有生效 | 程序只加载 `.env`，示例文件只是模板 | 确认当前目录存在名为 `.env` 的文件 | 复制 `.env.example` 为 `.env`，真实 Key 只写入复制文件 |
| `.env` 与系统环境冲突 | 不清楚谁优先 | 阅读 `load_project_environment()` 的三步注释 | 当前实现固定使用项目 `.env` 的同名值 |
| streaming 半截回复进入历史 | 没按状态过滤 | 运行 conversation 定向测试 | 只允许 complete 进入 API 历史 |
| 转换后 UI 历史也少了 | 原地删除或修改内部列表 | 比较转换前后的 `manager.messages` | 返回新 `APIMessage`，不改内部消息 |

## 回归结论

- 可以进入下一章：否；第一章尚未完成。
- 可以进入第一章下一小步：是。
- Electron 构建与退出回归：通过，没有出现 “A JavaScript error occurred in the main process”。
- D 步模型模式接线：项目 `.env` 自动加载、优先级、默认 fake、configured 缺 Key 和测试隔离均通过；真实 Key 请求待用户验收。
- E 步消息模型与历史清洗：7 项定向测试通过，尚未接入真实请求。
- 未解决问题：ConversationManager 接线、真实 DeepSeek 多轮验收、UI 三模型选择和十轮对话回归。
