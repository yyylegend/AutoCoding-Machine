# 自定义配置与数据说明

[返回首页](../README.md)

日常使用只需要首页的启动和切换命令。想调整角色、启用技能，或查找聊天记录时，再看这份说明。

## 自定义 Profile

内置的 `coding`、`review`、`companion` 可以直接按名称切换。教学示例放在 `examples/profiles/`，不会自动出现在 `/profile` 清单。

从带注释的示例开始：[只读审查](../examples/profiles/reviewer.yaml)、[陪伴角色](../examples/profiles/companion.yaml)。把要使用的示例复制到 `profile_configs/`，再修改名称和提示词：

```powershell
Copy-Item examples/profiles/reviewer.yaml profile_configs/my-review.yaml
uv run python -m src --profile profile_configs/my-review.yaml
```

```yaml
name: my-review
kind: review
prompt: |
  你是面向初学者的代码审查助手。
  解释问题的触发条件、代码证据和最小修改建议。
skills: []
context_budget: 16000
load_instructions: true
# model: 本地服务的模型ID
```

`/profile` 会列出内置预设和 `profile_configs/` 下有效的 YAML。自定义配置目前必须用路径切换，例如 `/profile profile_configs/my-review.yaml`；YAML 中的 `name` 用于显示和状态目录，尚不支持按该名称查找文件。示例也可以直接按 `examples/profiles/…yaml` 路径试用。

之前的 `companion-demo` 和 `beginner-review` 已移到示例目录；旧会话没有删除，加载原名称的示例仍使用同一状态目录。通用 Profile 的下一版需求见 [规划文档](plans/2026-09-06-universal-profiles.md)，下表描述的是当前可用字段。

| 字段 | 含义 |
| --- | --- |
| `name` | 必填。以小写字母开头，最多 64 字符，只含小写字母、数字、`_`、`-`；不能占用内置名称。它决定状态目录 |
| `kind` | `coding` / `review` / `companion`，默认 `coding`；决定默认能力及允许的工具范围 |
| `prompt` | 替换该类型的系统提示词；省略则使用内置提示词 |
| `tools` | 可选，只能从该类型已有工具中选择；省略则沿用默认工具。`review` 无法借此启用 Shell |
| `skills` | `null` 使用全部已发现 Skills；`[]` 不加载；名称列表只启用指定 Skills。省略则沿用该类型默认值 |
| `model` | 可选，覆盖当前 Profile 的模型 ID，主调用与摘要使用相同模型；连接地址和密钥继续从 `.env` 读取 |
| `context_budget` | 可选，输入 token 预算；不是模型完整窗口。请为最大输出和消息封装留出空间 |
| `load_instructions` | 是否读取全局和项目的 AGENTS.md / CLAUDE.md；Companion 默认关闭 |
| `trace_enabled` | 是否保存详细运行轨迹；默认 `false`。开启会额外生成 `runs/`，适合评测和排查问题 |

Skills 从 `~/.agents/skills/` 和 `<workspace>/.agents/skills/` 发现，项目级同名覆盖全局级。Profile 组装时筛选清单，搜索和加载工具共用该清单；修改技能目录后重新启动。修改自定义 YAML 后可用 `/profile <路径>` 重新加载变化后的配置。要让模型加载 Skills，需启用 `load_skill` 工具。

当前清单为空时（包括 `skills: []` 或没有发现技能），运行时不注册 `search_skills` 和 `load_skill`，模型看不到这两个入口，也不能执行它们。有可用技能且配置允许时才注册。旧调用入口遇到空清单会说明当前没有可用技能，不再提示换关键词重试。“当前未启用”不等于电脑上没安装技能；加载 Skill 也不会新增文件或命令权限。

### 记忆与会话位置

所有 Profile 的会话和 TUI 输入历史都放在统一的 Profile 目录下。详细运行记录默认关闭；默认 Coding 的旧文件会在启动时自动整理到新位置，项目记忆和用户记忆的兼容路径不变。

其他 Profile 使用：

```text
.autocoding/profiles/<name>/
  MEMORY.md
  USER.md
  sessions/<session-id>.jsonl  # 原始对话，供恢复与召回
  runs/<session-id>.jsonl      # 仅 trace_enabled=true 时生成的配置与诊断
  input_history                # 当前 Profile 的输入历史
```

默认 Coding 的会话位置是 `.autocoding/profiles/coding/sessions/`。旧版 `.autocoding/sessions/`、`.autocoding/runs/` 和 `.autocoding/input_history` 首次启动时会被移动到 `profiles/coding/` 对应目录；同名目标文件不会被覆盖。

同名 Profile 在同一工作区复用状态，换名称得到独立状态。这里提供的是状态命名空间，不是多用户权限系统或操作系统级沙箱；Coding 的命令能力仍应在可信工作区使用。

`/memory` 查看当前 Profile 的记忆。Companion 第一版复用现有记忆工具，支持添加、替换和删除条目；删除记忆条目不会清除原始聊天历史。人格一致性和长期对话效果仍需真实模型评测。

### 运行记录

默认不生成 `runs/`。需要做评测或排查问题时，在 Profile YAML 中设置 `trace_enabled: true`；此时诊断在 `.autocoding/profiles/<name>/runs/`，记录配置、Skills SHA-256、实际模型输入与响应、服务端用量、模型和工具耗时。普通聊天只保留 `sessions/<session-id>.jsonl`。

这些记录包含对话正文，本地 `.autocoding/` 已被 Git 忽略；不会额外保存 API key。它们便于分析和复现配置，不保证随机模型输出完全一致，也不支持自动重新执行历史工具。
