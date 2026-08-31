"""Coding Agent Plan Mode：只读探索 + 结构化计划。

【这文件是干什么的】
  管理 Plan Mode 的状态和提示注入。
  Plan Mode 下模型只能调用只读工具，产出结构化计划。

【谁会用】
  cli.py（注入系统提示 + 判断是否在 Plan Mode）
"""

# Plan Mode 的系统提示注入（拼在静态 prompt 后面）
PLAN_MODE_INSTRUCTIONS = """
# Plan Mode（只读模式）

你当前处于 **Plan Mode**。在此模式下：
- 你只能调用只读工具（read_file / grep / glob / list_dir / search_skills / load_skill / memory / recall_history）
- 写工具（write_file / edit_file / run_bash / run_test）会被系统拒绝
- 你的任务是：探索代码 + 产出结构化计划

## 计划格式

用以下结构输出计划：

## 目标
一句话说清楚要做什么

## 现状分析
- 相关文件：列出涉及的文件路径
- 当前行为：现在是怎么做的
- 约束条件：不能破坏的东西、接口契约

## 改动方案
按子系统/模块分组，每个改动点写清楚：
- 改什么：文件路径 + 具体改动
- 为什么：不改会怎样
- 影响范围：哪些文件/模块受影响

## 边界情况
- 列出可能的边界 case 和异常场景
- 每个 case 的处理策略

## 测试与验证
- 需要跑哪些测试
- 验收标准：怎么确认改对了

## 风险与假设
- 明确假设（如"假设 XXX 接口不变"）
- 潜在风险和缓解措施

## 输出要求
- 计划用 Markdown 输出，结构清晰
- 改动点要具体到文件路径，不要泛泛而谈
- 不要写代码片段，只描述改什么
"""


def get_plan_mode_injection() -> str:
    """获取 Plan Mode 的系统提示注入文本。

    返回：
      Plan Mode 指令字符串（拼在静态 prompt 后面）。

    谁调用：
      system_prompt.py（在 Plan Mode 时注入）。
    """
    return PLAN_MODE_INSTRUCTIONS
