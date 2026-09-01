# Architecture

## 目标

公开版只呈现一个 Coding Agent。CLI 是外部交互入口，Runtime 是内部深模块，隐藏模型循环、上下文、权限、记忆和工具组装细节。

## 调用流程

```text
User input
  → CLI assembles messages
  → MachineLoop asks the model
  → Permission and Hook checks
  → ToolManager executes one tool
  → Tool result returns to the model
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

## 上下文摘要与超限的失败策略

- 摘要默认**关闭**（`.env` 的 `CONTEXT_SUMMARY_ENABLED=false`）：不调用 LLM，只做安全截断。
- 开启后，异常、超时、HTTP 错误、空响应统一视为失败，但**不中断任务**：
  改为插入一段确定性摘录（原始目标 / 近期决定 / 报错现场 / 涉及文件，总长 ≤ 4000 字符，
  开头标注「仅作历史参考」）。
- 同一批旧消息失败后进入 10 分钟进程内冷却，冷却期内不再重复请求模型；
  手动 `/compact`（`force=True`）可绕过冷却立即重试。
- 上下文超限（HTTP 400/413 且响应体含 context-length 关键词）抛领域异常，
  MachineLoop 强制压缩后**只重试一次**；压缩无进展或二次仍超限则明确失败，绝不无限循环。

## 关键设计选择

1. **工具是模型唯一的副作用入口。** 所有文件和命令操作都经过 ToolManager。
2. **权限与执行分离。** PermissionManager 决定是否执行，工具只负责自身行为。
   权限支持按调用参数动态判定（如 memory 工具按 `action` 区分），静态工具行为不变。
3. **Profile 负责组合。** Engine 不依赖 Coding Agent，Coding Profile 在 Runtime seam 注册专属能力。
4. **会话使用追加写。** JSONL 保留原始消息流水，压缩只影响运行时上下文。
5. **安全默认拒绝。** 未注册工具、保护路径和异常检查不会静默放行。
