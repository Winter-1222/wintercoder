# 霁雪（Jixue）

霁雪是一个以教学和可复盘开发为目标的轻量 Agent Harness。名字取自“雪后初晴”：内核保持清晰，外部模型、工具和界面都通过边界明确的适配器接入。

当前阶段只完成总体设计，尚未开始第一章代码。

## 开发基线

- Python：Conda 环境 `mycoder`，当前为 Python 3.12.13
- 桌面端：Electron + TypeScript
- LLM SDK：Python `anthropic`，仅允许出现在 LLM 适配器内
- 默认模型：`deepseek-v4-flash`
- 默认协议端点：Anthropic 协议，`https://api.deepseek.com/anthropic`
- 版本管理：Git，主分支 `main`

## 文档入口

- [总体开发路线](docs/ROADMAP.md)
- [总体架构](docs/ARCHITECTURE.md)
- [开发与复盘约定](docs/DEVELOPMENT_GUIDE.md)

每章开始时，从 `docs/templates/` 复制教学、开发日志和手动测试模板到对应章节目录。
