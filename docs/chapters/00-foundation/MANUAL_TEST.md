# 第 0 章手动测试

## 测试环境

- 日期：2026-08-30
- 操作系统：Windows
- Conda 环境：`mycoder`
- Python 版本：3.12.13
- Node/Electron 版本：Node.js 24.15.0 / Electron 44.0.0
- 模型：`fake-jixue`

## 准备

```powershell
conda run --no-capture-output -n mycoder python -m pip install --index-url https://pypi.org/simple -e ".[dev]"
npm install
```

本章不需要 API Key。若 Electron 包已安装但二进制缺失，可执行 `node node_modules/electron/install.js` 修复本地安装。

## 用例 1：验证开发环境

- 前置条件：Conda 中存在 `mycoder` 环境。
- 操作步骤：
  1. 执行 `conda run --no-capture-output -n mycoder python -V`。
  2. 执行 `node -v` 和 `npm -v`。
- 预期结果：Python、Node 和 npm 都能输出版本，Python 为 3.12 系列。
- 实际结果：Python 3.12.13、Node.js 24.15.0、npm 11.12.1。
- 结论：通过。

## 用例 2：验证真实进程握手

- 前置条件：依赖已安装。
- 操作步骤：
  1. 执行 `npm run dev`。
  2. 等待窗口打开。
  3. 观察右上角 Python Bridge 状态。
- 预期结果：状态从启动中变成“FakeLLM / Bridge 在线”，窗口没有白屏或控制台错误。
- 实际结果：Bridge 正常上线，界面显示空状态和可用输入框。
- 结论：通过。

## 用例 3：验证关闭与回收

- 前置条件：用例 2 的窗口仍在运行。
- 操作步骤：
  1. 关闭 Electron 窗口。
  2. 在任务管理器或 PowerShell 进程列表中搜索本项目的 `jixue.bridge`。
- 预期结果：Electron 和对应 Python Bridge 均退出，没有项目进程残留。
- 实际结果：真实 Electron 自动化测试退出码为 0，子进程随应用关闭；主进程 stderr 未出现 JavaScript Error 或已销毁对象错误。
- 结论：通过。
- 截图位置：`artifacts/ui/electron-smoke.png`，该目录已忽略，不提交仓库。

## 异常用例

- 向 Bridge 发送非 JSON 行：返回协议级 `error`，服务继续读取下一行。
- 信封版本不是 `1`：返回清晰错误，不终止进程。
- 单行超过 1 MiB：拒绝该命令，防止无界内存输入。

## 本章常见坑

| 现象 | 常见原因 | 排查方法 | 修复方式 |
| --- | --- | --- | --- |
| Bridge 一直离线 | `mycoder` 不存在或 Conda 不在 PATH | 在同一终端运行 Conda 版本命令 | 修复 Conda 环境或 PATH |
| stdout JSON 解析失败 | Python 日志写入 stdout | 单独启动 Bridge 检查每一行 | 日志改写 stderr |
| Electron 安装不正确 | postinstall 下载未完成 | 检查 `node_modules/electron/dist/electron.exe` | 重新执行 Electron 安装脚本 |
| 退出时弹 JavaScript 错误 | Bridge 退出事件向已销毁窗口发送状态 | 搜索主进程 stderr 中的 `Object has been destroyed` | 发送前检查窗口状态，并保护 stdin 关闭竞态 |
| PowerShell 脚本乱码 | Windows PowerShell 5 误判 UTF-8 | 单独解析 `.ps1` | 脚本保持 ASCII 或使用合适编码 |

## 回归结论

- 可以进入下一章：是。
- 未解决问题：真实 DeepSeek、配置加载和对话管理属于第一章，不在本章提前实现。
