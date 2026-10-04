# Harness 公共事件与权限交接实施计划

作者：Codex（根据已确认的 [ADR 0001](../adr/0001-harness-event-contract.md) 整理）；日期：2026-09-24；状态：已实现；评审：用户

## 1. 背景

第二调用者已经能通过 `open_harness_session()` 复用执行循环，但外部程序还没有稳定的运行通知入口。内部 `HookManager.on_event()` 已存在，公共会话入口尚未接收回调。

权限暂停目前会把原始 `ToolCall` 和包含工具参数的 `messages` 一并返回；CLI 也直接展示原始参数。公共 API 需要提供可展示的安全请求视图，同时保留 Harness 内部执行所需的原始调用。具体约定以 ADR 0001 为准。

## 2. 功能要求

- **FR-1**：`open_harness_session()` MUST 接受可选的 `on_event` 回调；它在该会话的多次运行中持续接收事件。
- **FR-2**：回调 MUST 只观察事件。返回值忽略，回调异常不能中断 Agent；注册回调不得自动批准权限。
- **FR-3**：事件名称和决议值使用小写 `snake_case`；`AgentEvent` 可从 `auto_coding_machine` 公共入口导入。
- **FR-4**：权限暂停对外返回安全的 `permission_request`，包含工具名、调用 ID、摘要、详情和轮次；不得在该结果中返回原始 `ToolCall` 或含原始参数的 `messages`。原始调用只保留给 Harness 内部恢复执行。
- **FR-5**：`permission_required` 通知与暂停结果使用同一份安全请求视图。宿主仍须显式调用 `run.resolve_permission(approved=...)`。
- **FR-6**：Harness 在收到批准或拒绝后、执行或回填工具结果前，单独发出 `permission_resolved`，决议值为 `approved` 或 `denied`。工具执行结果仍由后续工具事件和 Run Result 表达。
- **FR-7**：公共事件中的参数详情必须在离开 Harness 前生成副本并脱敏；明显敏感字段及常见内嵌凭据要遮盖，超过 200 字符的参数值整体隐藏。摘要和详情分开，宿主可自行折叠详情。脱敏是展示保护，不替代权限策略或凭据管理。
- **FR-8**：CLI 使用同一安全请求视图显示确认信息；批准和拒绝的交互行为保持不变。

## 3. 非功能要求

- 不增加模型调用、网络请求、后台线程或事件队列；回调保持同步，慢回调会占用调用方线程。
- 公共事件与暂停请求不得改变内部工具参数；批准后执行的仍是原始 `ToolCall`。
- 回调失败日志不得包含事件内容或异常原文，只记录事件名及异常类型。

## 4. 验收标准

- **AC-1（FR-1–3）**：给定带回调打开的会话，当连续运行两次任务时，回调能收到对应事件；回调返回值不会改变运行结果。
- **AC-2（FR-2）**：给定会抛出异常的观察回调，任务仍按原有权限与执行结果结束，异常日志不含测试用敏感字符串。
- **AC-3（FR-4、FR-7）**：给定包含敏感字段、常见凭据文本和超长内容的工具参数，`permission_required` 事件及暂停结果只含脱敏请求视图；不含原始 `ToolCall` 或原始工具参数消息。
- **AC-4（FR-5–6）**：给定等待确认的工具调用，调用方可从暂停结果读取请求；批准时先观察到 `approved` 再执行工具，拒绝时先观察到 `denied` 且工具不执行。
- **AC-5（FR-6）**：给定已批准但执行失败的工具，事件明确区分“已批准”和“执行失败”，不得把批准报告成执行成功。
- **AC-6（FR-8）**：给定 CLI 遇到权限暂停，确认界面展示安全摘要与详情，用户仍可批准或拒绝。
- **AC-7（FR-1、FR-4）**：未注册 `on_event` 时，不增加通知开销或改变任务、取消和权限决定；暂停结果仍使用新的安全字段。

## 5. 边界情况

- 回调为 `None`：不注册公共观察者。
- 回调抛异常：隔离异常，后续执行不受影响，日志不泄露异常原文。
- 拒绝权限：不执行工具，但继续把拒绝结果交回模型，遵循现有流程。
- 自定义工具提供嵌套参数：递归生成安全详情；脱敏规则仅针对明显敏感字段和常见凭据形式，不宣称是完整的秘密扫描器。
- 会话暂停期间再次调用 `resolve_permission()`：遵循现有单次决议约束，不重复执行工具。

## 6. API 契约

```python
session = open_harness_session(workspace, model_fn, on_event=observe)
run = session.begin_run("执行任务")
result = run.start()

if result["status"] == "permission_required":
    request = result["permission_request"]  # 安全视图，不含原始工具参数
    result = run.resolve_permission(approved=ask_user(request))
```

`observe(event: AgentEvent) -> None` 只接收安全事件副本。`permission_required` 的 `event.data["request"]` 与 `result["permission_request"]` 结构相同；`permission_resolved` 携带工具名、调用 ID、`decision` 和轮次。其他公共事件不携带原始消息、工具输出或未经处理的参数。完整的 Run Result 仍独立返回，不由事件替代。

实现已替换 `pending_tool_call` 用法。该接口尚未发布到 PyPI；CLI、Cookbook 和架构说明已迁移，不保留对外读取原始调用的兼容出口。

## 7. 数据模型

| 名称 | 形态 | 字段 |
| --- | --- | --- |
| `AgentEvent` | 现有不可变数据类 | `name: str`、`data: dict`；公共回调只收到安全副本 |
| `permission_request` | 普通字典 | `tool_name`、`tool_call_id`、`summary`、脱敏后的 `details`、`turn` |
| `permission_resolved` | `AgentEvent` 数据 | `tool_name`、`tool_call_id`、`decision`（`approved` / `denied`）、`turn` |

不新增事件总线、SSE 专用模型或异步迭代器。

## 8. 实施顺序

1. 先在 `tests/test_agent_run.py` 和公共入口测试中补齐失败用例：观察回调、脱敏、暂停结果不含原始调用、批准/拒绝事件顺序、回调异常隔离。
2. 在公共入口接入会话级回调；集中构造安全权限请求和事件副本，内部保留原始工具调用供恢复执行。
3. 在 `AgentRun.resolve_permission()` 发出独立决议事件；迁移 CLI 确认显示与 `pending_tool_call` 的文档用法。
4. 在 Cookbook 增加一段外部调用示例，说明监听事件和显式批准；更新架构说明与本计划状态。
5. 先跑相关测试，再运行完整 `uv run pytest -q`；检查 Markdown 相对链接和 `git diff --check`。

## 实施结果

- `open_harness_session(on_event=...)` 注册会话级观察回调；公共 `AgentEvent` 从包根导入。
- 权限暂停只返回脱敏 `permission_request`；原始调用和消息留在运行内部。批准/拒绝分别发出 `permission_resolved`，工具执行结果仍单独报告。
- 回调异常不会中断运行，日志不包含异常原文；CLI 与 Cookbook 使用新的安全视图。
- 完整测试：`uv run pytest -q`，442 passed（2026-09-24）。

## 9. 不在本计划内

- SSE、异步事件流、跨进程事件传输、流式 token 事件。
- 自动批准策略、权限策略重构、重试机制或新的插件系统。
- 将 CLI 重构成独立 UI 框架，或把所有内部 Hook 一次性改造成公共 API。
