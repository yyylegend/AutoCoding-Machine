# Harness 第二调用者验证

状态：已完成（2026-09-24）。这是近期执行计划；阶段与长期方向见[模块化 Harness 路线图](2026-09-22-modular-harness.md)，当前行为以[架构说明](../architecture.md)为准。

## 目标

让一个独立于现有交互式 CLI 的 Python 程序，用同一个公共入口完成只读 Review 任务。它应复用会话、模型与工具循环，而不自行组装 `MachineLoop`、`AgentRun` 或权限管理器。此次验证不新增 Harness 接口。

## 实施结果

- `examples/harness_review.py` 接收工作区和问题，通过 `auto_coding_machine` 的统一导入入口使用内置 `review` Profile、`ProfileTools`、`ModelAdapter` 和 `open_harness_session()`；意外请求权限时停止，不自动批准。命令见[Harness 快速入门](../harness-quickstart.md)。
- 核心组合和可选记忆契约分别通过 `auto_coding_machine` 与 `auto_coding_machine.memory` 导入；实现统一位于 `src/auto_coding_machine/`。
- `auto-coding-machine` 可作为本地 editable 依赖加入另一个 uv 项目；调用方不设置 `PYTHONPATH` 也能导入包，并可从自己的工作目录或环境文件读取模型配置。
- 固定模型测试实际调用 `read_file`，检查模型工具表不含写文件或 Shell 能力，并验证最终结果和 JSONL 消息。
- 示例与 CLI 都调用 `open_harness_session()`；更新后的外部调用脚本对 DroidForge 实际 Review 返回成功。CLI 另有流式输出、终端 Hooks 和交互命令；执行循环没有重复接线需要收拢，因此本次未改 CLI。

## 完成标准与后续

示例只调用现有公共入口。`uv run pytest -q`：434 passed（2026-09-24）；editable wheel 构建、外部目录导入和 DroidForge 真实模型调用均通过。Coding、Review、Companion 的现有能力和权限不变。PyPI 发布仍不在范围内。

后续根据实际调用反馈调整具体接口。腾讯 L1 继续作为显式启用的实验能力。原始 JSONL 仍负责会话与恢复；其他存储的跨会话检索、公开事件插件入口和插件安装不属于本计划。Phase 6 仍以第二个真实外部 Adapter 的需求为前提。
