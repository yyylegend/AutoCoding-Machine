# 后续需求与计划

[返回首页](../../README.md) · [当前架构](../architecture.md) · [当前 Profile 用法](../profiles.md)

这里统一放未来方向和实施计划，不再另建 requirements 文件夹。当前功能以首页和架构说明为准；计划中的接口和能力不代表已经实现。

## 当前主线

| 文档 | 目标 | 状态 |
| --- | --- | --- |
| [模块化 Harness 与插件适配](2026-09-22-modular-harness.md) | 让核心执行流程可复用，并通过明确 Adapter 插槽接入外部能力 | 方向已确认；接口待设计 |

## 基础工作

| 文档 | 目标 | 状态 |
| --- | --- | --- |
| [运行时边界](2026-09-05-runtime-boundaries.md) | 收拢创建入口，区分会话与单次执行，让界面只负责交互 | 部分实施；作为 Harness 主线的基础工作 |

建议顺序：先明确小接口和生命周期错误语义，再以外部记忆作为第一个真实 Adapter 验证插槽。暂不为了“通用”提前实现插件市场或多个空接口。

## 已承接的设计

- [通用 Profile 设计](2026-09-06-universal-profiles.md)：已被模块化 Harness 主线承接。保留 Profile 配置、状态隔离和权限约束作为设计依据，不再作为独立实施方向。

## 暂缓

- [长任务与上下文](../archive/2026-09-06-long-tasks.md)：Goal / Todo 与长任务编排暂不属于 Harness 主线，保留原方案以便以后重新评估。

## 历史方案

已完成或暂缓的设计放在 `docs/archive/`，保留设计取舍和验收依据，不作为新的待办清单。实际实现仍以代码和测试为准。

- [完成验证门 V2](../archive/2026-09-01-completion-verification-v2.md)
- [记忆与上下文恢复](../archive/2026-08-31-memory-compaction-resilience-v2.md)
