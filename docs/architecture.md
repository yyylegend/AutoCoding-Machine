# Architecture

## 目标

公开版只呈现一个 Coding Agent。CLI 是外部交互入口，Runtime 是内部深模块，隐藏模型循环、上下文、权限、记忆和工具组装细节。

## 调用流程

```text
User input
  → CLI assembles messages
  → ContextSelector temporarily recalls relevant old-session context
  → MachineLoop asks the model
  → Permission and Hook checks
  → ToolManager executes one tool
  → Tool result returns to the model
  → CompletionGate checks post-change validation evidence
  → Final reply or next tool call
```

## 模块

### `src/engine`

与具体入口无关的执行内核：契约、循环、上下文、权限、守卫、Hook、会话、记忆和工具注册。

### `src/runtime`

提供组装 seam。`create_coding_runtime()` 把 Engine 与 Coding Profile 组合成可运行对象，调用方不需要了解内部依赖顺序。

### `src/profiles/coding`

Coding Agent 的实现：系统提示词、LLM adapter、CLI、Plan Mode、沙箱、技能发现和代码工具。

### `src/common`

少量跨模块基础能力：日志、OpenAI-compatible HTTP 客户端和 Token 计数。

## 三层记忆模型

理解本项目的关键：同一段信息在不同层里的形态不一样，职责也不一样。

| 层 | 载体 | 谁写 | 特点 |
| --- | --- | --- | --- |
| 精选记忆 | `MEMORY.md` / `USER.md`（Markdown） | 模型通过 memory 工具 | 有容量上限，跨会话注入；写操作加锁 + 原子替换；权限按动作分级（`add` 自动，`replace`/`remove` 需确认） |
| 原始会话 | `<workspace>/.autocoding/sessions/*.jsonl` | MachineLoop 逐条追加 | **只增不减**（代码中不存在覆盖历史的方法），是唯一真相源 |
| 压缩视图 | 进程内消息列表 | ContextManager | 按 Token 预算压缩，只影响当次请求；不落盘、不改写 JSONL |

压缩会丢细节，但丢掉的内容始终躺在 JSONL 里，模型可以用 `recall_history` 跨会话找回
（扫描工作区最近 10 个 session，BM25 检索；语料过小时 BM25 的 IDF 会退化为 0，
此时自动改用关键词覆盖匹配兜底，避免"明明有却搜不到"）。

## 自动上下文选择

`ContextSelector` 在每次模型调用前，以最新用户消息为查询，从旧 session 自动召回相关历史：

- 使用与 `recall_history` 相同的 BM25 检索实现，不额外调用 LLM；
- 排除当前 session，默认至少命中 2 个查询词，单词查询自动降为命中 1 个；
- 最多注入 2 条、历史正文不超过 2000 字符；同一用户消息在工具循环中复用缓存；
- 召回结果只是本次请求的临时 system 消息，不追加到工作 messages，也不写入 JSONL；
- 检索失败时记录 warning 并跳过，不阻断 Agent 主任务。

## 上下文摘要与超限的失败策略

- 完整上下文窗口按 `CODING_CONTEXT_LENGTH` 显式配置 → 供应商 `/models` 元数据 →
  128K 保守默认值的顺序解析；输入预算最多使用窗口的 80%，并额外预留最大输出和估算误差空间。
- 摘要默认**关闭**（`.env` 的 `CONTEXT_SUMMARY_ENABLED=false`）：不调用 LLM，只做安全截断。
- 开启后，异常、超时、HTTP 错误、空响应统一视为失败，但**不中断任务**：
  改为插入一段确定性摘录（原始目标 / 近期决定 / 报错现场 / 涉及文件，总长 ≤ 4000 字符，
  开头标注「仅作历史参考」）。
- 同一批旧消息失败后进入 10 分钟进程内冷却，冷却期内不再重复请求模型；
  手动 `/compact`（`force=True`）可绕过冷却立即重试。
- 上下文超限（HTTP 400/413 且响应体含 context-length 关键词）抛领域异常，
  MachineLoop 强制压缩后**只重试一次**；压缩无进展或二次仍超限则明确失败，绝不无限循环。

## 完成证据门

`CompletionGate`（Coding Profile 专属，`src/profiles/coding/completion_gate.py`）
不相信模型单独声明 done，只认两条硬证据：

- **文件净变化**：`pre_tool` Hook 在写工具首次触碰路径前拍基线快照
  （存在性 + SHA-256，分块计算）；`evaluate` 时重读全部跟踪路径与基线比较。
  临时文件建了又删、文件改回原样都不算净变化；读取失败保守当作有变化。
- **新鲜验证**：最后一次真实修改之后，`run_test` 成功或 token 级白名单命中的
  `run_bash` 命令（pytest / lint / build 类）以退出码 0 完成。先测试后修改
  属于过期证据；验证后再修改自动失效；消失的临时写入不影响已有验证。

候选回答（candidate response）状态机：

- 模型第一次给出无 ToolCall 的文本只是**候选回答**，与当时的有效修改版本绑定；
- 证据不足时保留候选，插入运行时验证提示（synthetic nudge）继续循环；
  nudge 和候选回答都是内部脚手架，**不写入 Session JSONL**；
- 验证通过后复用原候选回答并附验证标记，模型的验证回执不得顶替实质内容；
  期间发生新的真实净修改则旧候选作废，等待新候选；
- 连续两次无证据返回 `verification_required`，但候选回答 + 未验证标记
  照样交付；轮数耗尽同样交付 pending candidate，回答不丢失。

流式展示两阶段提交：`StreamingAdapter` 依据 `should_publish_stream()` 决定
正常流式渲染或静默缓冲（token 照收、不建持久面板）；`last_streamed` 只表示
最终回答已持久展示。用户每个任务最多看到一份持久最终回答。

`read_file` 支持 `start_line` / `end_line`（1-based，含端点）分页读取长文件，
带真实行号和续读建议；长文件不再需要临时脚本。

## 关键设计选择

1. **工具是模型唯一的副作用入口。** 所有文件和命令操作都经过 ToolManager。
2. **权限与执行分离。** PermissionManager 决定是否执行，工具只负责自身行为。
   权限支持按调用参数动态判定（如 memory 工具按 `action` 区分），静态工具行为不变。
3. **Profile 负责组合。** Engine 不依赖 Coding Agent，Coding Profile 在 Runtime seam 注册专属能力。
4. **会话使用追加写。** JSONL 保留原始消息流水，压缩只影响运行时上下文。
5. **安全默认拒绝。** 未注册工具、保护路径和异常检查不会静默放行。
6. **完成需要证据。** 模型的 done 只是候选状态，文件净变化 + 新鲜验证才允许提交。
