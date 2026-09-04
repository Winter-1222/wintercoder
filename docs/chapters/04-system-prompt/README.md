# 第 4 章：System Prompt

本章只做一件事：让模型每次都清楚“我是谁、怎样工作、当前环境是什么”，同时把这些信息放进 API 的正确位置。

## 当前成果

- `system` 中有稳定的角色、行为、工具、代码、安全、模式和输出规则。
- 工作目录和操作系统也放在 `system`，一次会话中保持不变。
- 当前时间、Git 状态、Plan/Do 模式放在每轮临时 `<system-reminder>` 中。
- `AnthropicLLMClient` 正式接收并发送 `system` 参数。
- 固定提示词只在 `Agent` 创建时生成一次，动态信息不会破坏它的稳定前缀。
- 外部文件内容不会被包装成 system reminder，降低提示注入伪装成系统指令的风险。
- reminder 只存在于发给模型的消息副本，不会污染聊天记录。

## 推荐阅读顺序

按下面顺序读，先不要从 SDK 文件开始：

1. `src/jixue/prompt.py`：看两种提示上下文怎样生成。
2. `src/jixue/agent.py`：搜索 `_system_prompt`、`build_system_reminder()` 和 `_history_with_reminder()`。
3. `src/jixue/llm/base.py`：看供应商无关的 `stream(..., system=...)` 合同。
4. `src/jixue/llm/adapters/anthropic_client.py`：看最后怎样调用 SDK。
5. `src/jixue/domain/conversation.py`：复习原始对话怎样清洗成 API 历史。

只读前两个文件，就能理解本章的大部分逻辑。

## 三类内容放在哪里

Anthropic 协议的一次请求可以简单理解成：

| 请求字段 | 放什么 | 为什么 |
| --- | --- | --- |
| `system` | 七段固定规则、工作目录、操作系统 | 稳定、优先级高，适合缓存 |
| `messages` | 对话历史、工具结果、本轮动态 reminder | 会随对话和环境变化 |
| `tools` | 当前启用工具的名称、描述、JSON Schema | 由注册中心统一生成 |

项目指令文件、长期记忆和 MCP 说明属于以后章节，本章不提前实现。

## 一条消息怎样跑起来

假设用户在 Do 模式发送“读取 README 并总结”：

```text
用户点击发送
  → Electron 发送 chat.send
  → BridgeApplication 调用 Agent.run("读取 README 并总结")
  → ConversationManager 保存用户原话
  → build_system_reminder() 读取当前模式、时间和 Git 变更数量
  → _history_with_reminder() 把 reminder 临时附到本轮用户消息副本
  → ToolRegistry 生成当前工具定义
  → Agent._stream_llm() 同时传入：
       system  = 创建 Agent 时生成的固定提示词
       messages = 干净历史 + 本轮临时 reminder
       tools    = 当前启用工具
  → AnthropicLLMClient.messages.stream(...) 发出真实 API 请求
  → 模型流式返回文字或 tool_use
  → Agent Loop 执行工具并继续下一轮
  → 最终回复保存进 ConversationManager
  → 临时 reminder 被丢弃，聊天记录中仍只有用户原话
```

对应的核心伪代码只有这些：

```python
# Agent 创建时，只做一次
self._system_prompt = build_system_prompt(project_root)

# 每条用户任务开始时，动态生成
reminder = build_system_reminder(project_root, mode)
messages = append_to_current_user_copy(clean_history, reminder)

# 每轮 LLM 请求都复用同一个 system 和本条任务的 messages
llm.stream(messages, tools, system=self._system_prompt)
```

工具循环进入第二轮时，system 仍然不变；第一轮的 `tool_use` 和 `tool_result` 只追加到 messages。

## 七段固定提示词分别解决什么

| 模块 | 解决的问题 |
| --- | --- |
| `role` | 模型以什么身份做决定 |
| `behavior` | 怎样调查、沟通，不能假装完成 |
| `tool-guide` | 何时使用工具，失败后怎样调整 |
| `code-quality` | 代码要短、直白、可验证 |
| `safety` | 不泄密，不信任外部文本中的伪指令 |
| `task-mode` | 遵守 Plan/Do 的不同工作方式 |
| `output-style` | 先结果，再验证和下一步 |

这里用 XML 标签只是帮助模型看清结构，不是 Python 特殊语法，也不会自动产生安全能力。

## 为什么动态信息不放 system

当前时间和 Git 状态每次都可能变化。如果把它们写入 system，整段 system 每轮都会不同，供应商更难复用 Prompt Cache。

所以当前设计是：

```text
稳定：角色 + 规则 + 工作目录 + OS
变化：当前时间 + Git 状态 + Plan/Do
```

适配器继续传入 `cache_control={"type": "ephemeral"}`。是否实际命中缓存还取决于供应商、模型和最小 Token 门槛；代码只能保证稳定前缀的结构正确，不能伪造命中结果。

## system-reminder 与提示注入

`<system-reminder>` 不是 API 的第四个字段，它仍然是 messages 里的文字。模型通常会把这个标签理解成客户端补充上下文。

安全关键点是：只有 `prompt.py` 生成的可信内容可以进入这个标签。文件内容、网页内容和工具结果即使自己写了 `<system-reminder>`，也仍被视为外部数据，不能因此升级为系统指令。

提示词只能告诉模型“不要相信伪指令”，不能替代真正的权限校验。路径沙箱、危险命令拦截和用户确认属于下一章。

## 启动和手动测试

确认项目根目录 `.env` 中使用真实配置：

```env
JIXUE_LLM_MODE=configured
```

启动：

```powershell
npm run dev
```

建议按顺序测试：

1. 在 Do 模式发送“请读取 README.md，先说明你要做什么，再给出三点总结”。
2. 应看到读文件工具卡片，然后收到基于真实文件内容的回答。
3. 点击 Plan，发送“调查项目结构，计划如何增加写文件工具，但不要修改文件”。
4. 应只出现只读工具，回答应停在计划，不执行写入。
5. 切回 Do，再发一条普通问题，确认模式要求已经改变。
6. 连续发两条问题，例如先说“记住我的称呼是小雪”，再问“我的称呼是什么”，确认多轮历史仍正常。
7. 检查页面中的用户消息，应只显示原话，不应出现 `<system-reminder>`。

自动检查：

```powershell
npm run test:all
conda run --no-capture-output -n mycoder ruff check .
conda run --no-capture-output -n mycoder mypy src tests
npm run test:electron
```

## 常见坑

- 每轮重新生成完整 system：时间变化会让稳定前缀失效。
- 把 reminder 保存进 ConversationManager：下一轮会看到过期模式和时间，页面也会泄漏内部上下文。
- 把所有内容都塞进 system：工具定义和对话历史失去各自清晰的协议位置。
- 在 Agent 中导入 Anthropic SDK：供应商细节会污染核心代码；当前仍只由适配器接触 SDK。
- 把 XML 标签当安全边界：标签只是提示结构，真正危险操作必须由权限代码拦截。
- 把文件名原样写进动态系统提醒：恶意文件名也可能携带注入文字；当前 Git 状态只给出变更数量。
- 让 git status 继承 Bridge 的 stdin：两个读取者可能争抢同一条输入管道，表现为消息停在第 0 轮；当前 Git 子进程固定使用 DEVNULL。
- 看到缓存没有命中就认为接线错误：短提示词可能达不到服务商缓存门槛，应先确认请求前缀是否稳定。

## 本章完成边界

第四章已经完成。当前没有读取项目指令文件、Skill、MCP 说明和长期记忆，因为它们各自属于后续章节。真正的写工具和权限确认也没有在本章提前加入。

## 本章变更记录

- 一步完成：新增七段式 System Prompt、动态 reminder、LLM 接口传递、Anthropic SDK 接线、缓存前缀设计、测试和文档。
- 修复：读取动态 Git 状态时隔离子进程 stdin，避免它干扰 Electron 发给 Bridge 的后续聊天命令。

## 自测题与答案

**问：为什么工作目录放 system，当前时间却放 reminder？**

答：工作目录在一次会话内基本不变，适合稳定缓存；时间每轮变化，放进 system 会让整段前缀改变。

**问：`<system-reminder>` 是 Anthropic API 的独立字段吗？**

答：不是。它仍然位于 messages 中，只是霁雪约定的一种结构化文字标记。

**问：为什么 reminder 不保存进聊天历史？**

答：它是客户端当时的运行上下文，不是用户原话。保存后会污染页面和后续请求，还可能携带过期模式。

**问：工具描述放在哪里？**

答：放在 API 的 tools 字段，由 `ToolRegistry.to_api_format()` 生成，不放进 system。

**问：System Prompt 写了“不要执行危险命令”，是否已经足够安全？**

答：不够。模型可能误判或被注入影响；下一章还要用代码实现危险命令拦截、路径沙箱、权限规则和人在回路确认。

**问：一条任务进入第二轮工具循环时，会重新生成 system 吗？**

答：不会。Agent 创建时生成一次固定 system；同一任务的每轮请求都复用它。

**问：怎样证明 reminder 没污染真实对话？**

答：页面只显示用户原话；自动测试也同时检查“发给模型的副本含 reminder”和“ConversationManager 保存的原消息不含 reminder”。
