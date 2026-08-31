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

## 关键设计选择

1. **工具是模型唯一的副作用入口。** 所有文件和命令操作都经过 ToolManager。
2. **权限与执行分离。** PermissionManager 决定是否执行，工具只负责自身行为。
3. **Profile 负责组合。** Engine 不依赖 Coding Agent，Coding Profile 在 Runtime seam 注册专属能力。
4. **会话使用追加写。** JSONL 保留原始消息流水，压缩只影响运行时上下文。
5. **安全默认拒绝。** 未注册工具、保护路径和异常检查不会静默放行。
