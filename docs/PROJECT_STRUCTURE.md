# 目录与文件职责

这份文件只回答一个问题：当前每个目录和源码文件是干什么的。新增、删除文件时同步更新这里。

## 总览

```text
myAgent/
├─ apps/desktop/       Electron + React 桌面端
├─ config/             模型选择配置
├─ docs/               路线、目录说明和每章唯一的 README
├─ scripts/            本地检查脚本
├─ src/jixue/          Python 后端核心
├─ tests/              本地测试，Git 忽略
├─ .env                本地密钥，Git 忽略
├─ .env.example        不含密钥的配置示例
├─ pyproject.toml      Python 项目和检查工具配置
└─ package.json        前端命令总入口
```

## Python 后端

| 文件 | 职责 |
| --- | --- |
| `src/jixue/domain/conversation.py` | 消息结构、多轮历史和发给 API 前的清洗 |
| `src/jixue/domain/events.py` | Electron 与 Python 之间的一行 JSON 信封 |
| `src/jixue/llm/base.py` | 霁雪自己的 LLM 接口和流式事件 |
| `src/jixue/llm/fake.py` | 离线模拟 LLM，方便免费测试全链路 |
| `src/jixue/llm/config.py` | 从 `models.yaml` 和环境中生成四字段配置 |
| `src/jixue/llm/adapters/anthropic_client.py` | 唯一接触 Anthropic SDK 的适配器 |
| `src/jixue/bridge/bootstrap.py` | 读取项目根目录 `.env`，选择 Fake 或真实 LLM |
| `src/jixue/bridge/application.py` | 一轮聊天的业务核心：历史 → LLM → UI 事件 |
| `src/jixue/bridge/server.py` | 从 stdin 收 JSON，从 stdout 发 JSON |
| `src/jixue/bridge/__main__.py` | 让 `python -m jixue.bridge` 能启动 |
| `src/jixue/tools/base.py` | 工具合同、ToolResult 和通用 BaseTool |
| `src/jixue/tools/registry.py` | 注册、启用、禁用和导出工具定义 |
| `src/jixue/tools/read_file.py` | 第一个只读文件工具工厂 |
| `src/jixue/tools/__init__.py` | 工具层公开导入入口 |

`domain` 不知道 Electron 和 Anthropic 的存在；`adapters` 专门藏住外部 SDK；`bridge` 把桌面端和 Python 业务接起来。

## Electron 桌面端

| 文件 | 职责 |
| --- | --- |
| `apps/desktop/src/main/index.ts` | 创建窗口、启动/关闭 Python、转发 IPC |
| `apps/desktop/src/main/bridge-process.ts` | 启动 Conda 子进程并处理一行一个 JSON |
| `apps/desktop/src/preload/index.ts` | 只向网页暴露安全的聊天接口 |
| `apps/desktop/src/shared/protocol.ts` | 前后端共用的事件类型 |
| `apps/desktop/src/renderer/src/App.tsx` | 聊天页面和事件接收 |
| `apps/desktop/src/renderer/src/state.ts` | reducer：根据事件更新消息、状态和用量 |
| `apps/desktop/src/renderer/src/styles.css` | Codex 风格的界面样式 |
| `apps/desktop/src/renderer/src/main.tsx` | React 页面入口 |

其余 `electron.vite.config.ts`、`tsconfig.json` 和各级 `package.json` 是构建配置，不参与一条消息的业务处理。

## 配置和文档

| 文件 | 职责 |
| --- | --- |
| `config/models.yaml` | 三个模型的短名称和真实模型 ID |
| `docs/ROADMAP.md` | 章节顺序和一周范围 |
| `docs/chapters/00-foundation/README.md` | 环境准备、启动和测试 |
| `docs/chapters/01-llm-ui/README.md` | 第一章代码和完整消息链路 |
| `docs/chapters/02-tools/README.md` | 第二章工具底座和执行链路 |
| `scripts/test-all.ps1` | 顺序执行本地自动化检查 |

每章目录只允许有一个 `README.md`。测试文件虽然存在于本机，但由 `.gitignore` 排除，不会提交。
