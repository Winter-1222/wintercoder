---
name: inspect-python
description: 用 AST 静态统计 Python 文件或目录中的函数、类和导入，生成带范围限制的源码概览；当用户要求统计 Python 结构、定位入口候选或了解模块规模时使用。
---

# Python 源码概览

结合确定性脚本和必要源码阅读，回答用户指定范围内的结构问题。

1. 确定目标项目相对路径；霁雪项目未指定范围时使用 src/jixue。先阅读 [报告口径](references/report-guide.md)，明确统计范围和推断边界。
2. 在 Do 模式且具备 bash 工具时，从项目根目录执行下面的命令，把 --path 替换成目标路径并保留 Shell 引号：
   `conda run --no-capture-output -n mycoder python "skills/inspect-python/scripts/inspect_python.py" --path "src/jixue"`
   路径包含引号或 Shell 特殊字符时按当前 Shell 正确转义，不能直接拼接用户原话。
3. Plan 模式、后台只读子任务或无命令工具时，只读取资料与相关源码，说明尚未运行统计。等待用户切换到可执行模式；不要换一个工具绕过限制。
4. 先检查脚本退出状态和 JSON 的 errors、truncated，再按参考资料解释统计结果。需要解释某个函数或实际调用关系时，再用 read_file 读取对应源码。
5. 回答包含：扫描范围与完整性、主要统计、关键文件依据、无法从静态统计确定的事项。只展示与用户问题相关的明细。

脚本只解析源码，不导入目标模块，不执行目标代码，不写文件，无第三方依赖。正常使用无需先把脚本源码读进上下文；需要排查脚本或调整统计口径时再读取它。引用路径均相对本 SKILL.md 所在目录。
