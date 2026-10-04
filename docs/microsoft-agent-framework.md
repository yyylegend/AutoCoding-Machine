# Microsoft Agent Framework：从一次代码审查看懂 Harness

[返回首页](../README.md) · [本项目架构](architecture.md) · [Harness Cookbook](harness-cookbook.md)

这是一篇对照阅读笔记，依据 2026-09-26 查阅的微软官方文档。读完后，你应该能说清 Agent、Harness、Session 和 Workflow 各做什么，并知道这些概念在本项目里对应到哪里。这里的微软示例属于另一套 Python 包，不是本项目已经接入的依赖。

## 从一个任务开始

假设用户说：“检查 README 有没有讲清楚如何配置模型。”应用给 Agent 注册了读取文件的工具。模型可以提出“读取 README.md”，但它自己不能打开磁盘文件；运行时负责检查这个调用、执行工具、把结果交回模型，让模型继续完成回答。

如果模型随后要求修改 README，应用还要按工具权限决定是否询问用户。需要确认时，任务暂停；用户作出决定后才继续。这个例子说明 Harness 的核心工作：把模型提出的下一步变成受控的执行过程。[微软 Harness 架构](https://learn.microsoft.com/en-us/agent-framework/concepts/harness)、[工具审批说明](https://learn.microsoft.com/en-us/agent-framework/agents/tools/tool-approval)。

```text
用户任务 + 会话
    ↓
组装指令、历史和本轮需要的上下文
    ↓
模型回答 ──→ 已完成：返回结果
    │
    └──→ 请求工具：检查权限 → 执行或等待用户决定
                          ↓
                   工具结果进入会话 → 再次调用模型
```

这张图是执行过程的简化示意。实际可用的工具和审批规则，由具体应用配置；`read_file`、`write_file` 是本项目的工具例子，不是说微软的最小示例会自动拥有这两个工具。

## Agent、Harness 和 Workflow 怎么分

微软将 Agent、Harness Agent、Workflows 和 Integrations 列为主要部分。可以先这样理解：[官方概览](https://learn.microsoft.com/en-us/agent-framework/overview/?pivots=programming-language-python)。

| 概念 | 负责的事 | 适合的例子 |
| --- | --- | --- |
| Agent | 接收任务，使用模型和已注册工具，决定下一步并给出结果 | “找出这个项目值得检查的风险” |
| Harness | 组织模型与工具的循环、上下文、会话、审批等运行能力 | 模型连续读几个文件，必要时等待用户批准 |
| Workflow | 让代码预先规定执行步骤和分支 | “扫描 → 人工审核 → 生成固定格式报告” |
| Integrations | 连接模型、工具、记忆服务等外部能力 | 换模型服务或接入一个记忆后端 |

微软的 Python `create_harness_agent(...)` 会把已有的 Agent Framework 构件组合起来，返回一个可运行的 `Agent`。它提供一些默认能力，例如 Todo、运行模式和文件记忆；也允许按需调整。Workflow 适合执行路径已经明确、需要协调多个步骤或组件的任务。开放式代码审查通常可以先交给一个 Agent；固定审核流水线若用几个普通函数就能清楚完成，也可以直接写函数。[Harness 架构](https://learn.microsoft.com/en-us/agent-framework/concepts/harness)、[Workflow 概念](https://learn.microsoft.com/en-us/agent-framework/concepts/workflows/)、[官方使用建议](https://learn.microsoft.com/en-us/agent-framework/overview/?pivots=programming-language-python)。

## Session、上下文和审批在何处起作用

**Session 是一段对话的状态容器。** 多次运行复用同一个 Session，才能接上之前的历史和会话状态。微软 Python Harness 默认使用内存中的历史提供者；需要跨进程恢复时，应另行保存或恢复会话。它还要求工具循环中的每次模型调用都保存历史，而非只在整个任务结束时保存。[Session 文档](https://learn.microsoft.com/en-us/agent-framework/concepts/agents/conversations/session)。

**Context Provider 决定本轮还需要带什么信息。** 比如在调用前查找相关记忆、补充消息，或在运行后提取值得保存的内容。微软 Python 的 `HistoryProvider` 也是 Context Provider 的一种，专门处理对话历史；外部长期记忆只是这个扩展点的另一种可能用途。[Context Providers 文档](https://learn.microsoft.com/en-us/agent-framework/concepts/agents/conversations/context-providers)。

**Middleware 可以介入运行过程。** 微软提供 Agent、工具调用和模型调用等不同位置的中间件；它们可以检查或改变输入、结果和控制流程。单纯接收事件的观察回调只用于通知。我们项目的 `on_event` 属于后者，权限决定仍由调用方显式提交。[Middleware 文档](https://learn.microsoft.com/en-us/agent-framework/concepts/agents/middleware/)、[本项目权限示例](harness-cookbook.md#3-加一个自己的工具)。

在微软的 Python 工具审批流程中，运行结果可包含待用户处理的请求；调用方取得决定后，把审批响应交回 Agent 继续运行。我们项目采用 `result["permission_request"]` 加同一个 Run 的 `resolve_permission(approved=...)`。两者都让应用负责收集人的决定，但具体 API 不同。[微软工具审批](https://learn.microsoft.com/en-us/agent-framework/agents/tools/tool-approval)、[本项目事件约定](adr/0001-harness-event-contract.md)。

## 看一眼微软 Python 公共入口

下面的示例只展示创建 Agent、复用 Session 和运行一次任务。它没有注册项目文件工具，因此不能凭这段代码审查当前目录。运行前需在独立 Python 环境安装 `agent-framework` 与 `agent-framework-openai`，并通过环境变量提供 `OPENAI_API_KEY`；模型名可换成自己可用的模型。代码依据[微软 Harness 示例](https://learn.microsoft.com/en-us/agent-framework/concepts/harness)和[OpenAI 提供者说明](https://learn.microsoft.com/en-us/agent-framework/integrations/by-component/model-providers/openai)。

```python
import asyncio

from agent_framework import create_harness_agent
from agent_framework.openai import OpenAIChatClient


async def main():
    agent = create_harness_agent(
        client=OpenAIChatClient(model="gpt-4o-mini"),
        disable_web_search=True,
    )
    session = agent.create_session()
    response = await agent.run(
        "用一句话解释 Agent 和 Harness 的分工。",
        session=session,
    )
    print(response.text)


if __name__ == "__main__":
    asyncio.run(main())
```

注意这段代码使用 `await`，而本项目现有公共入口是同步的。这里展示的是微软框架的用法，没有在本仓库安装依赖或调用真实模型。

## 再对照我们的 Coding-Agent

下面是概念对照，不表示两个项目的类和 API 可以互换。当前实现以[架构说明](architecture.md)和代码为准。

| 问题 | Microsoft Agent Framework | 本项目当前实现 |
| --- | --- | --- |
| 如何开始 | `create_harness_agent(client=...)` 得到 Agent | `open_harness_session(workspace, model_fn, ...)` 得到 `AgentSession` |
| 如何运行一轮 | `await agent.run(..., session=session)` | `session.begin_run(...).start()`；单次任务由 `AgentRun` 持有 |
| 会话存在哪里 | Python Harness 默认内存历史，可替换 History Provider | 默认 JSONL `SessionStore` 是原始会话与恢复依据 |
| 记忆如何进入上下文 | Context Provider 在运行前后参与处理 | `RequestView` 组装本轮视图；可选 `MemoryProvider.recall`，写回使用单独的 `MemoryWriter` |
| 工具需要人批准时 | 结果给出待处理请求，再把决定交回 Agent | 返回安全的 `permission_request`，调用 `run.resolve_permission(approved=...)` |

本项目的 `MemoryProvider` 是刻意缩小的外部召回接口，不等于微软的通用 Context Provider。腾讯 MemoryCore Adapter 和显式写回已有本地实现；插件安装、任意 Python 插件加载与独立 Workflow 仍不属于当前能力。[记忆契约](../src/auto_coding_machine/memory/external.py)、[模块化 Harness 路线图](plans/2026-09-22-modular-harness.md)。

## 读完后可以自问

1. 换一个外部记忆服务后，为什么原始会话 JSONL 仍需要保留？
2. 收到 `permission_required` 事件，是否意味着工具已经获准执行？
3. “让模型自由检查代码”和“按固定步骤生成审核报告”，哪个更需要 Workflow？

参考答案：JSONL 保存可恢复的原始对话，外部记忆负责跨会话召回；权限事件只报告正在等待决定；固定步骤更接近 Workflow，但简单流程用普通函数即可。

如果这三个问题能说清，接着读[本项目 Harness Cookbook](harness-cookbook.md)，亲手走一次工具调用与审批；想看微软更完整的能力，再读[官方 Harness 文档](https://learn.microsoft.com/en-us/agent-framework/concepts/harness)。
