# Harness Core v0.1 收尾计划

日期：2026-10-04；状态：已完成（实施结果见文末）。本文只记录本轮收尾事项，不重写 Phase 0–6 历史；阶段与长期方向见[模块化 Harness 与插件适配计划](2026-09-22-modular-harness.md)。

[规划入口](README.md) · [当前架构](../architecture.md) · [ADR 0001](../adr/0001-harness-event-contract.md)

## 定位

轻量、可组合的 Agent Framework：公共接口保持最小集合（`open_harness_session`、`AgentSession`、`AgentRun`、`AgentEvent` 与契约类型），能力默认关闭、按需注入。Coding CLI 是其中一个应用，不是框架的一部分。扩展以真实调用需求驱动；不为目录对称新增 Provider / Manager / Builder，不扩大默认依赖。微软框架的职责划分只作对照参考，见[对照阅读](../microsoft-agent-framework.md)。

## 目标

开发者在独立 Python 项目里安装本包，接入自己的模型、工具和会话存储，按需启用外部记忆，并可靠处理工具执行、人工确认、取消和恢复。

## 本轮范围

### 1. 任务生命周期三处疑点

先用确定性模型和本地工具替身写行为用例复现，再做最小修复；不把推测当已确认缺陷。

| 疑点 | 关注行为 | 验收要点 |
| --- | --- | --- |
| 单次回复多个工具调用 | 暂停审批时批次中断，剩余调用被遗漏 | 同批调用审批前后顺序不变；批次未完成前不向模型提交缺少回执的历史；拒绝单个调用后剩余调用按明确规则处理；每个调用都有回执或明确未执行原因；已执行工具不重复执行 |
| 等待审批期间取消 | 批准先于取消检查执行了工具 | `permission_required` 后 `cancel()`，随后到达的批准不执行工具；结果与事件一致；已消费的权限决定不可重复提交；取消是协作式，不宣称撤回已发生副作用 |
| 权限恢复后的轮数预算 | 每次恢复把 turn 归零，重新获得整份预算 | 保留现有 max_turns 定义；暂停与批准不获得新预算；状态展示、事件轮次与实际计数一致 |

保留既有持久化失败约定：SessionStore 写入失败即停止任务；保留实际工具结果，不自动重试有副作用的工具；恢复时无法确定的执行状态明确表示未知。

批次处理的明确规则（本轮确立）：同一回复内的调用按顺序逐个处理；策略拒绝或用户拒绝单个调用后，同批剩余调用继续按各自的权限检查处理；只有取消会中止批次，未执行调用补明确的未执行回执。

### 2. 业务组合示例

新增“订单查询与回复草稿助手”示例：本地字典演示数据、自定义查询与草稿工具、业务提示词，通过公共入口运行并展示结果与事件。提供无需 API Key 的确定性演示路径；不发送真实消息、不连生产系统。验收看调用方是否需要自建 MachineLoop 或复制权限恢复逻辑，是否被迫依赖 Coding 提示词、工具、沙箱或技能扫描；只有示例暴露问题时才做必要解耦。

### 3. 公共接口约定整理

落实并写清：各结果状态返回哪些字段；配置错误、调用方式错误与运行失败的区分；`permission_required` / `permission_resolved` / 工具结果事件的顺序；宿主显式提交权限决定，`on_event` 返回值不控制执行；公开事件与权限请求使用脱敏副本；回调异常不中断任务且日志不含敏感原文。优先补类型说明与文档，确有必要才调整字段。

### 4. 独立安装交付

构建本地 wheel，在仓库外干净临时环境用普通安装（非 editable、不经 PYTHONPATH）导入公共包并跑最小示例；确认构建产物不含 `.env`、会话数据、密钥、`AGENTS.md`、`HANDOFF.md` 等本机资料；模型配置从调用方环境读取。Windows 优先验证；其他环境不可验证则如实记录。不要求 PyPI 发布。

### 5. 文档与验收

更新快速入门、Cookbook、架构说明、本计划索引与本机 HANDOFF.md；运行本轮相关测试、`uv run pytest -q`、`git diff --check`、文档本地链接检查和干净环境 wheel 验证。

## 实施结果（2026-10-04）

### 1. 任务生命周期

三条疑点均先用确定性模型和本地工具替身复现（`tests/test_task_lifecycle.py`，6 个用例），对照 HEAD 基线代码全部红、修复后全部绿：

- **单次回复多工具调用**：确认缺陷——审批暂停后同批剩余调用被遗漏，且历史缺少回执。修复：`MachineLoop._run_batch()` 按顺序成批处理，暂停返回 `remaining_tool_calls`，`AgentRun.resolve_permission()` 恢复后继续同批剩余调用；每个调用都有回执或明确「未执行」原因，已执行的不重复。用户拒绝只影响当前调用，剩余调用继续各自检查（规则已写入文档）。
- **等待审批期间取消**：确认缺陷——取消后到达的批准仍执行了工具。修复：`resolve_permission()` 先消费并上报决定（`permission_resolved`），取消状态下不执行工具、补「未执行」回执；批次内剩余调用由 `_abort_unexecuted()` 补回执；结果为 `cancelled`，事件顺序一致。重复提交已消费的决定仍抛 `RuntimeError`。取消语义明确为协作式。
- **权限恢复后的轮数预算**：确认缺陷——每次恢复把 turn 归零。修复：`MachineLoop.run(start_turn=..., resume_batch=...)` 由 `AgentRun` 传回暂停轮次，一次任务的 `max_turns` 连续计数；事件轮次与实际计数一致（基线对照里两次暂停都报 `turn=0`，修复后为 0、1）。

持久化失败约定保持不变（`session_write_failed` 停止任务、保留工具结果、恢复时未知状态明确标注）。

### 2. 业务示例

`examples/order_assistant.py`：本地字典订单、`query_order`（auto）与 `save_reply_draft`（ask）自定义工具、业务提示词、`open_harness_session()` 公共入口、事件展示、确定性演示路径（无需 API Key），`--live` 才调用真实模型。示例不发送真实消息。配套 `tests/test_order_assistant_example.py` 验证：调用方不构造 MachineLoop、不复制权限恢复逻辑；工具表只有业务工具；暂停视图脱敏；显式预算下完全离线启动。示例暴露两处摩擦并做最小解耦：`context_budget` 显式时不再查询模型窗口；`skills=()` 跳过技能目录扫描。

### 3. 公共接口约定

[Cookbook 第 6 节](../harness-cookbook.md#6-运行结果与事件契约)新增结果字段表、三类错误区分、事件顺序、脱敏与取消语义；`open_harness_session` 补齐参数与错误约定 docstring。未调整字段、未增加公共类型。

### 4. 独立安装

`uv build` 产出 `dist/auto_coding_machine-0.1.0-py3-none-any.whl`；Windows 与 WSL（Ubuntu）均在仓库外干净 venv 普通安装（非 editable、不经 PYTHONPATH）验证：导入包路径来自 site-packages，模型配置取自调用方环境文件，最小任务返回 `success`。产物与 sdist 均不含 `.env`、会话数据、密钥、`AGENTS.md`、`HANDOFF.md`。`dist/`、`build/` 已加入 `.gitignore`。

### 5. 文档与清理

快速入门新增业务示例入口；架构说明同步批次规则、预算连续计数、协作式取消、离线启动与技能扫描边界；本计划入索引；HANDOFF.md 更新。清理：删除零调用的 `AgentRuntime.resume()`（公开入口不受影响）。

### 验收

- `tests/test_task_lifecycle.py`、`tests/test_order_assistant_example.py`、`tests/test_release_boundaries.py` 及相邻套件通过；Windows 与 WSL 完整测试均为 471 passed，另有 2 个压缩预算子用例通过（2026-10-04，包含 CLI 展示测试）。
- 审批恢复期间保留未完成的工具批次，批次结束后再压缩；最新批次超过消息数量或 Token 预算时，保留完整调用声明和回执。最后一个工具完成后的取消返回 `cancelled`；工具回执持久化失败仍优先返回 `session_write_failed`。
- 订单示例 `--live` 通过已注册业务工具的 Schema 创建 `ModelAdapter`；接线测试验证模型和执行器使用同一工具定义。
- `git diff --check` 通过；文档本地链接检查 101 条无断链；wheel 和 sdist 排除私有配置及会话数据。Windows 干净环境普通安装 wheel 后，公共包来自 site-packages，订单示例的本地演示任务返回 `success`；WSL 独立安装验证见前述记录。真实模型质量评估未包含在本轮验收中。

## 不在本轮

SSE、异步公共接口、多 Agent 编排、插件安装、腾讯云部署。Phase 6 的启动条件保持不变：出现第二个真实外部 Adapter 产生安装注册需求。
