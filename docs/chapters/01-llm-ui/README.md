# 第 1 章：让 AI 开口说话并可视化

> 当前进度：已完成 A 步“FakeLLM 流式链路”。真实 DeepSeek、配置加载、消息转换和多轮历史将在本章后续小步完成。

## 学习目标

- 理解领域 `LLMClient` 与供应商 SDK 适配器之间的边界。
- 能追踪一个文本增量从 Python 到 Electron UI 的完整路径。
- 理解为什么流式阶段不做 Markdown 渲染，结束后才渲染。
- 能用 FakeLLM 在没有网络和密钥时稳定复现界面行为。

## 问题背景

直接拿真实 API 调界面会把网络、密钥、供应商事件格式、进程通信和 React 状态五类问题混在一起。第一小步先用一个确定性 FakeLLM 产出不规则文本碎片、Token 用量和完成事件。只有这条本地链路稳定后，才让 Anthropic SDK 进入专用适配器。

## 核心概念

### 自己的 LLM 事件

`LLMClient` 返回 `LLMStreamEvent`，而不是 Anthropic SDK 的流事件。当前领域事件只有：

- `text`：一小段文本增量。
- `usage`：本轮输入和输出 Token。
- `complete`：停止原因和最终用量。
- `error`：可理解的模型调用错误。

Bridge 再把它们翻译成 UI 认识的 `stream_text`、`usage`、`turn_complete` 和 `error` 信封。这层翻译让 Renderer 不需要知道模型供应商。

### 流式文本与 Markdown 是两个阶段

Markdown 语法可能在任意字符处断开，例如反引号、标题符号或链接只到一半。每收到一个字符就重新解析会闪烁，也可能暂时生成错误结构。霁雪在消息状态为 `streaming` 时用 `<pre>` 展示原始文本；收到 `turn_complete` 后将状态改成 `complete`，再交给 React Markdown 渲染。

### UI 状态机

界面使用 reducer 维护消息、当前请求、Bridge 状态、用量和耗时。发送时先加入用户消息与空的 assistant 消息，之后只按 `request_id` 追加文本。完成或错误事件关闭本次请求，使输入框可以再次发送。

```text
idle → request_started → text_received × N → usage_received → request_completed → idle
                                         ↘ request_failed ───────────────↗
```

## 设计边界

- 输入：用户文本和 Bridge 事件。
- 输出：流式消息、完成后的 Markdown、模型/Token/耗时状态。
- 依赖：领域 `LLMClient`、NDJSON Bridge、React；当前实现使用 FakeLLM。
- 明确不负责：工具调用、Agent Loop、长期上下文和真实权限。

## 小步实现

### A 步：FakeLLM 与流式 UI（已完成）

- 目标：不依赖网络打通完整桌面链路。
- 涉及文件：`src/jixue/llm/base.py`、`src/jixue/llm/fake.py`、`src/jixue/bridge/application.py`、`apps/desktop/src/renderer/src/App.tsx`。
- 关键设计：FakeLLM 用 `1、2、5、3、8` 的循环块长切分 Markdown，主动覆盖碎片边界问题。
- 自动化测试：Bridge 事件顺序、reducer 行为、浏览器 Renderer 冒烟和真实 Electron 冒烟。

### B 步：配置与真实适配器（下一步）

- 目标：从 `config/models.yaml` 加载 `protocol/model/base_url/api_key` 四字段，默认选择 Flash。
- 计划文件：`src/jixue/config/` 与 `src/jixue/llm/adapters/anthropic_client.py`。
- 关键设计：只有适配器允许 `import anthropic`；缺少 Key 时 UI 可启动，但发送真实请求前返回可操作错误。
- 自动化测试：环境变量替换、三模型目录、SDK 假流和异常转换。

### C 步：对话管理器（待开始）

- 目标：维护内部消息元数据，并生成干净的 API 消息。
- 关键设计：API 层只有 `role + content`；内部层保留 ID、状态、时间、Token。`to_api_format()` 过滤失败消息、合并同角色、校验 user/assistant 交替。
- 自动化测试：空内容、失败半截回复、同角色合并和非法开头。

### D 步：模型选择与真实手测（待开始）

- 目标：切换 Flash、Pro、Vision Exp，完成十轮普通对话回归。
- 关键设计：Vision Exp 首版明确标注“实验、仅文本”，不提前实现图片上传。

## 代码导航

- `LLMClient.stream()`：上层唯一依赖的模型流协议。
- `FakeLLMClient.stream()`：无需网络的确定性事件源。
- `BridgeApplication.handle()`：把命令路由到模型，并产生连续序号事件。
- `chatReducer()`：纯函数形式的 UI 状态变化。
- `MessageView`：按消息状态选择原始流或 Markdown。
- `PythonBridge`：Main 进程中的子进程生命周期与事件转发。

## 视觉设计说明

界面采用“极夜观测站”方向：深海军蓝作为夜空，冰白承载正文，青蓝只用于状态和交互。标题使用编辑感较强的 Newsreader，运行数据用等宽字。布局保留标题栏、对话区、状态栏和输入区四个明确区域，没有加入与第一章无关的导航和设置页。

## 练习题

1. 把 FakeLLM 的块长改成全部为 `1`，比较流式阶段和完成阶段的性能与视觉差异。
2. 给 reducer 增加一条测试：收到旧请求的迟到 `turn_complete` 时，不能错误结束新请求。
3. 不看代码画出从点击“发送”到 Markdown 完成渲染的事件序列。

## 本章小结

当前版本已经证明 UI、IPC、子进程和领域事件能够协作，但没有假装完成真实模型与多轮对话。FakeLLM 把不稳定外部因素拿掉，使接下来接 Anthropic SDK 时只需验证适配器与配置。真实 SDK 数据会在适配器内被立即转成霁雪自己的事件，上层边界保持不变。
