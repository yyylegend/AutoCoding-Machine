# AutoCoding Machine 记忆与压缩韧性改动总结

> 日期：2026-08-31 ｜ 分支：`dev`（未提交）｜ 测试：`uv run --locked pytest` → 262 passed（原有 230 + 新增 32）
> 改动范围：记忆、召回、上下文压缩、LLM 适配器、CLI 提示、文档与测试；无新增依赖。
> 代码风格约定：面向新手、中文注释、避免高级语法（无 walrus / 复杂推导式 / 花哨装饰器），显式循环优先。

## 1. 背景与目标

这个仓库是一个轻量 Coding Agent（CLI + Runtime + Engine 三层，JSONL 存会话、Markdown 存长期记忆）。
本次改动的目标：**在保持"JSONL + Markdown"轻量架构的前提下，提高记忆与上下文压缩的韧性**。

硬约束（全程未违反）：
- 不引入 SQLite、向量库、独立摘要模型、外部 Memory Provider。
- 原始 JSONL 是唯一真相源，**只增不减**；压缩只影响进程内视图，不落盘、不改写历史。

## 2. 改动总览

| 文件 | 改动 |
|---|---|
| `src/engine/session_store.py` | 删除 `overwrite()` 死代码；头注释把"只增不减"升格为结构性保证 |
| `src/engine/memory_manager.py` | `add/replace/remove` 全程文件锁 + 临时文件原子替换 |
| `src/engine/tool_manager.py` | `@tool` 装饰器新增可选 `permission_fn`；`get_permission()` 支持按调用参数判定 |
| `src/engine/permission_manager.py` | 透传 `tool_call` 给动态权限；修正头部过期注释 |
| `src/profiles/coding/tools/memory_tool.py` | 动作级权限（`add` 自动、`replace`/`remove` 需确认）；写入异常转工具错误 |
| `src/profiles/coding/tools/recall_history.py` | 跨会话召回 + 最近 10 个上限 + 相邻命中合并 + 输出裁剪 + BM25 兜底 |
| `src/engine/context_manager.py` | 摘要失败 → 确定性摘录；失败冷却；只读诊断状态 |
| `src/common/llm_client.py` | 新增 `ContextLengthExceededError` 领域异常（状态码 + 响应体双重确认） |
| `src/profiles/coding/llm_adapter.py` | 流式超限异常直接交给 MachineLoop，禁止用未压缩上下文做非流式重发 |
| `src/engine/machine_loop.py` | 捕获超限异常 → 强制压缩 → 重试一次 → 无进展则明确失败 |
| `src/engine/hook_manager.py` | 新增 `compaction_fallback` 事件文档 |
| `src/profiles/coding/cli_ui.py` | 注册 `compaction_fallback` 的 CLI 提醒 |
| `tests/test_memory_compaction_resilience.py` | 新增 32 条测试 |
| `.gitignore` | 忽略 Workbuddy 本地记忆目录 `.workbuddy/` |
| `tests/test_context_summary.py` | 按新行为改写 test_04 / test_05 |
| `README.md` / `docs/architecture.md` | 三层记忆模型 + 摘要/超限失败策略 |

## 3. 逐项说明

### 3.1 跨会话历史召回（`recall_history`）

- 从"只搜最新一个 JSONL"改为**扫描工作区 sessions 目录下最近 10 个 session**（`RECALL_SESSION_LIMIT`）。
- 沿用现有中英文分词（中文 bigram + 英文整块）与 BM25，未引入新依赖。
- 语料包含 `content` + `tool_calls`；损坏 JSONL 行跳过。
- 排序：分数降序，同分优先较新的 session；同一 session 内**行号相邻的命中合并**成一条。
- 返回 top 5，每项带 session ID、文件时间、命中角色，以及命中消息**前后各一条上下文**。
- 输出统一过 `clip_text`，受 `max_output_chars` 约束（原实现收了参数却没用，每条固定砍 500 字符）。
- 同步更新了 ADR-0004 相关描述：文件头注释（保留原决策记录 + 标注修订）、`schema()` 的 description
  （这是**模型可见**的，描述仍写着"本次会话"会误导调用）、架构文档。

### 3.2 Markdown 记忆写入安全（`MemoryManager`）

- `add/replace/remove` 的"读取 → 校验 → 写回"**全程持有 `FileLock`**（锁文件 `<memory-file>.lock`，
  与 `session_store.append` 的命名模式一致；`filelock` 本就是已有依赖）。
- 写盘改为**同目录临时文件 + 原子替换**；失败时清理临时文件，旧文件一个字节都不动。
- 读取不加锁，靠原子替换保证只读到完整版本（无中间态）。
- `memory_tool` 把写入异常转成工具错误返回，不让主循环崩掉。

### 3.3 动作级权限（新增机制）

权限原本是**工具级**的（`@tool(permission="auto")`），无法按参数区分。本次扩展：

- `@tool` 装饰器新增可选参数 `permission_fn(tool_call) -> "auto" | "ask" | "deny"`，优先级高于静态 `permission`。
- `ToolManager.get_permission(name, tool_call=None)`：有 `permission_fn` 且传了 `tool_call` 时按参数动态判定。
- `PermissionManager._resolve_level(name, tool_call)` 透传 `tool_call`。
- **未使用 `permission_fn` 的现有工具行为完全不变**（回落路径保留）。
- memory 工具落地为：`add` → AUTO（纯追加），`replace` / `remove` → ASK（不可逆，需确认）。

### 3.4 摘要失败的确定性兜底（`ContextManager`）

- 摘要默认**关闭**（`CONTEXT_SUMMARY_ENABLED=false`，`.env.example` 已是该值），关闭时完全不调 LLM。
- 开启后，异常 / 超时 / HTTP 错误 / 空响应统一视为失败，**不中断任务**，改为插入确定性摘录：
  原始目标（首条 user，≤800）→ 近期决定（末 3 条 user/assistant，各 ≤600）→ 报错现场（末 2 条含
  error/exception/failed/失败/报错 的 tool 结果，各 ≤500）→ 涉及文件（去重，≤8 个）。总长 ≤4000 字符，
  开头标注「仅作历史参考，以后续最新用户消息为准」。
- 失败**进缓存**（`_failure_cache`，10 分钟进程内冷却）——原实现失败不缓存，导致摘要一挂就每轮重试。
- `force=True`（手动 `/compact`）绕过冷却。
- 新增只读诊断：`last_compaction_mode`（none/truncated/summary/excerpt）、`last_compaction_error`、`last_dropped_count`。

### 3.5 上下文超限识别与单次重试

- `llm_client` 新增 `ContextLengthExceededError`；识别条件为 **HTTP 400/413 且响应体（小写）含
  `context_length_exceeded` 或 `maximum context length`**——只看状态码会把 tool JSON 解析失败等
  普通 400 误判成超限，白压缩一轮还原样报错。流式与非流式两条路径都覆盖。
- `MachineLoop` 捕获后调 `_recover_from_context_overflow()`：强制压缩 → 重试**一次**。
  三种失败均明确返回，绝不循环：未配压缩器 / 压缩后 token 未减少 / 二次仍超限。

## 4. 实施中发现并修复的三个隐藏缺陷（均不在原计划内）

1. **BM25 在极小语料上静默失效（最严重）**
   `rank_bm25` 的 IDF = `log((N - df + 0.5)/(df + 0.5))`。语料 2 条、关键词命中 1 条时 IDF = `log(1.5/1.5)` = 0，
   导致**明明存在的关键词也搜不到**，工具静默返回"没找到"。短会话、只有两三个 session 的工作区必踩，
   等于让跨会话召回在最常用场景失灵。
   对策：BM25 零命中时回退到 `_coverage_hits()`——按"命中了几个查询词"打分，不依赖语料规模。

2. **合并命中时上下文行重复渲染**
   `_format_hit()` 对相邻命中逐行取前后文，第 N 行与第 N+1 行都命中时中间行会各输出两遍 → 改用集合去重。

3. **上一次中断遗留的半截代码**
   `_retry_after_forced_compact` 的签名与调用点参数不一致，且缺 `count_tokens` / `ContextLengthExceededError`
   导入 → 重写为 `_recover_from_context_overflow()`，统一返回 `{"status": "ok"|"failed", ...}`。

另补一处漏网：计划要求 fallback 警告"向 CLI 显示"，但只 fire 了 Hook、CLI 未注册监听 → 警告静默丢失。
已在 `cli_ui.py` 补 `on_compaction_fallback`。

## 5. 破坏性变更（需要 review 时特别注意）

1. **摘要失败不再静默降级为纯截断**，而是插入一条 `[历史摘要]` 前缀的摘录消息。
   依赖旧行为的测试已改写（见下）。
2. **`memory` 工具的 `replace` / `remove` 从 AUTO 变为 ASK**，会触发 `permission_required` 挂起流程。
3. **`SessionStore.overwrite()` 被删除**（src 与 tests 均零引用，已验证）。
4. **`ToolManager.get_permission()` 增加第二个可选参数**（`tool_call=None`，向后兼容）。

## 6. 测试情况

新增 `tests/test_memory_compaction_resilience.py`（32 条，按改动块分组）：

- 跨会话召回：多 session 搜索、session ID/时间标注、同分新者优先、最近 10 个护栏、坏行跳过、
  相邻命中不重复、输出裁剪、无会话目录
- 记忆安全：并发 20 条不丢、写失败保旧文件且清理临时文件、动作级权限、`auto_approve` 放行、写入异常转工具错误
- 摘要兜底：失败冷却（不重复调 LLM）、`force` 绕过冷却、摘录结构、字符上限、tool 配对完整
- 上下文超限：400/413 关键词 → 领域异常、普通 400 → 仍是 HTTPError、
  流式异常透传、压缩后重试一次成功、无进展失败、二次仍超限失败、无压缩器失败
- 二次验收回归：记忆读取失败保留旧文件、召回严格字符上限、强制压缩警告 Hook、
  Timeout/HTTP 摘要兜底、批准/拒绝写入行为、压缩不改写 JSONL
- `SessionStore` 不再有 `overwrite` 方法

`tests/test_context_summary.py` 的 test_04 / test_05 已按新行为改写，并加强断言：
**去掉摘录消息后，其余部分必须与纯截断结果完全一致**（确保摘录只做加法，不影响近期消息切分）。

## 7. 遗留与后续建议

- 跨会话召回目前**未加时间衰减权重**（只按 BM25 分数排序 + 同分新者优先）。若实际使用中出现
  "老会话高分压过新会话"的排序质量问题，再考虑加权重。
- 摘要失败冷却是**进程内**的（与摘要缓存一致，不落盘），重启即失效——符合"不落盘"约定。
- 尚未提交到 `dev`，也未推送远端。
