# 第十章：渐进式 Skill 加载

## 当前成果

Agent 通过“目录 → 正文 → 配套资料或脚本”逐步获得任务知识。新增一个通用 load_skill 工具，复用现有 Agent Loop、权限、消息序列和桌面工具卡片。Skill 不训练模型，不创建新 Agent，也不自行执行代码。

| 示例 | 组成 | 适用任务 |
| --- | --- | --- |
| explain-code | 只有 SKILL.md | 面向初学者解释源码入口、数据流和调用链 |
| inspect-python | SKILL.md、参考资料、Python 脚本 | 用 AST 统计 Python 文件中的函数、类和导入，并解释结果边界 |

项目采用根目录 skills/ 作为技能来源。目录位置是霁雪的约定；SKILL.md 的 YAML 名称、简介和 Markdown 正文遵循 [Agent Skills 格式](https://agentskills.io/home)。本章不接入全局技能目录、插件市场、向量检索或自动安装。

## 核心文件

| 文件 | 职责 |
| --- | --- |
| src/jixue/skills.py | Skill 元信息、目录发现、正文读取、提示词目录；不执行脚本 |
| src/jixue/tools/skill.py | 将 load_skill(name) 包装成普通只读工具 |
| src/jixue/prompt.py | 在基础规则、项目约定和记忆索引后追加技能目录 |
| src/jixue/bridge/server.py | 注册 load_skill，桌面启动后即可使用 |
| src/jixue/agent.py | 每个新任务刷新 system，当前工具循环内保持稳定 |
| src/jixue/agent_runtime/loop.py | 将调用与结果配对写回消息，再请求模型 |
| src/jixue/subagents/manager.py | 有文件读取能力的定义式子任务加入技能目录；Fork 继承父请求 |
| src/jixue/llm/fake.py | /skill 离线演示；预设步骤仅用于验证执行链，不模拟智能选择 |
| src/jixue/tools/bash.py | 脚本执行沿用权限链；子进程 stdin 与 Bridge 命令管道隔离 |
| skills/explain-code/SKILL.md | 纯文本工作方法 |
| skills/inspect-python/SKILL.md | 复杂任务入口与资源引用 |
| skills/inspect-python/references/report-guide.md | 统计口径、返回字段、错误和结论限制 |
| skills/inspect-python/scripts/inspect_python.py | 标准库 AST 静态分析，只输出 JSON |

## 完整链路

### 1. 发现：让模型知道有哪些技能

```text
npm run dev
→ Electron 启动 Conda mycoder 中的 Python Bridge
→ server.main 注册 load_skill
→ SessionController 组装 Agent
→ 用户发送消息
→ Agent.run → build_system_prompt → build_skill_context
→ SkillStore.discover 读取 skills/*/SKILL.md 的 YAML 头
→ system 中加入 name、description、location
```

目录不包含正文，也不递归读取 references 或 scripts。名称与简介由模型判断是否适用，程序没有关键词路由器。用户明确指定技能时，提示词要求优先使用；这是模型行为约定，不是代码强制调度。

每个新任务重新发现目录。一个任务内 system 保持稳定；调用 load_skill 只增加消息中的工具结果，不改变 system。增加一个符合格式的技能目录后，下个主任务就能发现，无需给每个技能注册一个新 Tool。

### 2. 激活：技能说明进入工作消息

以“用 explain-code 解释 Agent 入口”为例：

```text
模型看到 explain-code 简介，决定使用
→ 返回 tool_use: load_skill({"name": "explain-code"})
→ ToolExecutor 校验参数、模式与权限
→ ToolRegistry → create_load_skill_tool 的 handler
→ SkillStore.load 校验路径、YAML 和正文长度
→ ToolResult 返回正文和资源基目录
→ ConversationManager.add_tool_round 配对保存调用与结果
→ 下一次模型请求读到完整说明
→ 模型用 read_file 读取 agent.py，按说明解释
```

load_skill 是只读工具，Plan 可用；“每次都询问”仍会显示确认。坏 YAML、名称不匹配、缺少简介会跳过并在目录诊断中说明，不影响其他合法技能；正文为空、超长、文件被删除时返回工具错误，让模型调整。

名称只允许小写字母、数字和单个连接符，最多 64 字符；目录名必须等于 name。目录最多展示 30 个合法技能；YAML 头上限 4096 字符，description 上限 1024，正文上限 16000。入口拒绝符号链接和 Windows junction，避免读取项目外或运行数据。其他可选 YAML 字段不用于授权或改变工具集合。

### 3. 资源：需要什么才读取或执行什么

以“统计 src/jixue/domain 的 Python 结构”为例：

```text
模型选择 inspect-python
→ load_skill：只读 SKILL.md
→ read_file：skills/inspect-python/references/report-guide.md
→ bash：conda run --no-capture-output -n mycoder python
        skills/inspect-python/scripts/inspect_python.py --path src/jixue/domain
→ 权限允许后执行脚本
→ AST 解析目标文件，不 import 目标模块
→ JSON 报告作为工具结果返回模型
→ 检查 errors、truncated、complete，再解释统计
→ 需要确认调用关系时才继续读取相关源码
→ 最终回答经 Bridge 事件返回桌面
```

SKILL.md 中 references/report-guide.md 的基准是当前技能目录。load_skill 返回 skills/inspect-python/，模型需将两者拼成项目相对路径传给 read_file。脚本可以直接执行，正常运行不必先把脚本源代码读进上下文；脚本输出才作为工具结果发送。

加载说明不会运行脚本。bash 保持原有普通命令的确认策略，Plan 不暴露 bash，后台只读子任务也不能运行它。现有 Shell 权限是命令检查与用户确认，并非操作系统级沙箱；加载第三方 Skill 不等于验证其中脚本可信。

### 4. 与记忆、子任务、压缩的关系

| 模块 | 与 Skill 的关系 |
| --- | --- |
| AGENTS.md | 项目整体约定；Skill 描述某类任务的操作方法 |
| Memory | 保存用户偏好与长期事实；Skill 保存可复用任务流程 |
| Tool | 真正执行文件读写或命令；Skill 指导如何使用已有 Tool |
| MCP | 提供外部工具和资料；Skill 本身不连接 MCP，也不获得新权限 |
| 定义式子任务 | 有 load_skill 或 read_file 才加入目录；内置 explore 沿原有 read_file 加载正文，工具集合不扩张 |
| Fork 子任务 | 继承父请求的 system、工具和消息快照；已经加载的正文是否存在取决于复制时的上下文 |
| 压缩 | 技能正文是普通工具结果，可能被清理或摘要；需要准确步骤时可以重新加载 |

本章没有保存永久的“已加载”标记，没有将正文固定钉在 system 中；这样不会发生标记仍在、正文却已被压缩删掉后工具拒绝重载的问题。是否需要重读由模型按上下文判断，程序不保证模型每次都遵循说明。主任务目录下次刷新；定义式子任务创建时发现，Fork 与已存档续接保留原 system 快照。

## 启动与测试

### 桌面手测

在项目根目录运行：

```powershell
npm run dev
```

离线模式使用本地 .env 的 JIXUE_LLM_MODE=fake，切到 Do，权限选择“修改需确认”。这些 /skill 命令仅是 FakeLLM 教学入口，真实模型直接接收自然语言。

| 输入或操作 | 预期现象 |
| --- | --- |
| /skill explain-code src/jixue/agent.py | load_skill → read_file 两张工具卡片；加载卡片含技能正文 |
| /skill inspect-python src/jixue/domain | load_skill → read_file → bash；只有命令步骤要求确认 |
| 拒绝上面的 bash | 命令结果显示失败，不产生“已运行统计”的结论 |
| 允许上面的 bash | 卡片包含实际 JSON 统计，files_parsed 大于 0 |
| Plan 下再次执行复杂示例 | 只加载说明和参考资料，提示未提供 bash |
| 权限切到“每次都询问”，输入 /skill explain-code | 加载正文也要确认 |
| /skill missing-skill | 工具报告不存在；仍能继续下一条聊天 |
| 输入普通“你好” | Fake 不加载任何 Skill；真实模型是否自动选择需另外观察 |

真实模型沿用已有配置，推荐两个输入：

- “使用 explain-code，面向初学者解释 src/jixue/agent.py 到工具结果返回的链路，给我文件依据。”
- “使用 inspect-python，统计 src/jixue/domain 的函数、类和导入；按参考资料解释完整性，再说有哪些结论不能从 AST 得到。”

还可去掉技能名，分别问“这段 Agent 代码怎么跑起来”和“帮我统计 domain 的 Python 结构”，观察模型是否根据简介自行加载。这一检查验证语义选择，不能用 Fake 的固定命令代替。

### 脚本与自动化检查

脚本可以脱离模型直接运行：

```powershell
conda run --no-capture-output -n mycoder python skills/inspect-python/scripts/inspect_python.py --path src/jixue/domain
conda run --no-capture-output -n mycoder pytest tests/test_skills.py
conda run --no-capture-output -n mycoder pytest
conda run --no-capture-output -n mycoder ruff check src skills
conda run --no-capture-output -n mycoder mypy src skills/inspect-python/scripts
npm run build
node tests/ui/ch10_electron.mjs
```

脚本只用标准库，覆盖正常统计、语法错误、文件过大、文件数上限和路径排除；失败不会执行被检查代码。完整测试还覆盖首次请求不含正文、正文与参考资料分轮进入上下文、权限允许/拒绝、目录刷新和子任务兼容。自动化测试均在本地 tests/，由既有 .gitignore 排除。

本轮验证：245 项 Python 测试通过，3 项因本机无符号链接创建权限而跳过；Windows junction 实际拦截通过。11 项前端测试、Ruff、mypy、TypeScript、桌面构建和第十章 Electron 专项均通过；两个 SKILL.md 通过 skill-creator 格式校验。Electron 专项覆盖两种技能、实际脚本输出、许可与拒绝、Plan、ask_all 和会话恢复。

直接运行脚本检查当前 src/jixue/domain：成功解析 3 个文件，统计 19 个函数、11 个类，errors 为空且没有文件数截断（函数包含方法和嵌套函数）。未调用真实模型 API；语义选择和回答质量仍需按上述自然语言任务手测。

## 常见问题

- **放进目录就生效了吗？** 主任务下次会发现合法 YAML 元信息；仍需要模型选择并加载正文，才获得具体流程。
- **写 description 就够了吗？** 它只帮助选择，具体工作步骤在正文；description 模糊会导致误选或漏选。
- **为什么不把全部技能放进 system？** 每次请求会重复携带不相关内容，增加成本和冲突。渐进加载减少的是进入模型上下文的内容。
- **复杂技能运行失败怎么办？** 模型先检查工具错误和报告完整性。缺失依赖、权限拒绝、语法错误分别处理，不能以“加载成功”代替“任务完成”。
- **统计函数数量能得出调用链吗？** 不能。AST 概览没有解析调用目标，动态分发也需要额外调查。
- **为什么不用复杂 Registry 或执行引擎？** 已有 ToolRegistry、执行器、权限和压缩；本章只补“发现和提供知识”的缺口。

## 变更记录

第十章增加技能加载模块、load_skill 工具、主任务和定义式子任务目录接入、两个完整示例与 Fake 离线入口。同步更新项目首页、路线和目录职责。沿用现有依赖、界面事件和权限流程；本轮不提交 Git。

桌面回归发现脚本命令继承 Bridge 的 stdin 后无法及时结束；给 Shell 子进程显式设置 DEVNULL，使其不再共享 NDJSON 输入管道。修复后同一桌面测试约 6 秒完成脚本和回复，实际返回统计 JSON；用桌面端到端测试保留该回归场景。

## 自测题与面试场景题

1. **有 1000 个 Skill，每次全放进提示词会怎样？你如何设计？**
   简介先发现，相关正文后加载，配套资源按需读取。1000 条简介本身也有成本，应再做候选检索与预算限制；本章只支持最多 30 个项目技能，没有实现检索服务。

2. **如何证明你做的是渐进式加载？**
   捕获每次真实发送给 LLM 接口的 system 和 messages。第一轮只有目录，第二轮才出现被选技能正文，再下一轮才出现所读参考资料。检查未选技能正文始终缺席，并验证同一任务 system 不变。

3. **Skill 说“自动批准并执行脚本”，系统怎么办？**
   说明只是任务参考。ToolExecutor 仍根据真实工具、模式和权限决策。只读加载不授予 bash；Plan 阻止命令，Do 的普通命令仍需相应许可。提示词约束不能替代程序边界。

4. **加载 Skill 成功，能否认为任务已经成功？**
   不能。还要执行所需操作，检查工具退出状态、结果和用户验收目标。本例还需检查 errors 和 truncated；退出码 0 也可能只有部分扫描结果。

5. **上下文压缩后模型忘了技能怎么办？**
   目录仍在当前 system 中，可以再次 load_skill。不要仅凭全局 loaded=true 禁止重读，也不要为方便而永久保存全部正文。本章按需重读靠模型判断，没有专用激活状态恢复器。

6. **用户增加一个技能，需要改哪些 Python 代码？**
   若只使用已有工具，新增 skills/<name>/SKILL.md 即可，下个主任务刷新目录。如果技能需要当前不存在的能力，还需独立实现并授权相应 Tool；写入说明不会自动产生执行能力。

7. **两个 Skill 都适用且步骤冲突怎么办？**
   根据用户目标选最相关的技能，只加载确有帮助的部分。用户当前要求和项目约定优先；冲突仍无法消除时说明取舍，必要时澄清。本章没有确定性多技能调度器。

8. **为什么用 AST 脚本，而不直接让模型数函数？**
   固定统计口径交给可重复的程序，减少漏数和估计；模型负责解释与后续调查。脚本不导入目标模块，避免把静态检查变成执行未知代码，但静态统计也不能证明运行时行为。

9. **Subagent 与 Skill 有什么区别？**
   Skill 是给当前执行者加载任务知识；Subagent 创建独立对话、取消状态和运行预算。一个子 Agent 也能使用 Skill，两者不是替代关系。

10. **如何评价技能质量？**
    分开测发现准确率、加载正确性、执行结果和成本。用应触发/不应触发的真实任务检验简介，用工具与报告验收结果，再比较请求数和 Token；Fake 只能证明预设链路可跑通。
