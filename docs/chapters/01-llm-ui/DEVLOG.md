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

## 下一小步

1. 定义并测试 `LLMConfig` 与 YAML 加载器。
2. 新建只允许在适配器目录导入的 Anthropic 客户端。
3. 用假 SDK 流先测试转换，再使用 DeepSeek Key 做一次人工集成测试。
4. 实现内部/API 两层消息和 `ConversationManager.to_api_format()`。
