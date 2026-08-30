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

这条链路验证的是适配器本身。**在 C 步完成当时**，`BridgeServer` 启动仍注入 FakeLLM；后续 D 步已经加入 fake/configured 启动选择。

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
- C 步已经提交：`c6e13ef feat(llm): 封装 Anthropic 流式适配器`。

## C 步结束时记录的下一小步（当前已推进第 1 项）

1. 给 Bridge 启动入口增加明确的模型模式选择，同时保留 FakeLLM 默认值：已完成。
2. 用户确认后在本机临时设置 Key，完成一次真实 DeepSeek 流式手测。
3. 实现内部/API 两层消息和 `ConversationManager.to_api_format()`。

## 2026-08-31：D 步 Bridge 模型模式接线

### 目标

- 把模型目录、客户端工厂和 Bridge 启动入口连成真实可运行链路。
- 默认继续使用 FakeLLM，不能因为电脑里碰巧有 Key 就自动联网。
- configured 缺 Key 时允许握手，在真正发送时返回清楚、可恢复的错误。
- 让 Electron 显示 Python 实际注入的模型，而不是把 FakeLLM 状态写死。

### 实际改动

- 新增 `src/jixue/bridge/bootstrap.py`，集中读取 `JIXUE_LLM_MODE` 和 `JIXUE_MODEL_ID`。
- `fake` 模式直接创建 `FakeLLMClient`；`configured` 模式读取目录、选择模型并调用 `create_llm_client()`。
- `server.main()` 不再直接写死 FakeLLM，而是在最外层调用 bootstrap 注入 `LLMClient`。
- 非法模式、模型 ID 或目录错误统一包装为 `BridgeBootstrapError`，只写 stderr 后退出。
- `bridge.ready` 增加 `payload.model`，声明当前客户端的真实 `model_name`。
- Electron Main 对 model 做运行时校验，保存为 `activeModelName`，状态显示“实际模型 / Bridge 在线”。
- `chat.send.payload.model_id` 使用握手取得的模型名，不再固定写成 `fake`。
- D 步初版给 `.env.example` 增加模式和模型 ID 说明，但当时还没有实现 `.env` 自动加载；这个缺口已在后面的“D 步修订”中补上。
- 新增本地 bootstrap 测试，覆盖默认离线、目录默认模型、Pro 选择、缺 Key 和非法配置。

### 启动链路

```text
PowerShell 环境变量
  → Electron Main process.env
  → Python 子进程 os.environ
  → server.main()
  → create_runtime_llm(Path.cwd())
  ├─ fake → FakeLLMClient
  └─ configured
       → models.yaml
       → ModelCatalog
       → 选择 JIXUE_MODEL_ID 或 default_model
       → create_llm_client()
       → AnthropicLLMClient
  → BridgeApplication
  → bridge.ready(model=实际模型名)
  → Electron 状态栏
```

### 遇到的问题

#### Electron 状态文本把 FakeLLM 写死

- 现象：即使 Python 注入正式客户端，Main 收到 `bridge.ready` 后仍显示“FakeLLM / Bridge 在线”。
- 原因：旧实现只把 ready 当布尔信号，没有让 Python 声明当前模型。
- 修复：握手 payload 增加 model；Electron 运行时检查非空字符串后保存并显示。
- 复盘：状态应来自事实发生的那一层。模型由 Python 注入，就应由 Python 握手声明，不能让 UI 猜。

#### D 步初版的 `.env` 认知落差（随后已修复）

- 现象：只修改 `.env.example` 不会影响 Electron 子进程。
- 原因：项目没有引入 dotenv；该文件从一开始只是“变量名称示例”。
- 初版处理：文件和教程曾写成“不会自动加载”，要求在启动 npm 的同一个 PowerShell 中设置变量。
- 后续判断：这虽然解释了现象，却不符合本项目希望的本地配置体验，也给初学者增加了不必要的终端环境知识。
- 最终修复：Bridge 主动读取项目根目录 `.env`，`.env.example` 只作为复制模板。
- 复盘：文档不能替代缺失的产品能力。用户合理期待项目读取 `.env` 时，应补齐明确、可测试的加载链路。

### 设计取舍

- 选择 `fake/configured`，而不是把模式命名为 `fake/deepseek`。
- 原因：模式表达领域语义；以后换供应商时 configured 仍然成立，不必修改 Electron 启动协议。
- 选择进程启动时选模型，暂不提前实现 UI 下拉框。
- 原因：本小步只验证配置到客户端的接线；UI 动态切换需要会话与模型路由设计，应单独完成。
- 选择 configured 缺 Key 时完成握手。
- 原因：目录与适配器组装本身是成功的，缺凭据属于发送前可补救状态；延迟创建保证不会误联网。
- 选择非法模式和损坏目录为启动错误。
- 原因：这类结构问题无法靠重试同一条消息修复，应在最靠近启动配置的位置明确拒绝。

### `Codex-api` 技能怎样影响实现

- bootstrap 和 Electron 只选择霁雪自己的 `LLMClient`，没有新增任何 SDK 导入。
- 真实请求仍只通过官方 Anthropic SDK 适配器，并继续携带提示缓存参数。
- 本小步使用缺 Key 和本地假对象验证边界，没有用原始 HTTP 或兼容层绕过适配器。

### 验证

- bootstrap 与 Bridge 定向测试 11 项通过。
- Ruff、Mypy（16 个 Python 源文件）和 TypeScript 类型检查通过。
- 完整 Python 33 项、前端 2 项通过。
- 真实 Electron 构建、`fake-jixue` 动态握手、FakeLLM 消息和无错误退出回归通过。
- 默认真实 Python 子进程握手返回 `model=fake-jixue`。
- configured 且明确无 Key 的真实 Python 子进程握手返回 `model=deepseek-v4-flash`；发送消息返回 `credentials_missing`，进程保持正常。
- 非法模式只输出一条中文 stderr 错误，没有 Python traceback，也没有污染 stdout。
- 尚未使用真实 Key，没有真实网络调用或模型费用。

### Git

- C 步提交：`c6e13ef feat(llm): 封装 Anthropic 流式适配器`。
- D 步按约定保持未提交，等待用户启动和手动测试。

## 2026-08-31：D 步修订——项目根目录 `.env` 自动加载

### 用户发现的问题

- 用户已经在当前项目根目录新增 `.env`。
- 文件中 `JIXUE_LLM_MODE=configured`、`JIXUE_MODEL_ID=flash` 和 `DEEPSEEK_API_KEY` 均已设置。
- 启动后仍显示 `fake-jixue`。

### 根因

`create_runtime_llm()` 当时直接读取 `os.environ`。Electron 虽然会把自己的系统环境传给
Python 子进程，但没有任何代码打开项目 `.env`。因此“文件存在”和“进程里有变量”是两
件不同的事，用户写入文件的内容根本没有进入模型目录加载器。

旧链路：

```text
项目 .env（没人读取，到这里断了）

系统环境 → Electron → Python os.environ → create_runtime_llm()
```

### 修复后的链路

```text
Electron 把 Python cwd 固定为项目根目录
  → server.main()
  → create_runtime_llm(Path.cwd())
  → load_project_environment(project_root)
       1. 复制 os.environ 作为兜底
       2. 用 python-dotenv 读取 project_root/.env
       3. .env 覆盖同名系统变量
  → JIXUE_LLM_MODE=configured
  → models.yaml 展开 DEEPSEEK_API_KEY
  → AnthropicLLMClient(model=deepseek-v4-flash)
```

### 实际改动

- `pyproject.toml` 显式增加 `python-dotenv` 运行依赖，不能只依赖其他包偶然间接安装它。
- `bootstrap.py` 新增 `load_project_environment()`，逐步注释复制、读取、覆盖和空值归一化。
- 使用 `dotenv_values()` 返回局部字典，不修改全局 `os.environ`，避免配置污染测试或其他模块。
- 关闭 dotenv 自带的 `${VAR}` 插值；`models.yaml` 仍是唯一负责密钥占位符展开的地方。
- `.env` 的同名值优先于系统环境；没有该文件时保留系统环境和 fake 默认值。
- `.env.example` 改成可复制的 configured 模板；示例文件自身不会被加载，也永远不写真实 Key。
- Electron 冒烟测试改用操作系统临时目录中的 fake `.env`，避免自动化读取用户 Key、调用真实模型或产生费用。
- 新增本地测试：系统环境故意选择 fake，临时项目 `.env` 选择 configured + pro，最终必须得到 `deepseek-v4-pro`。

### 为什么项目 `.env` 优先

用户明确希望“这个项目用这个文件”。如果系统里残留旧的 `JIXUE_LLM_MODE=fake`，却能
盖住当前项目的 configured 配置，就会再次出现“明明写了却不生效”。因此优先级固定为：

```text
项目根目录 .env > 系统环境变量 > 代码默认值
```

这里仍保留系统环境兜底，方便 CI 或高级用户在没有 `.env` 的环境启动；但日常开发只需
看当前项目文件，不必猜某个 PowerShell 或 Windows 全局变量里还残留什么。

### 安全边界

- 没有读取或打印用户 Key，只检查变量名、是否非空以及最终客户端类型/模型名。
- `.env` 已被 `.gitignore` 排除，`git status` 和提交都不会包含它。
- 自动化测试使用固定假 Key 或 fake 临时配置，不读取工作区真实 `.env`。
- 创建 `AnthropicLLMClient` 不会联网；只有用户在 UI 主动发送消息才会发起真实请求。

### 本轮已完成的验证

- `tests/bridge/test_bootstrap.py`：7 项通过。
- Ruff：`bootstrap.py`、`server.py` 和 bootstrap 测试通过。
- Mypy 严格检查：上述 3 个文件无类型问题。
- 只读装配当前项目配置：`client_type=AnthropicLLMClient`、`model_name=deepseek-v4-flash`。
- 完整 Python：34 项通过；完整 Ruff 通过；Mypy 严格检查 16 个源码文件通过。
- 前端 Vitest：2 项通过；TypeScript 类型检查通过。
- 真实 Python Bridge 收到 `bridge.hello` 后返回 `bridge.ready.model=deepseek-v4-flash`。
- Electron 构建和真实窗口回归通过；测试强制使用临时 FakeLLM 配置，聊天及无错误退出通过。
- 以上装配检查没有调用 `stream()`，所以没有网络请求和模型费用。

### 验证中遇到的 Windows 临时目录占用

- 现象：Electron 界面测试和退出断言已经完成，但删除临时项目目录时第一次返回 `EBUSY`。
- 原因：Windows 在 Python 子进程刚退出后仍可能短暂占用它之前的工作目录。
- 修复：`fs.rm()` 增加 5 次、每次间隔 200 毫秒的有限重试；只作用于 `mkdtemp()` 创建的专用目录。
- 复测：Electron 端到端测试完整通过，临时目录成功清理。
- 复盘：端到端测试的“通过”还包括资源回收。临时目录必须隔离真实 `.env`，清理又要考虑 Windows 文件锁的释放窗口。

### Git

- 本修订与尚未提交的 D 步放在一起。
- 按用户约定，本轮完成后不自动提交，先交给用户启动和手动测试。

## 2026-08-31：E 步两层消息与历史清洗

### 目标与范围

- 复用已有内部 `Message`，新增只有 role/content 的 `APIMessage`。
- 实现 `ConversationManager` 的加入、快照、清空和 `to_api_format()`。
- 本步不修改 Bridge、LLMClient 或 UI，避免同时改变存储规则和真实请求边界。

### 实际改动

- 新增 `domain/conversation.py`，代码注释按初学者阅读顺序解释每一步。
- `to_api_format()` 固定执行：过滤非 complete → 丢弃空白 → trim → 合并同角色 → 校验。
- 相邻同角色用两个换行合并；历史必须从 user 开头。
- `messages` 返回 tuple 快照；`add()` 拒绝重复 ID。
- `domain/__init__.py` 导出 `APIMessage`、`ConversationError` 和 `ConversationManager`。
- 新增本地测试，覆盖过滤、合并、不修改原历史、便捷方法、clear、重复 ID、空结果和 assistant 开头。

### 遇到的坑

- Mypy 在同一个测试里把“清空前的二元组”错误延续到清空后的比较。
- 将 `assert manager.messages == ()` 改成语义相同的 `assert not manager.messages` 后通过。
- 复盘：测试断言不仅要让 Python 正确，也要让静态类型检查器容易理解。

### 验证

- 对话管理器定向测试：7 项通过。
- Ruff：通过。
- 完整 Python：41 项通过。
- Mypy：17 个源码文件严格检查通过。

### Git

- D 步已提交：`4ec2c1b feat(llm): 接通项目配置与动态模型`。
- E 步按约定保持未提交，等待用户手测。

## 当前下一小步

1. 把 ConversationManager 接入 Bridge 与 LLMClient，真正发送完整历史。
2. 自动验证第二轮请求含第一轮 user/assistant。
3. 再进行真实 DeepSeek 多轮手测和 UI 模型选择。
