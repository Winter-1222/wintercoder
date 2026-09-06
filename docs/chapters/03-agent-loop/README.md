# 第 3 章：Agent Loop

## 当前成果

Agent 可以反复请求模型、执行工具并写回结果，直到模型给出最终回答。保留 50 轮上限、连续异常工具保护、安全工具分批并发、Plan/Do、权限确认和用户停止。

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
            ├─ 没有工具：保存最终回复 → loop_complete
            └─ 有工具：ToolExecutor.execute
                 → 校验参数和权限，必要时等待用户确认
                 → 安全工具分批执行 → 大结果落盘 → tool_result
                 → 会话成对保存 tool_use 与 tool_result → 下一轮
```

一轮指一次模型请求，一个用户任务可以包含很多轮。`ModelResponse` 只收集本次请求的块、工具调用和用量；`ToolRound` 只收集本轮工具结果。它们都是临时执行结果，完成后写入唯一会话，不另存一套历史。

`AgentLoop._respond()` 负责请求预算和恢复规则：每个用户任务最多预算触发一次摘要；供应商返回 `prompt_too_long` 且尚未产生任何流事件时，最多摘要后重试一次。`RequestAttempts` 的生命周期跨越整个工具循环，防止每轮重新获得重试额度。

Plan 有两道限制：循环只导出只读工具定义，执行器仍会在真正执行前再次检查只读属性。Do 则按当前权限模式判断。MCP 工具和本地工具走同一个执行器。

`partition_tool_calls()` 将连续安全工具放在一个并发批次；遇到不安全工具时结束前一批，让该工具单独执行。结果按模型请求顺序写回，而不是按完成先后排序。连续 3 次请求不存在或禁用的工具会提前终止。

停止按钮通过 `Agent.cancel()` 到达 `RunControl`。模型流、工具等待和权限确认都监听同一个信号，停止后旧工具卡片得到收尾事件，当前任务消息按取消语义排除，下一个任务仍可继续。

## 启动与手动测试

在项目根目录运行：

```powershell
conda activate mycoder
npm run dev
```

使用 Fake 模型依次测试：

1. 发送“你好”：正文流式出现，最后恢复输入框。
2. 发送 `/loop README.md docs/PROJECT_STRUCTURE.md`：模型请求两次读取，经历三轮模型请求，最终有两张结束的工具卡片。
3. 在确认写入模式发送 `/write tests/manual/agent-split.txt 拆分测试`：拒绝时文件不写入；重新发送并允许后正常写入。
4. 在模型或工具等待期间点击停止，再发“继续”：前一任务结束，新任务正常回复。真实模型更容易观察较长等待；自动化测试可确定性验证这个过程。
5. 完成至少 5 个内容充实的对话后发送 `/compact`：能压缩则显示完成提示；短对话应提示不足或摘要不够短，不能破坏消息。

```powershell
conda run --no-capture-output -n mycoder python -m pytest
conda run --no-capture-output -n mycoder ruff check .
conda run --no-capture-output -n mycoder mypy src
npm run test:frontend
npm run typecheck
```

测试文件只保留在本机 `tests/`，由 Git 忽略。已有回归覆盖普通消息、工具顺序与并发、权限、取消、循环上限、三层压缩及超长恢复。

## 常见问题

**新增工具要改 Agent 吗？** 工具按现有合同注册到 `ToolRegistry` 即可，执行器不按具体工具名增加分支。

**模块之间会互相调用整个 Agent 吗？** 不会。运行组件不导入 `jixue.agent`；入口向组件传入它实际需要的对象，依赖方向从组装入口指向执行组件。

**`.jixue/tool-results` 还能删吗？** 目前仍需要。超过 50,000 字符的完整结果存放在这里，模型通过 `read_artifact` 凭编号取回。删掉已有文件会让相应编号失效，本次保留，且目录继续被 Git 忽略。

**组件拆开后，工具消息会分散保存吗？** 不会。所有完成的文字和工具轮仍写进同一个 `ConversationManager`。

## 变更记录

- 建立最小 Agent Loop、轮次上限、取消、异常工具保护、并发与 Plan/Do。
- 接入统一消息序列和三层上下文保护。
- 本次按职责拆分运行组件，`agent.py` 保留显式组装及公共入口；原有 Bridge 事件协议保持一致。

## 自测题与答案

1. **从哪里看整体结构？** 先看 `agent.py` 的构造函数，再看 `AgentLoop.run()`。
2. **一次模型回复带有文字和工具调用，会结束吗？** 不会。只要请求工具，就先执行并写回结果，再请求模型。
3. **为什么工具事件已显示，还要成对写回会话？** UI 事件用于展示；下一次模型请求需要有同 ID 配对的调用与结果。
4. **谁决定是否自动压缩？** 主循环检查预算和尝试额度，压缩器负责实际生成、校验和提交摘要。
5. **为什么多个模块共享 RunControl？** 停止操作必须同时唤醒模型流、工具执行等待和权限确认。
6. **`turn_complete` 和 `loop_complete` 有什么区别？** 前者结束一次模型请求，后者结束整个用户任务并让 UI 解锁。
