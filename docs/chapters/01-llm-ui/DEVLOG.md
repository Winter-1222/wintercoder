# 第 1 章开发日志

## 本章目标

完成不产生费用的 FakeLLM 流式桌面闭环，为真实 DeepSeek 适配器建立可回归的参考链路。

## 2026-08-30：A 步 FakeLLM 流式链路

### 计划

- 定义供应商无关的 LLM 流事件。
- 用不规则 Markdown 碎片模拟真实流。
- 实现流式文本、完成后 Markdown 和实时状态栏。
- 用真实 Electron 验证完整路径。

### 实际改动

- 新增 `LLMClient` Protocol、事件枚举、用量和完成信息。
- 新增 `FakeLLMClient`，固定输出标题、列表、加粗和行内代码。
- Bridge 支持 `chat.send`，输出 `stream_text → usage → turn_complete`。
- React reducer 管理消息状态和当前请求；状态栏显示模型、Token 和耗时。
- 完成“极夜观测站”桌面视觉，并使用本地字体，避免运行时字体网络请求。
- 新增 Renderer 浏览器冒烟与真实 Electron Main/Preload/Bridge 端到端测试。

### 遇到的问题

#### Python Playwright 无 Electron 启动接口

- 现象：Python 脚本访问 `playwright._electron` 时属性不存在。
- 原因：当前 Python Playwright API 不提供 Node 版本的 Electron 实验接口。
- 排查过程：先确认浏览器 Renderer 测试正常，再检查 Python Playwright 对象的公开能力。
- 修复：保留 Python Playwright 测 Renderer；真实 Electron 改用 `playwright-core` 的 Node `_electron` 接口。
- 如何防止复发：桌面进程测试使用 Node；浏览器独立页面测试继续使用 Python，职责分开。

#### 开发服务器被辅助脚本遗留

- 现象：使用 `npm run dev:web` 包装启动时，父 npm 进程退出后 Vite 子进程仍占用端口。
- 原因：Windows 下进程树回收没有覆盖 npm 再派生的子进程。
- 排查过程：读取进程命令行，确认残留进程只属于当前项目后逐个停止。
- 修复：浏览器测试直接启动 Vite 的 Node 入口，减少一层 npm 包装。
- 如何防止复发：自动化启动器尽量直接持有真实服务进程，并在测试后检查目标端口。

#### React 事件订阅读取过期状态

- 现象：事件监听只注册一次，但处理函数中的 `activeRequestId` 来自第一次渲染；错误事件可能无法结束当前请求。
- 原因：React 闭包捕获了旧状态。
- 排查过程：审查一次性 `useEffect` 订阅与内部状态引用，发现正常完成路径碰巧不依赖该值。
- 修复：处理逻辑只使用事件中的 `request_id`，让 reducer 判断事件是否匹配当前请求。
- 如何防止复发：长生命周期监听器优先派发自包含事件，避免直接读取易变化的组件状态。

### 设计取舍

- 选择：流式阶段显示原始文本，完成后一次性 Markdown 渲染。
- 没选的方案：每个 delta 都重新运行 Markdown parser。
- 原因：未闭合语法会闪烁，频繁解析也增加无意义渲染。
- 以后何时重新评估：需要超长回复的增量富文本时，可研究分块完成标记，但仍不解析未闭合块。

- 选择：使用确定性 FakeLLM 作为默认启动模型。
- 没选的方案：开发环境启动即要求 DeepSeek API Key。
- 原因：本地 UI、IPC 和事件测试应该稳定、免费且离线可运行。
- 以后何时重新评估：不会移除 FakeLLM；真实适配器会作为可选配置加入。

### 验证

- 自动化测试：Python 11 项、前端 2 项通过；TypeScript、Mypy、Ruff、构建均通过。
- 真实 Electron：自动等待 Bridge 在线，发送“真实桌面链路测试”，验证标题、列表内容、模型名与零控制台错误后退出。
- Renderer 截图：`artifacts/ui/renderer-smoke.png`；Electron 截图：`artifacts/ui/electron-smoke.png`，两者均不提交。
- 已知限制：没有 Anthropic SDK、真实 DeepSeek、多轮历史和模型选择；Token 是 FakeLLM 的保守字符估算。

### Git

- 分支：`main`
- 功能提交：`676759f feat(foundation): 打通 FakeLLM 桌面流式链路`
- 文档提交：由本次文档提交记录。

## A 步结束时记录的下一小步（当前已推进到第 2 项）

1. 新建只允许在适配器目录导入的 Anthropic 客户端：已完成。
2. 用假 SDK 流先测试转换：已完成；真实 DeepSeek 人工集成测试尚未执行。
3. 实现内部/API 两层消息和 `ConversationManager.to_api_format()`：尚未开始。

## 2026-08-30：Codex 风格界面修订

### 改动

- 删除深色网格、轨道装饰和独立大仪表栏。
- 改为浅灰项目侧栏、白色对话工作区、紧凑标题栏和底部悬浮输入框。
- 模型、输入/输出 Token 与耗时收进输入框工具栏，减少视觉噪声。
- 保留流式纯文本、完成后 Markdown、Bridge 状态和键盘发送行为。
- 移除不再使用的 Newsreader 字体导入与依赖。

### 验证

- Renderer 浏览器冒烟通过并生成新版截图。
- 真实 Electron 端到端与关闭窗口回归通过。
- TypeScript 类型检查与本地单元测试通过。
- 本次改动未提交，等待用户验收。

## 2026-08-30：一条消息链路教学重写

### 原问题

- 文档只说明 `LLMClient`、流式事件和 reducer 的概念，没有把它们串成一次真实请求。
- 没有解释 `request_id`、`message_id`、`sequence` 的区别。
- 没有告诉零基础读者按什么顺序阅读 Electron、Python 和 React 文件。

### 本次改动

- 以用户输入“你好”为固定例子，分 26 步走完 Renderer → Preload → Main → Python → FakeLLM → Renderer。
- 给出 `chat.send`、`stream_text`、`usage`、`turn_complete` 的具体信封示例。
- 解释 React state/reducer/action、异步、LLM、流式、Markdown 和 Token。
- 区分 LLM 领域事件、Bridge 信封和 React action 三层事件。
- 增加真实/模拟能力边界、排错入口、文件地图、练习和带答案自测题。

### 验证

- 按消息发送与返回方向逐项对照当前源码。
- 确认文档明确写出 ConversationManager、真实 DeepSeek 和准确 Token 尚未实现。
- 本次只修改文档和协作规范，没有修改运行代码，也没有提交 Git。

## 2026-08-30：`chatReducer` 与界面源码教学注释补全

### 原问题

- `chatReducer` 没有解释 reducer 是什么，初学者容易把它误认为 API 请求函数或界面渲染函数。
- 每个 `case` 只写了状态更新代码，没有交代由哪个 Bridge 事件触发、会改变哪些字段、为什么使用不可变更新。
- `App.tsx` 中 Effect、事件订阅、派发 action、流式文本和完成后 Markdown 的关系不够直观。

### 本次改动

- 在 `state.ts` 文件顶部解释 state、action、dispatch 与 reducer 的关系。
- 为六种 action 的每个 `case` 写出触发时机、旧状态到新状态的变化和忽略过期事件的原因。
- 为 `App.tsx` 的组件、Effect、事件转换、发送函数和主要 JSX 区域增加中文教学注释。
- 为 FakeLLM 与 LLM 抽象补充流事件、分块、用量估算和供应商隔离说明。

### 验证

- reducer 与界面行为保持不变，注释内容逐项对照当前事件名称与字段。
- 完整自动化结果记录在本轮最终报告中。
- 本次改动不提交 Git，等待用户先手动阅读和测试。

## 2026-08-30：B 步四字段配置与模型目录加载

### 目标

- 让 YAML 中的普通数据先经过严格校验，再交给未来的 LLM 适配器。
- 保证 `LLMConfig` 只有 `protocol/model/base_url/api_key` 四个字段。
- 缺少 Key 时允许应用继续启动；真正的结构错误要尽早拒绝。

### 实际改动

- 新增 `src/jixue/llm/config.py`，实现默认目录读取、可选本地覆盖、环境变量展开和逐层校验。
- 新增 `LLMConfig`、`ModelDefinition`、`ModelCatalog`、`CredentialStatus` 和可读的 `ModelCatalogError`。
- 同 ID 本地模型采用整体替换，避免深合并产生“看起来成功、实际字段来自两份文件”的隐式配置。
- `api_key` 不进入对象 repr；诊断命令只显示 `ready` 或 `credentials_missing`。
- 新增 `python -m jixue.llm.config` 只读检查入口，不启动 Electron，也不请求模型。
- 本地新增 6 项配置测试，覆盖三模型、缺 Key、脱敏、覆盖、未知协议、坏默认值和缺字段。

### 遇到的问题

#### Mypy 报告重复类型转换

- 现象：严格类型检查指出 bool 和 int 分支中的 `cast` 是多余的。
- 原因：经过 `type(value) is ...` 判断后，Mypy 已经自动把 `object` 收窄为目标类型。
- 修复：直接返回已收窄的值，并在源码旁解释为什么不再需要转换。
- 复盘：类型检查不仅能找错误，也能提示代码中没有必要的“保险动作”。

### 设计取舍

- 选择完整字段环境变量引用，例如 `${DEEPSEEK_API_KEY}`；暂不支持字符串中间插值。
- 原因：完整替换的输入输出容易解释，也不会留下半替换 URL。
- 选择缺 Key 为非致命状态，字段缺失和未知协议为致命错误。
- 原因：没有 Key 的新用户仍应能打开 FakeLLM 界面，但错误结构不能拖到网络请求时才暴露。

### 验证

- 官方 DeepSeek 文档确认当前三模型名和 Anthropic 端点与目录一致。
- 配置测试 6 项通过，诊断命令正确显示三个模型与缺凭据状态。
- Python 17 项、前端 2 项、Ruff、Mypy 和 TypeScript 全部通过。
- 真实 Electron 构建、FakeLLM 消息和关闭窗口回归通过，配置模块没有破坏既有链路。
- 当前聊天仍使用 FakeLLM；本步不冒充真实 DeepSeek 已接通。
- Git 提交：`fbcbfec feat(llm): 增加四字段模型配置加载`。

## 2026-08-30：C 步 Anthropic 协议适配器与假 SDK 流

### 目标

- 只在供应商适配器内导入官方 `anthropic` SDK。
- 把 SDK 文本增量、最终 Token 和停止原因转换成霁雪自己的事件。
- 把可预期的 SDK 异常转换成安全、稳定、可重试判断的领域错误。
- 用本地假 SDK 流验证请求和响应，不使用真实 Key，也不产生模型费用。

### 实际改动

- 在 `pyproject.toml` 增加 `anthropic>=0.120,<1` 运行依赖。
- 新增 `llm/adapters/anthropic_client.py`，使用 `AsyncAnthropic.messages.stream()` 消费异步文本流。
- 新增 `llm/factory.py`，让上层只按 `protocol` 创建 `LLMClient`，不直接认识供应商类。
- 新增 `LLMClientError`，只公开 `code/message/retryable`，不让 SDK 异常对象越过边界。
- Bridge 捕获领域错误并转成 `error` 信封；未预期异常只返回固定消息，不回显异常 repr、用户文本或 Key。
- 请求显式传入 `api_key/base_url/max_retries`，避免 SDK 从另一个环境变量悄悄取得错误凭据。
- 请求传入顶层 `cache_control={"type": "ephemeral"}`，为后续稳定多轮前缀准备提示缓存。
- 新增本地假 SDK 测试，检查参数、文本事件顺序、最终用量、客户端复用、缺 Key 和类型化错误。
- 扩展架构守门测试：扫描整个 `src/jixue`，`anthropic` 只能由 `llm/adapters` 导入。

### 一条假 SDK 消息链路

```text
测试 LLMConfig
  → create_llm_client() 选择协议
  → AnthropicLLMClient.stream("你好")
  → _get_client() 延迟创建并缓存客户端
  → messages.stream(...) 接收请求参数
  → text_stream 产生文本片段
  → LLMStreamEvent(TEXT)
  → await get_final_message()
  → LLMStreamEvent(USAGE)
  → LLMStreamEvent(COMPLETE)
```

这条链路验证的是适配器本身。当前 `BridgeServer` 启动时仍注入 FakeLLM，所以在 Electron 中点击发送不会调用 DeepSeek。

### 遇到的问题

#### `get_final_message()` 看起来像普通方法，实际需要等待

- 现象：第一次写成 `final_message = stream.get_final_message()` 后，Mypy 提示协程没有 `usage` 和 `stop_reason`。
- 原因：异步客户端的 `get_final_message()` 返回 awaitable；仅从方法名称看不出这一点。
- 修复：改为 `final_message = await stream.get_final_message()`。
- 复盘：使用异步 SDK 时不能只看示例外观，要同时检查类型声明；Mypy 能在真实请求前发现“拿到协程却当结果使用”的错误。

#### 测试替身的返回类型写得太宽

- 现象：Bridge 错误测试把假客户端流声明成 `AsyncIterator[object]`，Mypy 无法确认它符合 `LLMClient`。
- 原因：`object` 只表示“任意对象”，没有保证事件具备 `type/text/usage` 字段。
- 修复：把返回类型收窄为 `AsyncIterator[LLMStreamEvent]`。
- 复盘：测试代码的类型也应该描述真实合同，否则测试替身可能悄悄偏离正式接口。

### 设计取舍

- 选择延迟创建 SDK 客户端，而不是构造适配器时立刻要求 Key。
- 原因：没有 Key 的初学者仍能启动应用、查看模型目录并使用 FakeLLM；真正发送时才得到 `credentials_missing`。
- 选择复用同一个 SDK 客户端。
- 原因：后续多次请求可以复用 SDK 内部连接池，不必每条消息重新建立客户端。
- 选择在适配器内翻译类型化异常。
- 原因：Bridge 和 UI 只需要稳定领域语义，也避免把供应商响应细节或敏感信息直接公开。
- 选择现在就传提示缓存参数，但不宣称已经命中。
- 原因：缓存需要稳定且足够长的前缀；当前单条短消息和两字段 Usage 还不能提供真实命中证据。
- 选择不做真实网络请求。
- 原因：本小步的目标是免费验证封装边界；真实 Key、网络和费用由下一次明确手测控制。

### `Codex-api` 技能怎样影响实现

- 使用官方 `anthropic` Python SDK，而不是手写 HTTP 或套 OpenAI 兼容层。
- 使用官方流式 helper 和类型化异常，不解析私有 SDK 内部字段。
- 在请求入口加入提示缓存参数，并在教学中区分“已传参数”和“真实命中”。
- 把 SDK 完全限制在适配器文件，上层只接收霁雪领域事件和领域错误。

### 验证

- 适配器与 Bridge 定向测试 13 项通过。
- 完整 Python 27 项、前端 2 项通过。
- Ruff、15 个 Python 源文件的 Mypy 检查和 TypeScript 类型检查全部通过。
- Electron 生产构建与真实进程回归通过；窗口自动发送 FakeLLM 消息并正常退出，没有主进程 JavaScript 错误。
- 没有读取真实 `DEEPSEEK_API_KEY`，没有网络调用，也没有费用。

### Git

- B 步已经提交：`fbcbfec feat(llm): 增加四字段模型配置加载`。
- C 步按约定保持未提交，等待用户阅读、启动和手动测试。

## 当前下一小步

1. 给 Bridge 启动入口增加明确的模型模式选择，同时保留 FakeLLM 默认值。
2. 用户确认后在本机临时设置 Key，完成一次真实 DeepSeek 流式手测。
3. 实现内部/API 两层消息和 `ConversationManager.to_api_format()`。
