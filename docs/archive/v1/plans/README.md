# 后续需求与计划

[返回首页](../../../../README.md) · [产品总览](../product-overview.md) · [当前架构](../architecture.md) · [当前 Profile 用法](../profiles.md)

这里统一放未来方向和实施计划，不再另建 requirements 文件夹。当前功能以首页和架构说明为准；计划中的接口和能力不代表已经实现。

## 当前主线

| 文档 | 目标 | 状态 |
| --- | --- | --- |
| [模块化 Harness 与插件适配](2026-09-22-modular-harness.md) | 让核心执行流程可复用，并通过明确 Adapter 插槽接入外部能力 | Phase 0–4 本地实现；Phase 5 按现有需求验收完成；Phase 6 待条件满足 |

CLI 与无界面 Review 示例已共用公共入口；外部调用方通过本地 editable 依赖安装成功。Textual CLI 是应用层更新，交互使用方式见 [CLI 指南](../cli.md)。腾讯 L1 仍是可选实验；Phase 6 以第二个真实外部 Adapter 为前提。

## 暂缓

- [长任务与上下文](../../2026-09-06-long-tasks.md)：Goal / Todo 与长任务编排暂不属于 Harness 主线，保留原方案以便以后重新评估。

## 历史方案

已完成或暂缓的设计放在 `docs/archive/`，保留设计取舍和验收依据，不作为新的待办清单。实际实现仍以代码和测试为准。

- [Harness Core v0.1 收尾](../../2026-10-04-harness-core-readiness.md)：已完成的生命周期、业务示例与独立安装验收记录。
- [Harness 第二调用者验证](../../2026-09-24-harness-reuse-validation.md)：已完成的独立只读 Review 验证记录。
- [Harness 公共事件与权限交接](../../2026-09-24-harness-public-events.md)：已实现的事件契约和权限交接验收记录。
- [运行时边界演进](../../2026-09-05-runtime-boundaries.md)：部分实施的历史计划，其余设想不作为当前实施指令。
- [通用 Profile 设计](../../2026-09-06-universal-profiles.md)：已被 Harness 主线承接。
- [完成验证门 V2](../../2026-09-01-completion-verification-v2.md)
- [记忆与上下文恢复](../../2026-08-31-memory-compaction-resilience-v2.md)
