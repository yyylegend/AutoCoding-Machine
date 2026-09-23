# 运行时边界演进计划

状态：部分实施。`RuntimeComponents`、`AgentSession`、`AgentRun`、`RequestView` 和 typed `AgentEvent` 的第一切片已落地；本文其余目标仍是待实施设计。本计划作为[模块化 Harness 主线](2026-09-22-modular-harness.md)的生命周期基础，不单独扩展为新的产品路线。

当前实现以 [架构说明](../architecture.md) 为准，使用方法以 [README](../../README.md) 为准。

Profile 配置要求保留在[已承接的通用 Profile 设计](2026-09-06-universal-profiles.md)中。本计划描述职责边界，不要求一次性重写全部模块；长任务规划已暂缓归档。

## 为什么需要下一轮整理

Profiles、共享能力和 TUI 已经接通，但 CLI 仍同时负责交互、组件创建和会话状态。接入第二个界面或执行中纠偏时，这些职责会增加修改范围。

本轮目标是明确边界，不增加插件市场、微服务或新的业务功能，也不要求立刻调整所有目录名。

## 目标结构

```text
TUI / 未来 Web 入口 / 已有评测
  → 统一创建入口（读取 Profile，创建所需能力）
  → AgentSession：一段对话的状态与生命周期
      → AgentRun：一次用户任务的状态与生命周期
          → AgentLoop：模型、策略与工具执行流程
              → 上下文组装 / 模型适配 / 工具资源

关键执行事件 → 界面展示 / 运行记录 / 评测
```

`AgentLoop` 在这里表示现有 MachineLoop 的职责，不要求为了命名而重写它。Web 入口尚未实现；评测的具体接入方式需结合用户已有脚本确定。

## 实施顺序与验收

| 步骤 | 调整 | 验收 |
| --- | --- | --- |
| 1. 收拢创建入口 | 将生产环境的组件组装集中到 Runtime；保留模型和执行器的测试替换能力 | CLI 和无界面测试走同一条默认组装路径，Coding 行为保持一致 |
| 2. 区分会话与任务 | AgentSession 持有会话、配置和压缩视图；AgentRun 持有取消信号、轮数与任务证据 | 连续任务不复用过期验证；取消保留会话；恢复和 Profile 切换的状态边界明确 |
| 3. 整理工具环境 | 用组合表达文件沙箱、记忆、Skills 和会话资源，逐步替换隐式属性探测 | Companion 的资源使用不依赖文件沙箱继承；不同 Profile 的状态范围保持一致 |
| 4. 统一执行事件 | 明确模型、工具、确认、完成、失败、取消的事件数据；TUI 与 Trace 消费事件 | 同一执行过程能由界面和评测分别消费，记录不会进入聊天历史 |

权限检查和完成验证属于控制逻辑，不能依赖普通事件监听器是否存在。用户确认结果应通过明确的恢复接口交回执行层。

## 已实施：AgentSession / AgentRun 第一切片

`src/runtime/factory.py` 的 `build_runtime_components()` 统一创建工具、权限、上下文、完成验证、预算和状态栏。CLI 仍创建终端 Hook 和模型适配器，并把 UI 需要的预算元数据传入；Factory 与 CLI 使用同一组组件实例。

`src/runtime/session.py` 现在管理一段对话的 JSONL 派生视图：它保存压缩产物和视图起点，负责用户/技能消息追加、清屏、手动压缩、会话切换和中断回执修复；原始 JSONL 仍不被覆盖。

`src/runtime/run.py` 管理一次任务的启动、取消和 ASK 权限恢复：它重置任务级完成验证状态，在批准或拒绝后统一执行/构造工具结果、触发 `post_tool` Hook、回填消息、追加 JSONL，然后使用同一取消令牌恢复 `MachineLoop`。CLI 只负责收集批准结果。

`src/engine/request_view.py` 集中历史召回和临时状态追加；`src/engine/events.py` 与 `HookManager.on_event()` 提供 typed 执行事件。UI、Trace 和状态栏使用事件流，CompletionGate 等控制策略继续使用兼容 Hook。

本切片没有实现完整的多入口 `AgentSession`；Profile 级组件创建和终端交互仍在 CLI / Runtime 周围。执行轮数仍由 `MachineLoop` 管理，完成证据仍由 `CompletionGate` 管理。typed `AgentEvent` 已覆盖 UI、Trace 和状态栏使用的事件，但尚未统一所有生命周期事件。

## 状态生命周期

| 范围 | 包含内容 | 结束条件 |
| --- | --- | --- |
| 会话 | Profile、会话存储、历史与压缩视图 | 切换会话、切换 Profile 或退出 |
| 单次任务 | 取消信号、执行轮数、候选回答、修改与验证证据 | 当前任务完成、失败或取消；权限等待后恢复时仍属于同一次任务 |

先封装现有行为，再考虑执行中纠偏、消息队列或会话分叉。每个步骤复用现有测试，并为发生变化的生命周期补充行为测试。

## 暂不计入已完成能力

- `create_session`、`submit`、`cancel` 是目标接口示意，不是当前可导入 API。
- `interfaces/`、`providers/`、`capabilities/` 是讨论中的目录候选，不是当前文件结构。
- Qwen GGUF 优化、操作系统级沙箱和确定性工具回放不属于本轮架构整理。

不预填性能收益或任务成功率；重构首先验证行为兼容性，后续实验再使用真实记录评价效果。
