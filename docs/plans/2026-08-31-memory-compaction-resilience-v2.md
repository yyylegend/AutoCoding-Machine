# AutoCoding Machine 记忆与压缩韧性改进计划（修订版 v2）

> v1 由 GPT 生成，2026-08-31 经代码级审查后修订。所有修订点均标注决策来源。
> 实施分支：`dev`（当前工作区干净，与假设一致）。

## 实施状态：已完成（2026-08-31）

`uv run --locked pytest` → **262 passed**（原有 230 + 新增 32）。

二次验收后补齐：流式超限异常透传、记忆读取失败保护、召回严格字符上限、
强制压缩警告 Hook，以及对应的 Timeout/HTTP、权限批准/拒绝和 JSONL 不变回归测试。

实施中额外修复的三个缺陷（均不在原计划内，已补测试）：

1. **BM25 在极小语料上静默失效**（最严重）：`rank_bm25` 的 IDF 为 `log((N - df + 0.5)/(df + 0.5))`，
   语料 2 条、关键词命中 1 条时 IDF = 0 —— **明明存在的关键词也搜不到**。
   短会话和只有两三个 session 的工作区必踩，等于让跨会话召回在最常用场景失灵。
   对策：BM25 零命中时回退到"关键词覆盖数"匹配（`_coverage_hits`），不依赖语料规模。
2. **合并命中时上下文行重复渲染**：`_format_hit` 对相邻命中逐行取前后文，
   第 N 行和第 N+1 行都命中时会把中间行各输出两遍 → 改用集合去重。
3. **断线遗留的半截代码**：`_retry_after_forced_compact` 签名与调用点不一致、
   缺 `count_tokens` / `ContextLengthExceededError` 导入 → 重写为
   `_recover_from_context_overflow`，统一返回 `{"status": "ok"|"failed", ...}`。

另补一处漏网点：计划要求 fallback 警告"向 CLI 显示"，但只 fire 了 Hook 事件、
CLI 没注册监听 → 警告静默丢失。已在 `cli_ui.py` 补 `on_compaction_fallback`。

## Summary

在保持 JSONL + Markdown 轻量架构的前提下，完成跨会话召回、召回上下文、记忆并发安全、写入确认，以及压缩失败兜底。
原始 JSONL 始终作为事实来源，不引入 SQLite、向量库或外部 Memory Provider。参考 Hermes 压缩器的失败冷却、静态 handoff 和单次恢复思路，独立实现，不复制其源码。

## 修订记录（相对 v1）

| # | 修订 | 原因 | 决策 |
|---|------|------|------|
| 1 | 新增：同步 ADR-0004 相关注释与模型可见描述 | recall_history 头注释与 schema description 都写着「只搜当前会话」，改跨会话必须同步，否则实现与文档打架 | 审查发现（必改项） |
| 2 | memory 权限从「全部改 ask」改为「remove/replace 改 ask，add 保持 auto」 | 全部 ask 会在长任务里频繁打断；不可逆操作管住即可 | 用户拍板 |
| 3 | 新增：删除 `SessionStore.overwrite()` | 零调用死代码（src 与 tests 均已验证），功能与「JSONL 事实来源」承诺矛盾 | 用户拍板 |
| 4 | 跨会话召回增加护栏：只扫最近 10 个 session | 每次召回全量重建 BM25 索引，成本随 session 数线性涨 | 用户拍板 |
| 5 | context-length 识别加响应体关键词匹配 | 只看 400/413 太宽，会把 tool JSON 解析失败等普通 400 误判成上下文超限 | 审查发现（必改项） |
| 6 | 新增：修正 `permission_manager.py` 头部过期注释 | 注释称「ASK 按 AUTO 执行」，但 MachineLoop 已实装 permission_required 暂停流程 | 审查发现 |

## Key Changes

### 1. 跨会话历史召回

- 保持 `recall_history(query)` 工具接口不变，从「只搜最新 JSONL」改为扫描当前工作区 sessions 目录下**最近 10 个** session（按 mtime 从新到旧，`list_sessions()` 现成可复用；上限常量放模块顶部，可调）。
- 继续使用现有中英文分词（中文 bigram + 英文整块）和 BM25，不增加新依赖。时间衰减加权不做（未来可选）。
- 每条消息的检索语料包含 `content` 和 `tool_calls`，跳过损坏 JSONL 行（沿用现有逻辑）。
- 正分结果按 BM25 分数排序，**同分时优先较新的 session**；合并同一 session 中相邻的重复命中。
- 最多返回 5 个命中，每项包含 session ID、文件时间、命中角色，以及命中消息前后各一条上下文。
- 最终输出统一经过 `clip_text` 按 `max_output_chars` 裁剪——**这是修复项**：当前实现收了 `max_output_chars` 参数但没用，每条固定砍 500 字符。
- 【修订 1】同步更新三处描述：
  - `recall_history.py` 头注释：ADR-0004 的「只搜当前会话」标注为已修订（2026-08-31，跨会话 + 最近 10 个上限），保留原决策记录；
  - `schema()` 的 description：改为「从本工作区的近期会话历史中搜索」（模型可见，直接影响调用质量）；
  - `docs/architecture.md` / README 中涉及召回范围的表述。

### 2. Markdown 记忆写入安全

- `MemoryManager.add/replace/remove` 的「读取、校验、写回」全过程使用 `<memory-file>.lock` 文件锁（复用 `filelock`，已有依赖；命名与 `session_store.append` 的 `<file>.lock` 模式一致），避免并发会话互相覆盖。
- 写入同目录临时文件，成功后原子替换；写入失败时保留旧文件并清理临时文件（模式与 `SessionStore.overwrite` 的 tmp+replace 一致，但该处理逻辑保留在本改动中）。
- 普通读取不加锁，依赖原子替换保证只读到完整版本。
- 【修订 2】权限分级到动作级：
  - `add` 保持 `auto`；
  - `replace` / `remove` 改为 `ask`（不可逆操作必须确认）。
  - 实现方式：`@tool` 装饰器新增可选参数 `permission_fn(tool_call) -> "auto" | "ask"`（按 arguments 动态判定）；`PermissionManager._resolve_level` 在工具注册时优先读 `permission_fn`，无则回落到字符串权限 → tool_defaults 表 → DENY，**现有工具行为完全不变**。
  - 同步更新回归锁测试 `test_memory_tool.py::test_permission_manager_wiring_allows_memory`（当前断言 memory 一律 AUTO）；不传 tool_manager 时 memory 落入 tool_defaults 表外的 DENY 行为保持不变。
  - 【修订 6】顺手修正 `permission_manager.py:16-17` 的过期注释（「ASK 按 AUTO 执行」→ 已实装确认流程）。

### 3. 摘要失败的确定性兜底

- 保持 `CONTEXT_SUMMARY_ENABLED=false` 为默认值（`.env.example` 已确认是 false）；关闭时不调用 LLM，继续执行现有安全截断。
- 开启摘要后，将异常、超时、HTTP 错误、无 choices、空白内容统一视为摘要失败。
- 摘要失败时不终止任务，生成最多 4000 字符的确定性历史摘录：
  - 第一条非空用户消息作为原始目标，最多 800 字符；
  - 最后三条 user/assistant 消息作为近期决定，每条最多 600 字符；
  - 最后两条包含 `error`、`exception`、`failed`、`失败` 或 `报错` 的工具结果，每条最多 500 字符；
  - 最多保留 8 个去重后的文件路径；
  - 摘录开头明确标记「仅作历史参考，以后续最新用户消息为准」。
- 摘录作为一条 `role="user"` 消息（沿用现有 `[历史摘要]` 前缀风格）放在 system 注入之后、近期消息之前；真实消息的保留/切分仍走现有 `_find_safe_boundary`，tool call/result 配对完整。
- 原始 JSONL 不覆盖、不压缩；丢失的详细内容仍可通过 `recall_history` 找回。

### 4. 防止重复超时与上下文溢出

- 为摘要失败按旧消息指纹记录 10 分钟进程内冷却（与现有 `_summary_cache` 同款 md5 指纹；**失败也进缓存**，这是当前缺陷：失败不缓存导致每轮重试）；冷却期内直接复用确定性摘录，不再重复请求模型。
- 手动 `/compact` 的 `force=True` 绕过冷却，允许用户立即重试摘要。
- 不做「摘要模型失败后再调用同一个模型」的无效重试；当前项目没有独立辅助模型。
- 【修订 5】LLM 客户端识别 context-length 错误：HTTP 400/413 **且**响应体（小写匹配）包含 `context_length_exceeded` 或 `maximum context length` 时，抛出明确领域异常 `ContextLengthExceededError`（定义在 `src/common/llm_client.py`，流式与非流式两条路径都覆盖）；其余 400/413 维持普通 `requests.HTTPError`。
- MachineLoop 捕获该异常后执行一次强制压缩（`maybe_compact(force=True)`）并重试当前模型调用一次；若压缩没有减少上下文或第二次仍超限，则停止重试并返回清晰错误，禁止无限循环。
- `ContextManager.maybe_compact()` 的返回类型保持 `list`，新增只读诊断状态 `last_compaction_mode`、`last_compaction_error`、`last_dropped_count`。
- fallback 或强制重试通过新 Hook 事件（`compaction_fallback`）向 CLI 显示一次警告，现有 `compacted` Hook 保持兼容。

### 5. 删除 SessionStore.overwrite()【修订 3，新增】

- 删除 `SessionStore.overwrite()` 方法（src 与 tests 零引用，已验证）。
- 在 `session_store.py` 头注释中把「JSONL 只增不减（ADR-0002）」从约定升格为**结构性保证**：代码里不存在任何覆盖历史的方法。

### 6. 文档同步

- 更新 README / `docs/architecture.md`，说明三层模型：Markdown 精选记忆、JSONL 原始会话、上下文压缩视图。
- 明确摘要默认关闭、失败时使用确定性摘录、原始历史始终可召回。
- `.env.example` 保持 `CONTEXT_SUMMARY_ENABLED=false` 与 `MEMORY_ENABLED=true`（现状即是，确认即可）。

## Test Plan

- 跨多个 JSONL session 搜索中英文关键词，验证排序、同分新者优先、session 信息及前后文。
- 验证超过 10 个 session 时只扫最近 10 个；损坏 JSONL、空 session、零命中、相邻重复命中及输出超限（`max_output_chars` 真正生效）。
- 并发添加两条记忆不会丢失任何一条；模拟临时文件写入失败时旧文件保持完整。
- 验证 `memory` 动作级权限：`replace`/`remove` 进入 ASK 挂起流程（批准后执行、拒绝后不执行），`add` 自动执行；`auto_approve=True` 时 ASK 放行；不传 tool_manager 时 memory 仍 DENY（回归锁更新）。
- 模拟摘要超时、HTTP 错误、空响应和异常，验证确定性摘录结构、字符上限（800/600/500/4000）及 tool 配对。
- 验证失败冷却阻止重复 LLM 调用（失败进缓存），`force=True` 可以绕过冷却。
- 验证默认关闭摘要时完全不调用 summarizer。
- 验证 context-length 识别：匹配关键词的 400/413 抛 `ContextLengthExceededError`；不含关键词的 400 仍是普通 HTTPError；强制压缩后只重试一次，无压缩进展或再次溢出时明确失败。
- 验证 `overwrite()` 删除后 `uv run pytest` 无残留引用。
- 验证所有压缩路径都不修改原始 JSONL。
- 最终运行 `uv run pytest`，全部原有与新增测试通过。

## Assumptions

- 从当前 `dev` 分支实施，完成后推送 `dev`，不直接修改 `main`。
- 不引入 SQLite、向量检索、独立摘要模型或外部 Memory Provider。
- 采用已确认的「摘录后继续」失败策略与「remove/replace 确认、add 自动」权限策略。
- 参考仓库未检测到可识别的 LICENSE，因此只复用公开设计思想并独立编码，不逐段复制源码。
