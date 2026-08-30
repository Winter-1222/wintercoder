# 第 0 章：工程基线

## 学习目标

- 理解为什么 Electron 与 Python 之间需要一个窄协议，而不是互相调用内部对象。
- 能从零启动 `mycoder` 环境、Python Bridge 和 Electron 客户端。
- 能解释 stdout、stderr、Preload 和领域层隔离各自解决的问题。

## 问题背景

Agent Harness 后面会同时涉及模型 SDK、工具、权限、MCP 和长期状态。如果第一天就把它们接在一起，任何一次失败都可能来自网络、API、进程、前端或业务逻辑。第 0 章只建立一条没有外部网络依赖的进程链路，让每一层都可以单独替换和测试。

## 核心概念

### NDJSON Bridge

Electron Main 通过 stdin 向 Python 子进程发送“一行一个 JSON”，Python 通过 stdout 返回同样格式的事件。每条消息都有 `version`、`type`、`request_id`、`sequence` 和 `payload`。这是一条进程协议，不是把 Python 类直接暴露给 JavaScript。

```text
Renderer → Preload 窄接口 → Electron Main → stdin → Python Bridge
Renderer ← Preload 事件   ← Electron Main ← stdout ← Python Bridge
```

stdout 只能放协议数据。普通诊断写 stderr，否则 Electron 读到日志文本后会把它当 JSON 解析，整条链路随即失效。

### Electron 安全边界

Renderer 不拥有 Node.js 权限，也不知道如何启动 Python。Preload 只暴露四个领域动作：发送聊天、读取 Bridge 状态、订阅业务事件、订阅连接状态。这样以后替换进程实现时，界面不需要跟着改。

### 领域层隔离

`src/jixue/domain` 和 `src/jixue/llm/base.py` 只定义霁雪自己的消息、事件和协议。架构测试会拒绝它们导入 `anthropic`、Electron 或 MCP SDK。外部实现只能在适配器边界出现。

## 设计边界

- 输入：Electron 发来的 NDJSON 命令。
- 输出：带请求 ID 和序号的 NDJSON 事件。
- 依赖：Python 标准库、Electron、React；默认链路不访问网络。
- 明确不负责：真实模型配置、对话历史、工具调用和权限判断。

## 小步实现

### 步骤 1：建立双语言工作区

- 目标：固定 Python `src` 布局、Electron workspace 和统一测试入口。
- 涉及文件：`pyproject.toml`、`package.json`、`apps/desktop/package.json`。
- 关键设计：Python 命令固定由 `conda run -n mycoder` 执行；Node 依赖由根目录 lockfile 管理。
- 自动化测试：pytest、Vitest、Ruff、Mypy 和 TypeScript 类型检查。

### 步骤 2：定义稳定信封

- 目标：让进程两端只依赖字段协议。
- 涉及文件：`src/jixue/domain/events.py`、`apps/desktop/src/shared/protocol.ts`。
- 关键设计：协议版本固定为 `1`，未知或非法信封转成协议错误，不让进程崩溃。
- 自动化测试：序列化往返、版本错误、缺失字段和非法载荷。

### 步骤 3：启动和守护 Python 子进程

- 目标：Electron 打开时自动启动 Bridge，关闭时回收。
- 涉及文件：`src/jixue/bridge/server.py`、`apps/desktop/src/main/bridge-process.ts`。
- 关键设计：Main 使用 `conda run --no-capture-output -n mycoder python -u -m jixue.bridge`；stderr 单独记录，stdout 按行解析。
- 自动化测试：Bridge 应用层握手测试和真实 Electron 冒烟测试。

### 步骤 4：只暴露最小 Preload API

- 目标：保持 Renderer 无 Node 权限。
- 涉及文件：`apps/desktop/src/preload/index.ts`、`apps/desktop/src/main/index.ts`。
- 关键设计：启用 `contextIsolation` 和 Renderer sandbox；禁止任意导航、新窗口和未知 IPC 发送方。
- 自动化测试：TypeScript 类型检查与真实窗口测试。

## 代码导航

- `Envelope.from_json_line()`：Python 协议入口。
- `run_server()`：异步读取 stdin，锁定 stdout 写入顺序。
- `PythonBridge.start()`：创建 Conda 子进程并把协议事件转发给窗口。
- `window.jixue`：Renderer 唯一可见的主进程能力。
- `tests/test_architecture.py`：守住外部 SDK 隔离边界。

## 练习题

1. 如果把普通日志写到 stdout，为什么错误会出现在 Electron 而不是日志语句所在的位置？
2. 尝试给信封增加一个可选 `trace_id`，同时修改 Python、TypeScript 类型和往返测试。

## 本章小结

第 0 章没有急着接真实模型，而是先建立了可独立验证的 Electron—Python 边界。Renderer 只说领域语言，Main 管进程，Bridge 管协议，领域层不认识任何外部 SDK。以后替换模型或通信实现时，上层界面不需要知道变化发生在哪里。
