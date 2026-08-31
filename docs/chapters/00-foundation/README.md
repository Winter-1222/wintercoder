# 第 0 章：工程准备

这一章只负责把开发环境搭起来，不包含 Agent 功能。

## 推荐阅读顺序

1. 先看根目录 `README.md`，知道项目能做什么。
2. 看本页完成安装和启动。
3. 再看第一章 README，理解一条消息怎么运行。

## 三个基础概念

- **Conda 环境**：隔离 Python 及其依赖。本项目固定使用 `mycoder`。
- **Electron**：用网页技术制作桌面客户端。它负责窗口，不负责调用 LLM。
- **进程**：一个正在运行的程序。霁雪同时有 Electron 进程和 Python 进程。

## 第一次安装

在项目根目录打开 PowerShell：

```powershell
conda run --no-capture-output -n mycoder python -m pip install -e ".[dev]"
npm install
```

第一条安装 Python 包，第二条安装 Electron/React 包。只需在依赖变化时重新执行。

## 启动

```powershell
npm run dev
```

正常现象：出现“霁雪 Jixue”窗口，左下角最终显示 `fake-jixue / Bridge 在线`。这说明 Electron 已成功启动 Python。

## 自动测试

```powershell
conda run --no-capture-output -n mycoder pytest
npm run test:frontend
npm run typecheck
```

`tests/` 是本地测试目录，已被 Git 忽略。测试失败只表示要修代码，不会自动改动代码。

## 手动检查

1. 执行 `npm run dev`。
2. 确认窗口能够出现。
3. 确认左下角 Bridge 变成在线。
4. 关闭窗口，确认没有 JavaScript 错误弹窗。

常见问题：

- 提示找不到 `conda`：先在终端执行 `conda --version`。
- Bridge 一直离线：确认环境名确实是 `mycoder`。
- `npm` 找不到：安装 Node.js 后重新打开 PowerShell。

## 本章变更记录

- 建立 Python、Electron、TypeScript 和 Git 基线。
- 约定 Python 命令都在 `mycoder` 中运行。
- 约定测试文件留在本地，不提交 Git。

## 自测题与答案

**问：为什么不能直接运行系统 Python？**

答：系统 Python 的包版本可能不同。固定 `mycoder` 能让开发和测试使用同一套依赖。

**问：关闭 Electron 时，Python 为什么也要关闭？**

答：Python 是 Electron 启动的子进程。窗口退出后若不关闭它，就会留下无用的后台进程。
