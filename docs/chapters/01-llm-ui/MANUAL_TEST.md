# 第 1 章手动测试

> 本记录只验收 A 步 FakeLLM 流式链路；真实 DeepSeek 与多轮历史尚未验收。

## 测试环境

- 日期：2026-08-30
- 操作系统：Windows
- Conda 环境：`mycoder`
- Python 版本：3.12.13
- Node/Electron 版本：Node.js 24.15.0 / Electron 44.0.0
- 模型：`fake-jixue`

## 准备

1. 完成第 0 章依赖安装。
2. 执行 `npm run dev`。
3. 等待右上角显示“FakeLLM / Bridge 在线”。

## 用例 1：流式文本与完成后 Markdown

- 前置条件：Bridge 在线，输入框可用。
- 操作步骤：
  1. 输入“请验证第一条流式消息”。
  2. 点击“发送”。
  3. 在输出过程中观察标题前的 `##` 和加粗标记。
  4. 等回复完成后再次观察同一消息。
- 预期结果：生成中显示原始 Markdown 文本和光标；完成后 `##` 变成标题、列表分行、加粗和行内代码正确渲染。
- 实际结果：文本按不规则碎片持续出现，结束后一次性变成 Markdown，没有闪烁或重复文本。
- 结论：通过。

## 用例 2：状态栏与再次发送

- 前置条件：用例 1 已完成。
- 操作步骤：
  1. 观察 MODEL、INPUT、OUTPUT 和 ELAPSED。
  2. 再发送一条不同消息。
- 预期结果：模型显示 `fake-jixue`；Token 大于零；耗时在生成时变化、完成后停止；第二次发送正常开始。
- 实际结果：四项状态均更新，第二个请求在首个请求结束后可发送。
- 结论：通过。
- 注意：这只证明 UI 支持连续多次请求，不代表完整对话历史已经发送给模型。

## 用例 3：真实 Electron 自动化回归

- 前置条件：Electron 二进制完整，端口没有残留开发服务器。
- 操作步骤：
  1. 执行 `npm run test:electron`。
  2. 等待测试自动构建、打开窗口、发送消息并关闭。
- 预期结果：命令退出码为 0；能找到“霁雪已经醒来”“Python Bridge 正常”和 `fake-jixue`；Renderer 无控制台错误。
- 实际结果：2026-08-30 执行通过，截图写入 `artifacts/ui/electron-smoke.png`。
- 结论：通过。

## 用例 4：统一自动化回归

- 操作步骤：
  1. 执行 `npm run test:all`。
  2. 执行 `npm run typecheck`。
  3. 执行 `conda run --no-capture-output -n mycoder ruff check .`。
  4. 执行 `conda run --no-capture-output -n mycoder mypy src`。
- 预期结果：所有命令退出码为 0。
- 实际结果：Python 11 项、前端 2 项、Ruff、Mypy 和 TypeScript 全部通过。
- 结论：通过。

## 异常用例

- 空白消息：发送按钮保持禁用，不创建空消息。
- Bridge 未就绪：输入框禁用并显示等待提示。
- 非法 Bridge 命令：收到 `error` 事件，服务器继续运行。
- 窗口关闭：Electron 自动回收 Python Bridge。

真实 API 断网、缺失 Key、限流和半截 assistant 消息要在 B/C 步实现后补测，本记录不写成已通过。

## 本章常见坑

| 现象 | 常见原因 | 排查方法 | 修复方式 |
| --- | --- | --- | --- |
| 回复直到最后才出现 | stdout 被缓冲 | 检查 Python 是否带 `-u` | 使用无缓冲模式并逐事件 flush |
| Markdown 生成时抖动 | 每个 delta 都重新解析 | 查看消息状态与渲染分支 | 流中用纯文本，完成后再解析 |
| 错误后输入框一直禁用 | 长期监听器读取旧 React 状态 | 检查闭包和 request_id | 派发自包含事件，由 reducer 校验 |
| Python 测试无法启动 Electron | Python Playwright 不提供 Electron 接口 | 检查 Playwright 对象能力 | Electron 用 Node `playwright-core` 测试 |
| 模型名或 Token 不更新 | usage/complete 事件未转发 | 检查 Bridge 信封序号 | 修复事件映射，不从文本猜状态 |

## 回归结论

- 可以进入下一章：否；第一章尚未完成。
- 可以进入第一章下一小步：是。
- 未解决问题：配置加载、Anthropic SDK 适配器、真实 DeepSeek、ConversationManager、三模型选择和十轮对话回归。
