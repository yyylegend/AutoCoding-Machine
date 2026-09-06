# Architecture

本文描述当前代码实现。下一轮的目标架构见 [架构演进计划](plans/2026-09-05-runtime-boundaries.md)，其中的 `AgentSession`、`AgentRun` 和统一事件接口尚未实现。

未来需求统一放在 [plans 入口](plans/README.md)；通用 Profile、Goal / Todo、长上下文专项优化尚未实现，不能按规划中的接口调用。

## 当前结构

CLI 是外部交互入口，Coding、Review 与 Companion 使用同一运行时，由 `src/profiles/config.py` 决定能力组合。目前 CLI 仍创建工具、权限、模型适配器和上下文管理器，再交给 Factory 连接；Runtime 尚未完全隐藏组装细节。

```text
src/__main__.py
  → profiles/coding/cli.py：输入、命令、组件创建、会话视图
      → profiles/config.py：读取 Profile
      → runtime/factory.py：连接组件，创建 AgentRuntime
          → engine/machine_loop.py：模型与工具循环
          → runtime/：上下文、Skills、历史检索、记忆注入、Trace
          → common/model_adapter.py：模型调用与响应解析
          → profiles/coding/：工具实现与 Coding 完成验证
```

目录位置与适用场景并不完全相同：部分共用工具包装及终端代码仍在 `profiles/coding/`。`runtime/tools.py` 当前也会导入这里的工具模块，不能将其描述成已经独立发布的通用 SDK。

### 当前状态由谁持有

| 状态 | 当前持有者 |
| --- | --- |
| 当前 Profile 与配置切换 | CLI 的 `run_cli` 外层循环 |
| 当前 store、history、压缩视图、Plan Mode | CLI 的 `_run_profile` 局部变量 |
| 对原始会话的读写 | `SessionStore`；CLI 与 MachineLoop 调用它追加消息 |
| 消息组装与运行组件引用 | `AgentRuntime`、`RuntimeRegistry` |
| 取消信号、执行轮数 | CLI 创建 `CancellationToken`，MachineLoop 管理执行轮数 |
| 文件基线、验证版本、候选回答 | Coding 的 `CompletionGate` |
| 工具可用范围与资源 | `ProfileTools`、`ToolEnvironment`、`ToolManager` |
| 运行记录 | `RunTrace` 包装模型调用，并订阅部分 Hooks |

目前还没有单独的会话控制器或任务状态对象。`ToolEnvironment` 仍继承 `WorkspaceSandbox`；TUI 与日志使用部分共享 Hooks，但没有覆盖全部生命周期的统一事件协议。

## Profiles 与共享模块

`python -m src --profile <名称或YAML路径>` 在启动时读取不可变 Profile。配置解析拒绝未知字段、非法名称和超出类型范围的工具；TUI 的 `/profile` 在输入空闲时结束旧会话循环，重新组装 Profile 并开新会话；不在运行中的循环内替换组件，也不支持动态 Python 插件。

| 模块 | 唯一负责的事情 |
| --- | --- |
| `profiles/config.py` | 内置默认值、YAML 校验、Profile 状态目录 |
| `runtime/factory.py` | 创建运行时并连接组件；保留 `create_coding_runtime` 兼容入口 |
| `runtime/tools.py` | 按 Profile 注册工具，把记忆、Skills 和会话范围传给工具 |
| `runtime/skills.py` | 发现与筛选 Skills，搜索、加载、CLI 使用同一启动清单 |
| `runtime/memory.py` | 构造记忆路径与注入，工具只调用存储服务 |
| `runtime/history.py` | JSONL 历史检索，供工具和自动召回复用 |
| `runtime/context.py`、`context_selector.py`、`prompts.py` | 上下文预算、摘要、召回及指令注入 |
| `common/model_adapter.py` | 无界面的模型调用和响应解析 |
| `runtime/trace.py` | 独立诊断记录，不参与模型历史召回 |

Engine 不需要知道 Companion 的人格或 Coding 的提示词。Coding 保留完成验证门；Review 不注册写工具；Companion 不注册文件和 Shell 工具，并默认关闭 Coding 指令文件加载。

`ProfileTools` 以筛选后的技能清单决定是否注册技能入口：清单为空时，`search_skills` 和 `load_skill` 既不出现在模型工具定义中，也不能被执行。低层搜索工具仍处理空清单，明确区分“没有可用技能”和“关键词不匹配”。

所有 Profile 都将 sessions 和 input_history 放在 `.autocoding/profiles/<name>/` 下；runs 只有在 `trace_enabled: true` 时才创建。默认 Coding 的项目记忆仍兼容 `.autocoding/MEMORY.md`，用户记忆仍兼容全局 USER.md。首次启动会把旧的 `.autocoding/sessions`、`runs` 和 `input_history` 移到 `profiles/coding/`，只在目标不存在时移动文件。这是应用状态分离，不是操作系统级隔离。

模型 ID 覆盖同时影响主调用和摘要。显式输入预算优先；自定义模型查询自己的窗口，不复用默认模型的预算缓存。token 计数目前仍为通用 tokenizer 估算，Qwen 的精确适配留待专项优化。

旧 `profiles/coding/` 下的 Skills、上下文入口保留兼容导出，实际实现只有共享层的一份。终端渲染目前仍在该目录内，无界面调用不依赖它。

## TUI 与配置切换

`run_cli` 用外层循环管理 Profile，内层会话循环返回所选配置。切换清除旧 Plan Mode、权限对象和压缩视图，保留原始 JSONL。输入历史按 Profile 的状态目录保存，避免上下键带出其他配置的输入。

切换清单包含内置预设与工作区 `profile_configs/` 下的有效 YAML；`examples/profiles/` 只存教学示例，不参与自动发现。自定义配置通过路径加载，列表名称尚不作为别名解析。

`cli_input.py` 负责命令补全、按键和输入底栏；`cli_ui.py` 负责渐变 Banner、回复面板、工具参数卡片和状态展示。底栏所需的 token 估算只在进入输入前更新，不在每次按键重绘时重新计算。流式和非流式输出都使用 Markdown 面板，完成验证门继续决定内容何时展示。

## 调用流程

```text
User input
  → CLI calls AgentRuntime.build_messages
  → ContextSelector temporarily recalls relevant old-session context
  → MachineLoop asks the model
  → Permission and Hook checks
  → ToolManager executes one tool
  → Tool result returns to the model
  → CompletionGate checks validation evidence (Coding only)
  → Final reply or next tool call
```

## 模块

### `src/engine`

与具体入口无关的执行内核：契约、循环、上下文、权限、守卫、Hook、会话、记忆和工具注册。

### `src/runtime`

提供组装入口。`create_runtime()` 把 Engine 与所选 Profile 组合成 `AgentRuntime`，支持调用方注入组件。目前 CLI 与 Factory 都承担部分创建工作，尚未收敛为一个完整的会话创建入口。

### `src/profiles/coding`

Coding 专属系统提示词、完成验证、Plan Mode、沙箱和代码工具，以及保留原位置的 CLI 和终端流式渲染。

### `src/common`

跨模块基础能力：日志、OpenAI-compatible HTTP 客户端、无界面模型适配、文本裁剪和 Token 计数。

## 三层记忆模型

理解本项目的关键：同一段信息在不同层里的形态不一样，职责也不一样。

| 层 | 载体 | 谁写 | 特点 |
| --- | --- | --- | --- |
| 精选记忆 | `MEMORY.md` / `USER.md`（Markdown） | 模型通过 memory 工具 | 有容量上限，跨会话注入；写操作加锁 + 原子替换；权限按动作分级（`add` 自动，`replace`/`remove` 需确认） |
| 原始会话 | `.autocoding/profiles/<name>/sessions/*.jsonl` | CLI 与 MachineLoop 通过 SessionStore 追加 | 作为恢复对话的依据，不因压缩而改写 |
| 压缩视图 | 进程内消息列表 | ContextManager 生成，CLI 与循环使用 | 不替换原始会话；发送给模型的视图可出现在独立诊断记录中 |

压缩会丢细节，但丢掉的内容始终躺在 JSONL 里，模型可以用 `recall_history` 跨会话找回
（扫描当前 Profile 最近 10 个 session，BM25 检索；语料过小时 BM25 的 IDF 会退化为 0，
此时自动改用关键词覆盖匹配兜底，避免"明明有却搜不到"）。

## 自动上下文选择

`ContextSelector` 在每次模型调用前，以最新用户消息为查询，从旧 session 自动召回相关历史：

- 使用与 `recall_history` 相同的 BM25 检索实现，不额外调用 LLM；
- 排除当前 session，默认至少命中 2 个查询词，单词查询自动降为命中 1 个；
- 最多注入 2 条、历史正文不超过 2000 字符；同一用户消息在工具循环中复用缓存；
- 召回结果只是本次请求的临时 system 消息，不追加到工作 messages 或原始会话 JSONL；开启运行记录时，实际请求视图会保存到 runs 目录；
- 检索失败时记录 warning 并跳过，不阻断 Agent 主任务。

Profile 版的自动召回与 `recall_history` 使用同一个会话目录；`/resume` 不允许传入路径跳转至其他目录。工具注册表存在时，权限管理器不再用旧默认表放行未注册工具。

## 诊断记录与原始会话

`runs/<session-id>.jsonl` 保存配置、Skills 内容指纹、实际请求视图、模型响应及用量、模型调用与工具耗时。`/resume` 同时切换日志目标。没有 SessionStore 的程序调用不自动创建诊断文件。

诊断记录可以包含自动召回、压缩和验证提示，原始会话仍只保留对话流水，两者不会混合检索。日志写入失败不会阻断任务。记录可以帮助检查当时的配置与输入，但不承诺模型输出或工具副作用可确定性回放。

## 上下文摘要与超限的失败策略

- Profile 设置 `context_budget` 时直接使用该输入预算，调用方负责预留输出空间。未设置时：默认模型按 `CODING_CONTEXT_LENGTH` → `/models` 元数据 → 128K 默认窗口解析；覆盖模型 ID 时单独查询该模型元数据，失败则使用默认窗口。由窗口自动计算的输入预算最多占 80%，并预留最大输出和估算误差空间。
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

1. **工具是模型请求操作环境的入口。** 模型发起的工具调用由 ToolManager 执行；会话、输入历史及诊断的持久化由宿主程序负责。
2. **权限与执行分离。** PermissionManager 决定是否执行，工具只负责自身行为。
   权限支持按调用参数动态判定（如 memory 工具按 `action` 区分），静态工具行为不变。
3. **Profile 负责组合。** Engine 不依赖 Coding Agent，Coding Profile 在 Runtime seam 注册专属能力。
4. **会话使用追加写。** JSONL 保留原始消息流水，压缩只影响运行时上下文。
5. **安全默认拒绝。** 未注册工具、保护路径和异常检查不会静默放行。
6. **Coding 的代码修改完成需要证据。** Gate 跟踪写工具触碰的文件及修改后的验证；这不等于独立验收任务正确性。Review 与 Companion 不使用该验证门。
