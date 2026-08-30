# 霁雪目录与文件职责

本文只记录当前工作区已经存在的内容。新增、删除或移动文件时必须同步更新本文，避免目录结构与代码脱节。未来章节的规划放在 `ROADMAP.md`，不在这里提前列空目录。

标记说明：

- **提交**：属于项目正式源码或文档，应进入 Git。
- **本地**：用于当前开发机验证，被 `.gitignore` 排除，不进入 Git。
- **生成**：由安装、构建或运行产生，可安全重新生成。

## 1. 根目录

```text
myAgent/
├─ apps/                 Electron 桌面客户端
├─ config/               可提交的应用配置模板
├─ docs/                 架构、路线、教学和复盘文档
├─ scripts/              开发命令入口
├─ src/                  Python 主源码
├─ tests/                本地自动化测试，不提交
├─ .env.example          环境变量示例
├─ .gitignore            Git 忽略规则
├─ AGENTS.md             霁雪项目协作约定
├─ package.json          Node workspace 与根级命令
├─ package-lock.json     Node 依赖锁文件
├─ pyproject.toml        Python 工程与工具配置
└─ README.md             项目入口和快速开始
```

| 文件 | 类型 | 职责 |
| --- | --- | --- |
| `.env.example` | 提交 | 只声明环境变量名称，不存放真实 API Key。 |
| `.gitignore` | 提交 | 排除依赖、构建产物、密钥、运行数据和本地测试。 |
| `AGENTS.md` | 提交 | 约束注释语言、Conda 环境、SDK 边界、Git 和文档流程。 |
| `package.json` | 提交 | 声明 npm workspace，并提供开发、构建、类型检查和本地测试命令。 |
| `package-lock.json` | 提交 | 锁定 Electron/React 等 Node 依赖的完整版本树。 |
| `pyproject.toml` | 提交 | 声明 Python 包、开发依赖以及 pytest、Ruff、Mypy 配置。 |
| `README.md` | 提交 | 告诉开发者如何安装、启动、测试以及从哪里阅读文档。 |

## 2. `apps/desktop`：Electron 客户端

```text
apps/desktop/
├─ src/
│  ├─ main/
│  │  ├─ index.ts
│  │  └─ bridge-process.ts
│  ├─ preload/
│  │  └─ index.ts
│  ├─ renderer/
│  │  ├─ src/
│  │  │  ├─ App.tsx
│  │  │  ├─ main.tsx
│  │  │  ├─ state.ts
│  │  │  ├─ state.test.ts       本地，不提交
│  │  │  ├─ styles.css
│  │  │  └─ env.d.ts
│  │  ├─ favicon.svg
│  │  └─ index.html
│  └─ shared/
│     └─ protocol.ts
├─ electron.vite.config.ts
├─ package.json
└─ tsconfig.json
```

### Main 进程

| 文件 | 类型 | 职责 |
| --- | --- | --- |
| `src/main/index.ts` | 提交 | 创建安全 BrowserWindow、注册 IPC、启动 Bridge，并在退出时安全回收资源。 |
| `src/main/bridge-process.ts` | 提交 | 在 `mycoder` 中启动 Python，读写 NDJSON，维护 Bridge 状态并隔离 stderr。 |

Main 进程只处理窗口、IPC 和进程生命周期，不解释 Agent 业务，也不把 Node 能力直接交给 Renderer。

### Preload 与共享协议

| 文件 | 类型 | 职责 |
| --- | --- | --- |
| `src/preload/index.ts` | 提交 | 通过 `contextBridge` 暴露发送消息、读取状态和订阅事件四个窄接口。 |
| `src/shared/protocol.ts` | 提交 | 定义 Main、Preload、Renderer 共用的 Bridge 信封、状态和桌面 API 类型。 |

### Renderer

| 文件 | 类型 | 职责 |
| --- | --- | --- |
| `src/renderer/index.html` | 提交 | Renderer HTML 入口，设置 CSP、主题色和挂载节点。 |
| `src/renderer/favicon.svg` | 提交 | 霁雪雪花图标。 |
| `src/renderer/src/main.tsx` | 提交 | 创建 React Root，加载应用与全局样式。 |
| `src/renderer/src/App.tsx` | 提交 | Codex 风格侧栏、对话区、状态展示、输入框和 Bridge 事件订阅。 |
| `src/renderer/src/state.ts` | 提交 | 用纯 reducer 管理消息、请求、Token、耗时和 Bridge 状态。 |
| `src/renderer/src/styles.css` | 提交 | 中性浅色工作台的布局、排版、Markdown、响应式和动效。 |
| `src/renderer/src/env.d.ts` | 提交 | 引入 Vite 类型并声明 Renderer 的 `window.jixue` 类型。 |
| `src/renderer/src/state.test.ts` | 本地 | 验证 reducer 的流式追加、完成和错误收口，不进入 Git。 |

### 桌面工程配置

| 文件 | 类型 | 职责 |
| --- | --- | --- |
| `electron.vite.config.ts` | 提交 | 分别配置 Main、Preload 和 Renderer 的构建入口。 |
| `package.json` | 提交 | 声明桌面依赖以及 dev/build/test/typecheck 命令。 |
| `tsconfig.json` | 提交 | TypeScript 严格模式、JSX 和路径检查配置。 |

`apps/desktop/out/` 是构建产物，`node_modules/` 是安装依赖，两者均为生成内容，不提交。

## 3. `src/jixue`：Python 主源码

```text
src/jixue/
├─ bridge/
│  ├─ __init__.py
│  ├─ __main__.py
│  ├─ application.py
│  └─ server.py
├─ domain/
│  ├─ __init__.py
│  ├─ events.py
│  └─ messages.py
├─ llm/
│  ├─ __init__.py
│  ├─ base.py
│  └─ fake.py
└─ __init__.py
```

### Python 包根

| 文件 | 类型 | 职责 |
| --- | --- | --- |
| `src/jixue/__init__.py` | 提交 | 声明 Python 包和当前版本。 |

### `domain`：纯领域类型

| 文件 | 类型 | 职责 |
| --- | --- | --- |
| `domain/__init__.py` | 提交 | 导出领域层公共类型。 |
| `domain/events.py` | 提交 | 定义 NDJSON `Envelope`、协议版本、序列化和协议错误。 |
| `domain/messages.py` | 提交 | 定义消息角色、状态、用量和内部消息元数据。 |

这里不能导入 Anthropic、Electron 或 MCP SDK。

### `llm`：模型领域接口

| 文件 | 类型 | 职责 |
| --- | --- | --- |
| `llm/__init__.py` | 提交 | 导出 LLM 接口和 FakeLLM。 |
| `llm/base.py` | 提交 | 定义供应商无关的 `LLMClient` Protocol 与流事件。 |
| `llm/fake.py` | 提交 | 产生确定性 Markdown 文本、Token 和完成事件，用于离线开发。 |

真实 `anthropic` SDK 将来只能出现在本目录的适配器子目录中，不能进入 `base.py`。

### `bridge`：进程协议入口

| 文件 | 类型 | 职责 |
| --- | --- | --- |
| `bridge/__init__.py` | 提交 | 声明 Bridge 包。 |
| `bridge/__main__.py` | 提交 | 支持 `python -m jixue.bridge` 启动服务。 |
| `bridge/application.py` | 提交 | 处理 `bridge.hello` 和 `chat.send`，把 LLM 事件转成 UI 信封。 |
| `bridge/server.py` | 提交 | 异步读取 stdin、限制单行大小、串行写 stdout，并把日志送到 stderr。 |

## 4. `config`：可提交配置

| 文件 | 类型 | 职责 |
| --- | --- | --- |
| `config/models.yaml` | 提交 | 描述 Flash、Pro、Vision Exp 三个模型目录；Key 只写环境变量占位符。 |

本地覆盖应放在 `config/models.local.yaml`，该文件被忽略，不提交。

## 5. `scripts`：开发命令

| 文件 | 类型 | 职责 |
| --- | --- | --- |
| `scripts/test-all.ps1` | 提交 | 在 `mycoder` 中运行本地 Python 测试，再运行前端本地测试。 |

脚本本身属于开发基础设施，可以提交；它调用的具体测试源码只保留在本地。

## 6. `tests`：本地自动化测试

整个目录为**本地内容，不进入 Git**，但当前开发机继续保留和运行：

| 文件 | 职责 |
| --- | --- |
| `tests/domain/test_events.py` | 验证信封序列化、协议版本和错误输入。 |
| `tests/bridge/test_application.py` | 验证握手、FakeLLM 流式顺序、Token 和完成事件。 |
| `tests/test_architecture.py` | 防止领域层导入 Anthropic、Electron 或 MCP SDK。 |
| `tests/ui/smoke_renderer.py` | 用本机 Chrome 和 Mock Bridge 检查真实 Renderer 布局、交互和控制台。 |
| `tests/ui/smoke_electron.mjs` | 启动真实 Electron，覆盖 Main、Preload、Bridge、Renderer，并检查退出错误。 |

`artifacts/ui/` 保存本地测试截图，也是本地生成内容，不提交。

## 7. `docs`：设计与复盘

| 文件或目录 | 类型 | 职责 |
| --- | --- | --- |
| `docs/ROADMAP.md` | 提交 | 一周开发顺序、各章小步、测试重点和退出条件。 |
| `docs/ARCHITECTURE.md` | 提交 | 稳定架构边界、进程数据流和核心协议。 |
| `docs/PROJECT_STRUCTURE.md` | 提交 | 当前文件树和每个文件的职责，也就是本文。 |
| `docs/DEVELOPMENT_GUIDE.md` | 提交 | 中文注释、开发循环、测试、密钥和 Git 约定。 |
| `docs/templates/` | 提交 | 新章节教学、开发日志、手测记录的中文模板。 |
| `docs/chapters/00-foundation/` | 提交 | 第 0 章工程基线的教学、日志和手测。 |
| `docs/chapters/01-llm-ui/` | 提交 | 第一章 LLM/UI 的教学、日志和手测。 |

每个章节目录固定包含：

- `README.md`：概念、设计边界、小步实现和代码导航。
- `DEVLOG.md`：改动、错误、修复、取舍和验证结果。
- `MANUAL_TEST.md`：开发者可以照着执行的手动测试步骤。

## 8. 运行时与生成目录

以下内容不属于源码，已经由 `.gitignore` 排除：

| 路径 | 来源 | 是否可删除后重建 |
| --- | --- | --- |
| `node_modules/` | `npm install` | 是。 |
| `apps/desktop/out/` | `npm run build` | 是。 |
| `artifacts/` | UI 冒烟测试截图 | 是。 |
| `.pytest_cache/`、`.mypy_cache/`、`.ruff_cache/` | Python 工具 | 是。 |
| `.jixue/`、`sessions/`、`memories/`、`tool-results/` | 未来运行数据 | 不应提交；删除前需确认是否含用户数据。 |
| `.env`、`config/*.local.*` | 本地密钥和覆盖配置 | 不可从仓库恢复，应由开发者自行保管。 |

## 9. 新增文件时怎么维护本文

每次新增文件至少完成三件事：

1. 把它放进符合职责的目录；不要创建只有一层转发意义的空目录。
2. 在本文对应章节增加一行，说明它的输入、输出或边界职责。
3. 在当前章节 `DEVLOG.md` 记录新增原因，并在交付时告诉用户如何启动和测试；未得到明确要求前不提交 Git。
