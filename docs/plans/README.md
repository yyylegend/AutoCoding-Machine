# 后续需求与计划

[返回首页](../../README.md) · [当前架构](../architecture.md) · [当前 Profile 用法](../profiles.md)

这里统一放未来需求和实施计划，不再另建 requirements 文件夹。每份文档写明状态、要解决的问题和验收方式；当前功能以首页和架构说明为准。

## 待实施

| 文档 | 要解决的问题 | 状态 |
| --- | --- | --- |
| [通用 Profile](2026-09-06-universal-profiles.md) | 把固定类型改成可组合的预设，理清名称、权限和状态隔离 | 待实施 |
| [运行时边界](2026-09-05-runtime-boundaries.md) | 收拢创建入口，区分会话与单次执行，让界面只负责交互 | 待实施 |
| [长任务与上下文](2026-09-06-long-tasks.md) | Todo 保存进度，Goal 控制继续与停止，压缩后仍能接着做 | 待实施 |

建议按小批次推进：先通用 Profile；在确有需要时收拢对应 Runtime 职责；然后做 Todo、Goal、上下文优化。无需先完成整套架构重写，也不同时开启所有方向。

## 历史方案

已完成的设计统一放在 `docs/archive/`，保留设计取舍和验收依据，不作为新的待办清单。实际实现仍以代码和测试为准。

- [完成验证门 V2](../archive/2026-09-01-completion-verification-v2.md)
- [记忆与上下文恢复](../archive/2026-08-31-memory-compaction-resilience-v2.md)
