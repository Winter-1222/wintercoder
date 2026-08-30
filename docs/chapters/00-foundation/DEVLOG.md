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
- 手动测试：Electron 启动后显示 `FakeLLM / Bridge 在线`；关闭应用后 Bridge 随之退出。
- 已知限制：Bridge 当前只有单进程本地通信；没有重启退避和日志文件轮转。

### Git

- 分支：`main`
- 功能提交：`676759f feat(foundation): 打通 FakeLLM 桌面流式链路`
- 文档提交：由本次文档提交记录。
