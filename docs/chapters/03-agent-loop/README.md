# 第 3 章：Agent Loop

## 当前成果

Agent 可以反复请求模型、执行工具并写回结果，直到模型完整结束、用户停止或遇到限制。区分完成、未完成、失败和停止；停止后保留已执行事实以便续接。保留 50 轮上限、连续异常工具保护、安全工具分批并发、Plan/Do、权限确认和用户停止。

`src/jixue/agent.py` 现在只负责组装组件、对外入口和任务生命周期；具体执行位于 `agent_runtime/`。公共导入仍是 `from jixue.agent import Agent, AgentMode`，Bridge 不需要知道内部拆分。

## 核心文件与阅读顺序

```text
src/jixue/
├─ agent.py                     先读：组件如何组装，对外暴露哪些操作
├─ agent_runtime/
│  ├─ loop.py                   再读：一个任务如何从模型走到工具再回到模型
│  ├─ model.py                  模型流、单次响应及流事件转换
│  ├─ execution.py              工具校验、权限、分批执行和结果落盘
│  ├─ compaction.py             请求整理、手动/自动摘要与失败暂停
│  ├─ control.py                模式、运行状态、取消与权限回复
│  └─ events.py                 事件类型和统一事件构造
├─ domain/conversation.py       唯一工作消息序列
├─ context.py                   三层上下文保护策略
└─ tools/registry.py            工具注册和统一调用入口
```

组装入口直接创建具体组件，不使用多继承，也不把整个 Agent 传给各模块。`ConversationManager` 由主循环与压缩器共同使用；`RunControl` 提供同一个停止信号；`ModelStream` 供普通回复和摘要共用。

```python
self._control = RunControl()
self._model = ModelStream(llm, self._control, system_prompt)
self._executor = ToolExecutor(tools, tool_context, self._control)
self._compactor = ContextCompactor(conversation, self._model, self._control)
self._loop = AgentLoop(...)  # 显式传入上面组装的组件
```

这段是省略参数后的阅读示意，完整组装代码见 `agent.py`。

## 完整链路

```text
用户输入 → chat.send → Agent.run
  ├─ /compact → ContextCompactor.run_manual
  └─ 普通任务 → AgentLoop.run
       → 会话加入 user
       → ContextCompactor.request_messages 清理旧工具正文并附动态提醒
       → 超过预算时运行摘要事务
       → ModelStream.respond：模型流转成 stream_text / tool_use / usage
       → turn_complete
            ├─ end_turn 且有有效答复：保存最终回复 → loop_complete
            ├─ 截断或缺失结束事件：标为未完成，工具不执行 → loop_complete
            └─ tool_use 且有调用：ToolExecutor.execute
                 → 校验参数和权限，必要时等待用户确认
                 → 安全工具分批执行 → 大结果落盘 → tool_result
                 → 会话成对保存 tool_use 与 tool_result
                 → 用户已停止则收尾，否则进入下一轮
```

一轮指一次模型请求，一个用户任务可以包含很多轮。`ModelResponse` 只收集本次请求的块、工具调用和用量；`ToolRound` 只收集本轮工具结果。它们都是临时执行结果，完成后写入唯一会话，不另存一套历史。

`AgentLoop._respond()` 负责请求预算和恢复规则：每个用户任务最多预算触发一次摘要；供应商返回 `prompt_too_long` 且尚未产生任何流事件时，最多摘要后重试一次。`RequestAttempts` 的生命周期跨越整个工具循环，防止每轮重新获得重试额度。

Plan 有两道限制：循环只导出只读工具定义，执行器仍会在真正执行前再次检查只读属性。Do 则按当前权限模式判断。MCP 工具和本地工具走同一个执行器。

`partition_tool_calls()` 将连续安全工具放在一个并发批次；遇到不安全工具时结束前一批，让该工具单独执行。结果按模型请求顺序写回，而不是按完成先后排序。连续 3 次请求不存在或禁用的工具会提前终止。

停止按钮通过 `Agent.cancel()` 到达 `RunControl`。模型流、工具等待和权限确认监听同一个信号。取消只停止本轮后续执行，不删除原问题，也不撤销文件修改。工具结果与调用仍按原顺序成对写回：

| 停止时的工具状态 | 保存给模型和界面的内容 |
| --- | --- |
| 已完成并拿到结果 | 保留原结果，包括同一批次中已经完成的调用 |
| 已启动但未拿到结果 | 标为“结果未知”，提示先检查文件或外部实际状态 |
| 尚未启动，包括正在等权限 | 标为“工具未执行”，不因下一条消息自动重放 |

最后的部分回复标为 `cancelled`；`max_tokens`、缺失完整结束事件等标为 `incomplete`；模型异常或异常工具保护标为 `failed`。只有有效答复以 `end_turn` 结束时才记为 `complete`。截断响应即使已经出现工具块，也只登记未执行结果。`loop_complete` 表示本轮结束并解锁界面，不保证任务成功；其中 `incomplete` 明确表示尚未完成。

### 中断后如何继续

1. 等待“已停止”或“未完成”，在同一个会话发送“继续”，也可以补充修改后的要求。
2. Bridge 收到新的 `chat.send`，`Agent.run()` 为本轮新建取消信号和循环额度，沿用原 `ConversationManager`。
3. 工作消息追加新输入；请求模型时会带上原问题、已执行结果、部分回复及客户端生成的中断说明。
4. 模型据此决定下一步，新的工具调用仍经过原有权限链。续接会发起新请求，不会恢复上一条 Python `await` 或自动重新执行旧工具。

`to_api_format()` 只过滤仍在生成的草稿；已停止、失败和未完成的文字附上状态说明。已经随工具轮保存的过程文字不重复追加。累计轮号统计已结束的用户轮，包括停止和失败；摘要按这些结束边界保留最近两轮。

正常停止后，Bridge 将会话快照写入 JSONL，再向界面发送收尾事件。因此正常停止后重启，可打开原会话继续。**进程被强制结束或断电仍只能恢复最后一次落盘快照**：主会话目前按任务开始/结束落盘，本次未扩展为工具轮级持久化。已经启动的线程或远程调用也不保证立即取消；对“结果未知”先核对实际状态是提供给模型的指令，并非程序级自动去重保证。

### 与 Claude Code 对照

Claude Code 支持按 `Esc` 停止并等待下一条指令，也支持在当前动作结束后读取用户补充的要求；霁雪目前需要先停止、等输入框解锁后再发送。[官方中断说明](https://code.claude.com/docs/en/how-claude-code-works#interrupt-and-steer)

Claude Code 持续保存会话；`claude --continue` 恢复当前目录最近一次会话，`claude --resume` 选择会话。官方说明，进程结束时仍在运行的工具不会因恢复会话而自动完成或重新运行。续接恢复的是会话信息；文件回滚由独立的 checkpoint/rewind 能力负责。[会话恢复](https://code.claude.com/docs/en/sessions#what-a-resumed-session-restores)、[文件检查点](https://code.claude.com/docs/en/checkpointing)

Claude API 将 `max_tokens` 与正常的 `end_turn` 分开报告。它是达到输出上限，并不保证任务完成；霁雪这次据此标为未完成，等待用户决定是否继续。这里不推断 Claude Code 未公开的逐 token 内部实现。[官方停止原因](https://platform.claude.com/docs/en/build-with-claude/handling-stop-reasons)

## 启动与手动测试

在项目根目录运行：

```powershell
conda activate mycoder
npm run dev
```

按下面标注的模型模式依次测试：

1. 发送“你好”：正文流式出现，最后恢复输入框。
2. 发送 `/loop README.md docs/PROJECT_STRUCTURE.md`：模型请求两次读取，经历三轮模型请求，最终有两张结束的工具卡片。
3. 在确认写入模式发送 `/write tests/manual/agent-split.txt 拆分测试`：拒绝时文件不写入；重新发送并允许后正常写入。
4. 接入真实模型，让它先读取文件、再详细解释。看到读取工具完成后点停止；等界面显示“已停止”后，在同一会话发送“继续解释”。确认它能利用已有读取结果，并按新要求接着工作。
5. 在 Fake 模型下发送 `/write tests/manual/interruption.txt 停止测试`，在权限确认处点停止：工具显示未执行，目标文件不应出现；之后正常发送新消息。
6. 输出截断、并发部分完成和保存后恢复由本地确定性测试验证，无需刻意消耗真实模型额度。
7. 完成至少 5 个内容充实的对话后发送 `/compact`：能压缩则显示完成提示；短对话应提示不足或摘要不够短，不能破坏消息。

```powershell
conda run --no-capture-output -n mycoder python -m pytest tests/bridge/test_interruption.py
conda run --no-capture-output -n mycoder python -m pytest
conda run --no-capture-output -n mycoder ruff check src
conda run --no-capture-output -n mycoder mypy src
npm run test:frontend
npm run typecheck
```

测试文件只保留在本机并由 Git 忽略。`tests/bridge/test_interruption.py` 覆盖串行/并发部分完成、结果刚显示时停止、权限等待、正常停止后的存盘恢复、截断工具不执行和旧快照兼容；其余测试覆盖工具循环、权限、压缩和用量。前端测试检查“未完成”“已停止”和“结果未知”状态。

## 常见问题

**新增工具要改 Agent 吗？** 工具按现有合同注册到 `ToolRegistry` 即可，执行器不按具体工具名增加分支。

**模块之间会互相调用整个 Agent 吗？** 不会。运行组件不导入 `jixue.agent`；入口向组件传入它实际需要的对象，依赖方向从组装入口指向执行组件。

**`.jixue/tool-results` 还能删吗？** 目前仍需要。超过 50,000 字符的完整结果存放在这里，模型通过 `read_artifact` 凭编号取回。删掉已有文件会让相应编号失效，本次保留，且目录继续被 Git 忽略。

**组件拆开后，工具消息会分散保存吗？** 不会。所有完成的文字和工具轮仍写进同一个 `ConversationManager`。

## 变更记录

- 完善中断与完成状态：保留已执行事实、补齐未执行/结果未知的工具结果，明确标记截断，支持同会话继续与正常停止后的快照恢复。
- 建立最小 Agent Loop、轮次上限、取消、异常工具保护、并发与 Plan/Do。
- 接入统一消息序列和三层上下文保护。
- 本次按职责拆分运行组件，`agent.py` 保留显式组装及公共入口；原有 Bridge 事件协议保持一致。

## 自测题与答案

1. **从哪里看整体结构？** 先看 `agent.py` 的构造函数，再看 `AgentLoop.run()`。
2. **一次模型回复带有工具调用，会立即执行吗？** 只有模型明确以 `tool_use` 完整结束才执行；截断、取消和异常都补上未执行结果。
3. **为什么工具事件已显示，还要成对写回会话？** UI 事件用于展示；下一次模型请求需要有同 ID 配对的调用与结果。
4. **谁决定是否自动压缩？** 主循环检查预算和尝试额度，压缩器负责实际生成、校验和提交摘要。
5. **为什么多个模块共享 RunControl？** 停止操作必须同时唤醒模型流、工具执行等待和权限确认。
6. **`turn_complete` 和 `loop_complete` 有什么区别？** 前者结束一次模型请求，后者结束本轮并让 UI 解锁；需要结合 `cancelled`、`incomplete`、`is_error` 判断结果。
7. **停止后发送“继续”会重跑上个工具吗？** 程序不重放旧工具；新的模型请求读取历史后决定下一步，结果未知的操作需先核对。
8. **停止会撤销文件修改吗？** 不会；已经完成的操作和结果会保留，回滚是另一种动作。
