# 第 0 章开发日志

## 本章目标

在 Windows 上建立可启动、可测试、可回收的 Electron + Python 工程基线，并让 `bridge.hello → bridge.ready` 经过真实进程边界。

## 2026-08-30

### 计划

- 建立 Python `src` 布局和 Electron workspace。
- 定义 NDJSON 信封与 Python Bridge。
- 加入统一测试命令和架构边界测试。
- 用真实 Electron 验证进程启动和退出。

### 实际改动

- 新增 `pyproject.toml`，固定 pytest、Ruff、Mypy 和开发依赖。
- 新增根级 npm workspace、Electron Main/Preload/Renderer 基线与 lockfile。
- 新增 Python 协议信封、Bridge 应用层和异步 stdin/stdout 服务。
- 新增 `config/models.yaml` 和不含密钥的 `.env.example`，真实配置尚未加载。
- 新增 Python、前端、Renderer 浏览器和真实 Electron 四层测试。

### 遇到的问题

#### PowerShell 5 读取无 BOM 中文脚本失败

- 现象：统一测试脚本中的中文字符串被错误解码，PowerShell 报解析错误。
- 原因：旧版 Windows PowerShell 对无 BOM UTF-8 的自动识别不可靠。
- 排查过程：Python 测试和 npm 测试单独正常，只有 `.ps1` 入口解析失败。
- 修复：让执行脚本只包含 ASCII 命令；中文说明保留在 Markdown 文档中。
- 如何防止复发：不在需要兼容 Windows PowerShell 5 的无 BOM 脚本中加入中文字符串。

#### 前端构建工具主版本不一致

- 现象：TypeScript 报 Vite 插件类型不兼容，配置对象无法赋值。
- 原因：最新版 `@vitejs/plugin-react` 拉入 Vite 8 类型，而 `electron-vite` 当前使用 Vite 7。
- 排查过程：比较 lockfile 中两套 Vite 类型来源，确认并非业务配置写错。
- 修复：把 `@vitejs/plugin-react` 固定为 `5.2.0`，与 Vite 7 保持一致；同时删除 TypeScript 7 已移除的 `baseUrl` 配置。
- 如何防止复发：升级构建链时把 Electron Vite、Vite、React 插件和 TypeScript 作为一组验证。

#### Electron 包存在但二进制缺失

- 现象：`npm run dev` 提示 Electron 安装不正确。
- 原因：首次依赖安装没有完成 Electron 的 postinstall 下载。
- 排查过程：确认 `node_modules/electron` 存在，但 `dist/electron.exe` 不存在。
- 修复：执行 `node node_modules/electron/install.js` 补齐二进制；正常的 `npm install` 通常会自动完成。
- 如何防止复发：首次安装后先检查 `npm run dev`，不要把“包目录存在”当作 Electron 已可运行。

### 设计取舍

- 选择：使用 stdin/stdout NDJSON，而不是在第 0 章引入 HTTP Server。
- 没选的方案：本地端口、WebSocket、原生 Node 扩展。
- 原因：NDJSON 没有端口占用和认证面，足够表达流式事件，也便于终端手测。
- 以后何时重新评估：出现多个前端客户端或需要跨主机连接时。

### 验证

- 自动化测试：Python 11 项通过，前端 2 项通过；Ruff、Mypy、TypeScript 类型检查和 Electron 构建通过。
- 手动测试：Electron 启动后显示 `fake-jixue / Bridge 在线`；关闭应用后 Bridge 随之退出。
- 已知限制：Bridge 当前只有单进程本地通信；没有重启退避和日志文件轮转。

### Git

- 分支：`main`
- 功能提交：`676759f feat(foundation): 打通 FakeLLM 桌面流式链路`
- 文档提交：由本次文档提交记录。

## 2026-08-30：退出错误修复

### 现象与原因

- 现象：手动关闭窗口时偶发 “A JavaScript error occurred in the main process”。
- 原因：窗口已销毁后，Python Bridge 的退出状态仍通过旧的 `webContents` 发送；同时 stdin 与子进程关闭存在竞态。

### 修复

- 窗口 `closed` 时清空全局引用。
- 发送 IPC 前同时检查 BrowserWindow 与 webContents 是否已经销毁。
- Bridge 停止时保护 stdin error、重复 kill 和进程已自行退出的情况。
- 真实 Electron 测试改为主动关闭窗口并扫描主进程 stderr。

### 验证

- `npm run test:electron` 通过。
- 主进程 stderr 未出现 JavaScript Error、`Object has been destroyed` 或未处理 Promise。
- 本次改动未提交，等待用户验收。

## 2026-08-30：零基础教学重写

### 原问题

- 文档直接使用进程、NDJSON、stdin/stdout、Preload 等术语，没有建立前置心智模型。
- 只列入口函数，没有解释从 `npm run dev` 到 `bridge.ready` 的实际顺序。
- 没有推荐阅读顺序，读者不知道先看哪个文件。

### 本次改动

- 增加程序/进程、Electron 三层、标准通道、JSON/NDJSON 和 Bridge 的零基础解释。
- 增加三轮推荐阅读顺序。
- 用 12 个步骤追踪启动握手，并给出 hello/ready 的具体 JSON。
- 增加退出链路、文件地图、跟做练习、常见问题和带答案自测题。

### 验证

- 对照当前 Main、BridgeServer、Envelope、BridgeApplication 与 Preload 源码复核链路。
- 本次只修改文档和协作规范，没有修改运行代码，也没有提交 Git。

## 2026-08-30：源码教学注释补全

### 原问题

- 文件之间虽然已经能运行，但初学者只看源码时不知道当前文件位于哪一段链路。
- Electron 主进程、Python 子进程和 NDJSON 解析函数缺少“谁调用、输入输出、为什么存在”的就近说明。
- 异常处理看起来像很多零散判断，没有解释它们是在防止退出竞态和坏消息拖垮进程。

### 本次改动

- 为 Main、Preload、PythonBridge 和共享协议信封补充文件级与函数级中文注释。
- 为 BridgeServer、BridgeApplication、Envelope 及领域消息类型补充调用关系、数据形状和降级策略说明。
- 把“每个源码文件都应可作为零基础教材阅读”的规则写入项目协作约定与开发指南。
- 本地测试文件也补充测试目的、模拟边界和断言原因，测试文件仍由 `.gitignore` 排除。

### 验证

- 注释只解释既有行为，没有改变进程协议或运行逻辑。
- 完整自动化结果记录在本轮最终报告中。
- 本次改动不提交 Git，等待用户先手动阅读和测试。
