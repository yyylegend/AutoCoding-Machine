# AutoCoding Machine 完成验证与最终回答提交 V2 计划

**Author:** Codex
**Date:** 2026-09-01
**Status:** Implemented（2026-09-01 由 WorkBuddy 按本计划实施，审查修复后 316 测试全绿）
**Reviewers:** 用户、WorkBuddy
**Related:** `docs/architecture.md`、Hermes `verify_on_stop` / pending verification response / `pre_verify`
**Implementation branch:** `dev`
**Objective:** 修复“模型回答已经展示，系统又要求验证并再次回答”的用户体验问题，同时避免临时文件触发无意义测试。

## Context

当前工作区存在一版尚未提交的 `CompletionGate` V1 草稿。它通过 `post_tool` Hook 观察
`write_file` / `edit_file` 和测试命令，使用修改版本号判断模型能否宣布完成。基础规则有效，
但真实 session 暴露出两个结构性问题。

复现文件：
`.autocoding/sessions/20260901-020505-384234.jsonl`。

该 session 中，用户只要求讲解摘要降级与 Runtime 组装。模型因为 `read_file` 会截断长文件，
先后多次写入 `_tmp_dump.py`，运行后让脚本自删。最终工作区没有 `_tmp_dump.py`，也没有项目源码净变化。
但 V1 Gate 只看到多次 `write_file`，仍要求模型运行测试。用户先看到了流式生成的完整讲解，
随后又看到 Agent 请求测试、运行测试并输出第二份验证回答。

根因：

1. **证据模型错误**：V1 记录“发生过写工具”，没有判断任务结束时是否存在文件净变化。
2. **提交时序错误**：LLM Adapter 在 Gate 判断前就把候选最终回答永久显示给用户。
3. **工具能力缺口**：`read_file` 不支持按行分页，迫使模型制造临时脚本读取长文件。
4. **验证语义过弱**：任意写入后的任意成功测试都可关闭 Gate，无法区分临时文件、文档和真实代码修改。

Hermes 的可借鉴原则：

- 候选最终回答作为 pending response 保留，验证 continuation 不应覆盖实质回答；
- synthetic verification nudge 是内部脚手架，不应进入用户可见历史；
- verify-on-stop 依据 changed paths 和新鲜验证证据，而不是相信模型说 done；
- 文档类修改不应默认触发代码式验证；
- `read_file` 提供 offset/limit 或等价的行范围分页，避免临时脚本。

本项目不复制 Hermes 的大型 `turn_finalizer`。V2 保持一个 Coding 专属的深模块，MachineLoop 只消费
完成判断结果，CLI 只负责展示已提交的最终回答。

---

## Goals

1. 用户每个任务最多看到一份**持久化的最终回答**。
2. 模型流式生成的候选回答在 Gate 通过前不得被当成正式回答。
3. 临时文件创建后删除、文件修改后恢复原内容，均不得触发代码验证。
4. 真正的代码净变化必须在最后一次相关修改之后存在成功验证证据。
5. 验证 continuation 不得丢失原候选回答，也不得把验证回执替代为最终回答。
6. 长文件可以通过 `read_file` 分页读取，不需要创建临时脚本。
7. 保持 JSONL 追加写与“只存真实对话/工具流水”的约定。

---

## Functional Requirements

### 3.1 文件净变化

- FR-1: CompletionGate MUST 在每个新用户任务开始时清空上一任务的候选回答、路径快照、修改版本和验证证据。
- FR-2: 在 `write_file` / `edit_file` 首次尝试修改某个路径前，Gate MUST 保存该路径的基线状态：
  `exists` 与内容 SHA-256。
- FR-3: 路径快照 MUST 限制在 workspace 内；非法或越界路径 MUST NOT 被读取。
- FR-4: Gate MUST 为每个成功修改事件记录单调递增版本，并记录每个路径的最后修改版本。
- FR-5: 模型申请结束时，Gate MUST 重新读取所有已跟踪路径并与基线比较，得到 `changed_paths`。
- FR-6: 基线与当前状态相同的路径 MUST NOT 出现在 `changed_paths`。
- FR-7: 任务开始时不存在、结束时仍不存在的临时文件 MUST NOT 触发验证。
- FR-8: 先修改后恢复为原始内容的文件 MUST NOT 触发验证。

### 3.2 验证证据

- FR-9: 当 `changed_paths` 为空时，Gate MUST 接受候选回答，不要求测试。
- FR-10: 当 `changed_paths` 全部为 `.md`、`.rst`、`.txt` 或 `LICENSE` 类文档时，Gate SHOULD 跳过代码式验证。
- FR-11: 除纯文档外的净变化 MUST 要求新鲜验证证据。
- FR-12: `run_test` 仅在工具未报错且 `exit_code == 0` 时算成功验证。
- FR-13: `run_bash` 仅在工具未报错、`exit_code == 0`，且命令被确定性规则识别为测试、lint、build、type-check 或 diff-check 时算验证。
- FR-14: 有效修改版本 MUST 等于当前所有 `changed_paths` 的最后修改版本最大值。
- FR-15: 只有发生在有效修改版本之后或同版本之后的成功验证，才能关闭 Gate。
- FR-16: 已经验证后再次修改真实代码，旧验证 MUST 自动失效。
- FR-17: 成功验证后只发生临时文件写入且临时文件最终消失时，旧验证 SHOULD 保持有效。

### 3.3 候选回答与正式提交

- FR-18: 模型第一次产生无 ToolCall 的文本时，MachineLoop MUST 把它视为 candidate response，而不是立即视为已提交回答。
- FR-19: Gate MUST 将 candidate response 与当时的有效修改版本绑定。
- FR-20: 证据充分时，Gate MUST 返回唯一的 committed response。
- FR-21: 证据不足但仍允许继续时，Gate MUST 保留 candidate response，并返回一条 synthetic verification nudge。
- FR-22: synthetic verification nudge MUST 只存在于运行时工作消息，不得追加到 Session JSONL。
- FR-23: 如果 continuation 期间没有新的真实净修改，验证成功后 MUST 复用原 candidate response，并附加确定性验证 footer；模型生成的纯验证回执不得替代原回答。
- FR-24: 如果 continuation 期间发生新的真实净修改，旧 candidate response MUST 标记为过期；后续必须等待新的候选回答。
- FR-25: 验证被拒绝、失败或连续两次无证据时，任务 MUST 返回 `verification_required`，同时向用户交付原 candidate response 和紧凑的“未验证/验证失败”footer。
- FR-26: 若 continuation 耗尽最大轮数，但存在 pending candidate，系统 MUST 返回该 candidate 并附加未验证 footer，不得只返回 `max_turns` 而丢失用户答案。

### 3.4 流式展示

- FR-27: 当 Gate 没有未验证净修改且没有 pending candidate 时，CLI MAY 正常流式展示回答。
- FR-28: 当 Gate 存在未验证净修改或 pending candidate 时，LLM 调用 MUST 继续使用 streaming API 获取 token，但 CLI MUST 静默缓冲文本，不得创建持久回答面板。
- FR-29: Gate 接受后，CLI MUST 只展示一次 committed response。
- FR-30: Gate 拒绝并继续时，CLI SHOULD 展示简短状态，例如“正在验证修改…”，不得展示第二个完整回答框。
- FR-31: CLI 的 `last_streamed` MUST 表示“最终回答已经持久展示”，不能仅表示“收到过 token”。

### 3.5 `read_file` 分页

- FR-32: `read_file` MUST 支持可选 `start_line` 与 `end_line`，均为 1-based 且包含端点。
- FR-33: 不传分页参数时 MUST 保持当前行为兼容。
- FR-34: 只传 `start_line` 时 SHOULD 从该行读到文件末尾，再受现有字符上限约束。
- FR-35: `start_line < 1`、`end_line < start_line` 或非整数 MUST 返回 `invalid_args`。
- FR-36: 返回内容 SHOULD 带真实行号，并在未读完时提示下一段建议范围。
- FR-37: System Prompt MUST 告诉模型长文件优先分页读取，禁止为了读取文件创建临时脚本。

---

## Non-Functional Requirements

- NFR-1: 未配置 CompletionGate 的 MachineLoop 调用方 MUST 保持原行为。
- NFR-2: 文件 hash MUST 流式分块计算，单次不把大文件整体读入内存。
- NFR-3: Gate 的路径快照 MUST NOT 绕过 workspace 围墙读取外部路径。
- NFR-4: 文件在检查期间被删除、替换或暂时不可读时，Gate MUST 返回明确的未验证状态，不得崩溃或静默放行。
- NFR-5: `completion_rejected` Hook SHOULD 包含 reason、changed_paths、candidate_version 和 validation_version。
- NFR-6: candidate、synthetic nudge 和内部 footer scaffolding MUST NOT 进入 JSONL；committed response MUST 恰好写入一次。
- NFR-7: 不新增数据库表、后台任务、LLM Judge、插件注册系统或第二套 CompletionGate。
- NFR-8: 所有既有测试 MUST 通过；新增行为必须由 seam 级 pytest 覆盖。

---

## Acceptance Criteria

### AC-1: 临时文件无净变化 (FR-1, FR-2, FR-3, FR-4, FR-5, FR-7, FR-9)
Given 任务开始时 `_tmp_dump.py` 不存在
When Agent 创建并删除它后申请结束
Then `changed_paths` 为空且 Gate 不要求测试

### AC-2: 恢复原内容无净变化 (FR-6, FR-8)
Given `src/a.py` 有基线内容
When Agent 修改后恢复相同内容
Then Gate 判断无净变化

### AC-3: 未验证代码变化继续执行 (FR-11, FR-14, FR-15, FR-18, FR-19, FR-21)
Given `src/a.py` 存在净变化
When 模型在没有修改后成功验证的情况下申请结束
Then candidate 被保留且 Gate 返回 continue

### AC-4: 修改后成功验证允许完成 (FR-12, FR-13, FR-14, FR-15, FR-20)
Given `src/a.py` 存在净变化
When 修改后 `run_test` 或合格的 `run_bash` 验证命令以退出码 0 完成
Then Gate 接受 candidate

### AC-5: 新修改使旧验证失效 (FR-16)
Given 测试已经成功
When Agent 再次修改 `src/a.py` 后申请结束
Then 旧验证失效

### AC-6: 消失的临时写入不使验证失效 (FR-17)
Given 真实代码已经验证
When 之后只创建并删除临时脚本
Then 原验证仍然有效

### AC-7: 原回答只提交一次 (FR-23, FR-27, FR-29, FR-30)
Given 用户已经生成一份 1000 字候选讲解
When Gate 要求验证且验证成功
Then 用户最终只看到原讲解一次，并附一行验证 footer

### AC-8: 新修改淘汰旧候选回答 (FR-24)
Given Gate 已保存 candidate v1
When 验证失败并导致代码再次修改
Then v1 失效，系统只提交后续 candidate v2

### AC-9: 验证被拒仍保留实质回答 (FR-25)
Given 用户拒绝验证命令
When 模型再次申请结束
Then 结果状态为 `verification_required`，且 reply 包含原 candidate 与“未验证”footer

### AC-10: 轮数耗尽保留候选回答 (FR-26)
Given Gate 保存了 pending candidate
When continuation 耗尽轮数
Then 系统交付 pending candidate，而不是只显示 `max_turns`

### AC-11: 内部验证提示不落盘 (FR-22, NFR-6)
Given 发生验证 continuation
When 读取 Session JSONL
Then 不存在 `[完成验证]` synthetic user 消息，也不存在被拒绝的候选 assistant 消息

### AC-12: Gate 打开时静默缓冲流 (FR-28, FR-31)
Given Gate 当前未关闭
When 模型流式生成候选回答
Then token 被缓冲但 CLI 不留下最终回答面板，且 `last_streamed` 不得错误抑制最终提交

### AC-13: 按行分页读取长文件 (FR-32, FR-33, FR-34, FR-36, FR-37)
Given 一个 500 行文件
When 调用 `read_file(start_line=200, end_line=260)`
Then 只返回 200-260 行且带真实行号，不传分页参数时旧行为保持兼容

### AC-14: 非法分页参数被拒绝 (FR-35)
Given `start_line < 1` 或 `end_line < start_line`
When 调用 `read_file`
Then 返回 `invalid_args` 且不读取文件

### AC-15: 未注入 Gate 时保持兼容 (NFR-1, NFR-8)
Given CompletionGate 未注入
When 运行现有 MachineLoop 测试
Then 行为与 V2 前一致，完整测试套件全部通过

### AC-16: 纯文档净变化跳过代码测试 (FR-10)
Given `changed_paths` 只包含 Markdown、RST、TXT 或 LICENSE 文件
When 模型申请结束
Then Gate 不要求代码式测试，并返回文档变更 footer

---

## Edge Cases

- EC-1: 文件在 `pre_tool` 快照之后、工具执行之前被外部进程修改。最终比较以任务基线与当前状态为准，保守标记为 changed。
- EC-2: 工具报告失败但文件发生部分写入。因为路径已在 `pre_tool` 跟踪，最终净变化仍会被发现。
- EC-3: 文件非常大。hash 使用固定大小块，不整文件加载。
- EC-4: 符号链接指向 workspace 外。解析后的真实路径越界时不读取，并保守要求未验证。
- EC-5: 验证命令退出码 0，但只是 `git status` / `pwd` / `ls`。不得算验证。
- EC-6: 验证命令输出被截断。只要工具未报错且结构化 `exit_code == 0`，仍可算验证。
- EC-7: 用户拒绝 ASK 验证命令。保留 candidate，最终以未验证状态交付一次。
- EC-8: 模型候选回答为空。不得创建 pending candidate，按现有 `no_tool_call` 失败处理。
- EC-9: 候选回答后发生新的代码修改但模型没有再给实质回答。旧 candidate 不得作为成功答案提交。
- EC-10: 只有 Markdown 文件发生净变化。跳过代码测试，但 footer 可标记“文档变更，未运行代码测试”。
- EC-11: 同一任务经 ASK 权限暂停后 `Runtime.resume()`。路径快照、pending candidate 和验证证据必须保留。
- EC-12: 切换 session 或新用户 prompt。必须调用 `start_task()`，不得继承上一任务状态。

---

## API Contracts

N/A — 本功能不新增 HTTP endpoint；这里定义内部模块的调用契约。

V2 不新增抽象基类。MachineLoop 继续依赖一个注入对象，Coding Profile 提供唯一正式实现。

```typescript
interface FileState {
  exists: boolean;
  sha256: string | null;
}

interface CompletionDecision {
  action: "accept" | "continue" | "fail";
  finalResponse: string;
  continuationMessage: string;
  reason: string;
  changedPaths: string[];
}

interface CompletionGate {
  startTask(): void;
  beforeTool(toolName: string, arguments: Record<string, unknown>): void;
  afterTool(toolName: string, error: boolean, resultMetadata?: Record<string, unknown>): void;
  evaluate(candidateResponse: string): CompletionDecision;
  shouldPublishStream(): boolean;
}
```

接口约束：

- `before_tool` / `after_tool` 必须可以直接注册到现有 HookManager；
- `evaluate` 是唯一完成判断入口；MachineLoop 不读取 Gate 内部计数器；
- `final_response` 由 Gate 决定，CLI 不自行拼 candidate 与 footer；
- `should_publish_stream()` 只回答展示策略，不执行副作用。

### Runtime contract

```python
runtime.run(messages, cancel)     # 新用户任务，重置 Gate
runtime.resume(messages, cancel)  # ASK 恢复，保留 Gate 状态
```

### `read_file` schema extension

```python
read_file(
    path: str,
    start_line: int | None = None,
    end_line: int | None = None,
) -> ToolResult
```

---

## Data Models

全部状态仅存在于当前 Runtime 进程，不新增持久化模型。

| State | Type | Constraint | Purpose |
| --- | --- | --- | --- |
| baseline_by_path | `dict[str, FileState]` | 每任务首次路径快照 | 判断净变化 |
| last_mutation_version | `dict[str, int]` | 单调递增 | 判断真实 changed path 的最新修改 |
| validation_version | `int` | 默认 0 | 判断验证是否发生在有效修改后 |
| pending_response | `str | None` | 不写 JSONL | 保存实质候选回答 |
| pending_version | `int | None` | 与 candidate 绑定 | 新修改时使旧 candidate 失效 |
| rejection_count | `int` | 最大 2 | 防止无限 continuation |

---

## Module Placement and Entropy Control

### Replace, do not layer

当前未提交的 `src/engine/completion_gate.py` V1 是 Coding 专属策略，却放在通用 Engine 中并硬编码
`write_file`、`edit_file`、`run_test`。V2 实施时 SHOULD：

1. 将正式实现移动到 `src/profiles/coding/completion_gate.py`；
2. 删除 V1 的 Engine 实现和 `src/engine/__init__.py` 导出；
3. MachineLoop 只保留 `completion_gate.evaluate(candidate)` 这一处 seam；
4. Runtime Factory 创建 Coding Gate 并注册 `pre_tool` / `post_tool` Hook；
5. 不保留 V1 与 V2 两套状态机。

### Expected file impact

新增或替换：

- `src/profiles/coding/completion_gate.py`
- `tests/test_completion_gate.py`（重写为 V2 seam 测试）

必要修改：

- `src/engine/machine_loop.py`
- `src/runtime/factory.py`
- `src/runtime/runtime.py`
- `src/profiles/coding/llm_adapter.py`
- `src/profiles/coding/cli.py`
- `src/profiles/coding/tools/read_file.py`
- `src/profiles/coding/system_prompt.py`
- `src/engine/hook_manager.py`
- `README.md`
- `docs/architecture.md`

明确不修改：

- JSONL 文件格式与 SessionStore；
- ContextManager / ContextSelector；
- PermissionManager / WorkspaceSandbox；
- MemoryManager；
- 数据库与依赖锁文件。

---

## Implementation Phases

### Phase 0：冻结规格与保护当前工作树

1. 用户批准本计划后再实现。
2. 记录当前未提交 V1 diff；不得 reset 或丢失已有测试。
3. 将 V1 测试逐条映射到本计划 AC；不符合 V2 的断言先改测试，再替换实现。

验证：计划状态改为 Approved，AC-1 至 AC-15 均有测试名称映射。

### Phase 1：`read_file` 行范围分页

1. 先写 AC-13 / AC-14 失败测试。
2. 增加 `start_line` / `end_line` 参数校验与行号输出。
3. 更新 schema 和 System Prompt。
4. 保持旧调用不传分页参数时行为不变。

验证：分页测试通过；现有 read_file 与工具注册测试不回归。

### Phase 2：基线快照与净变化

1. 将 Gate 移到 Coding Profile。
2. 实现 workspace 内路径规范化和分块 hash。
3. 注册 `pre_tool` 捕获首次基线，`post_tool` 记录修改版本与验证版本。
4. 实现 `changed_paths` 与有效修改版本计算。

验证：AC-1、AC-2、AC-3、AC-4、AC-5、AC-6 通过。

### Phase 3：pending candidate 状态机

1. 用 `evaluate(candidate)` 替换 V1 `check()`。
2. 保存 candidate 与 mutation version。
3. 验证-only continuation 复用原 candidate；发生新净修改时使其失效。
4. fail/max-turn 路径仍交付 candidate + footer。
5. synthetic nudge 保持运行时临时消息，不调用 SessionStore。

验证：AC-7、AC-8、AC-9、AC-10、AC-11 通过。

### Phase 4：流式展示两阶段提交

1. `RichLLMAdapter` 根据 `should_publish_stream()` 决定 Live 展示或静默缓冲。
2. `last_streamed` 改为只表示 committed response 已展示。
3. Gate continuation 期间只显示状态，不显示完整 candidate。
4. CLI 在 accept/fail 时打印 Gate 返回的唯一 final_response。

验证：AC-12 通过；用伪流逐 token 回归“用户只看到一个持久回答”。

### Phase 5：ASK、Session 与全链路回归

1. 覆盖验证命令 ASK 允许、拒绝与多次暂停。
2. 覆盖 `runtime.run()` 重置、`runtime.resume()` 保留。
3. 用临时 workspace 复现原 session：创建 `_tmp_dump.py`、自删、候选回答。
4. 检查 JSONL 只出现一个最终 assistant 回答，且无 synthetic nudge。
5. 更新 README、架构文档和计划实施状态。

验证：AC-15；`uv lock --check`、`uv run --locked pytest -q`、`git diff --check` 全部通过。

---

## Test Plan

建议测试名称：

```text
test_temporary_file_created_then_deleted_is_not_a_net_change
test_file_restored_to_baseline_is_not_a_net_change
test_real_code_change_requires_fresh_verification
test_test_before_last_real_change_is_stale
test_temporary_write_after_validation_does_not_invalidate_evidence
test_pending_candidate_is_reused_after_verification_only_continuation
test_pending_candidate_expires_after_new_real_change
test_denied_verification_returns_candidate_with_unverified_footer
test_iteration_limit_preserves_pending_candidate
test_synthetic_verification_nudge_is_not_persisted
test_streamed_candidate_is_buffered_while_gate_is_open
test_committed_final_response_is_displayed_once
test_read_file_returns_requested_line_range
test_read_file_rejects_invalid_line_range
test_runtime_resume_preserves_pending_candidate_and_evidence
```

测试应通过公共 seam 观察：Gate decision、MachineLoop result、CLI renderer 输出和 SessionStore.load()。
不得断言私有字典或内部版本号的具体字段布局。

---

## Risks and Mitigations

| Risk | Impact | Mitigation |
| --- | --- | --- |
| hash 大文件变慢 | 完成检查延迟 | 分块读取，只 hash 实际被修改过的路径 |
| 外部进程并发修改文件 | 证据归因不精确 | 以任务基线与最终状态为事实，保守要求验证 |
| validation command 关键词误判 | 无关命令关闭 Gate | 使用 token 级 allowlist + exit_code，不做任意 substring |
| candidate 在修复后过期 | 用户收到旧结论 | 与有效修改版本绑定，新真实修改立即失效 |
| 静默缓冲降低流式体验 | 用户等待感增强 | 只在 Gate 未关闭时缓冲；干净任务保持原流式体验 |
| 多入口错误调用 `run()` | ASK 后证据被清空 | 所有恢复入口统一调用 `runtime.resume()` 并加集成测试 |

---

## Out of Scope

- OS-1: 不引入 LLM Judge 判断测试是否“足够好”，因为本轮只做确定性证据门。
- OS-2: 不分析代码覆盖率、测试与 changed path 的语义映射，因为需要独立评测设计。
- OS-3: 不实现 Git commit/tree 级隔离，也不修改用户已有 dirty worktree，避免改变现有 Git 工作流。
- OS-4: 不实现桌面前端或多平台消息 ID 管理；本轮只保证 CLI 语义正确。
- OS-5: 不实现 Hermes 完整 Goal / Completion Contract；未来另立计划。
- OS-6: 不把 CompletionGate 做成插件市场或抽象基类，当前只有一个真实实现。
- OS-7: 不保存 candidate 或 Gate 状态到 JSONL/数据库；跨进程中断后按普通未完成任务恢复。
- OS-8: 不自动删除模型创建的临时文件；通过分页读取减少其必要性，通过净变化判断避免误验证。

---

## Definition of Done

以下条件必须全部满足：

1. AC-1 至 AC-15 均有通过的自动化测试。
2. 原 session 场景可重复复现，并确认只产生一份持久最终回答。
3. `_tmp_dump.py` 创建后删除不会触发测试。
4. 真实代码修改未验证时仍会被 Gate 拦截。
5. 用户拒绝验证时不会丢失实质回答。
6. JSONL 不含 synthetic verification nudge 和被拒绝 candidate。
7. `read_file` 支持行范围分页，模型无需临时脚本读取长文件。
8. 当前 V1 Gate 已被替换，没有两套完成状态机。
9. `uv lock --check` 通过。
10. `uv run --locked pytest -q` 全部通过。
11. `git diff --check` 通过，除 Windows 换行提示外无问题。
