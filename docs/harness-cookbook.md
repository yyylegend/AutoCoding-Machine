# Harness Cookbook

这里按由浅入深的顺序，记录如何把 Harness 接到自己的程序里。第一次使用的话，先完成[快速入门](harness-quickstart.md)；本页重点讲具体做法。

项目目前通过本地 editable 依赖使用，尚未发布到 PyPI。示例使用稳定的公共导入入口；不从内部模块拼装执行循环。

先记住这条主线：`Profile` 选择能力，`Session` 管一段对话，`Run` 执行一次任务。

```text
Profile + 模型 + 工具 → open_harness_session() → begin_run() → start() → 结果
```

## 1. 先运行一个只读 Review

如果模型配置已经准备好，可以在 Coding-Agent 仓库根目录先运行现成示例：

```powershell
uv run python -m examples.harness_review . "检查这个项目的错误处理"
```

示例使用 `review` Profile，只注册读取工具；它不会自动批准权限，也不会修改项目。完整调用代码在 [`examples/harness_review.py`](../examples/harness_review.py)。

## 2. 接入自己的模型

Harness 不要求模型厂商，但要求调用函数把模型回复转换成 `AgentResponse`。下面是 `model_fn` 内的返回值片段，不是完整脚本。普通最终回答可以这样表示：

```python
from auto_coding_machine import AgentResponse

return AgentResponse(content="检查完成。", done=True)
```

如果模型希望使用工具，回复里还要带 `ToolCall`，例如：

```python
from auto_coding_machine import AgentResponse, ToolCall

return AgentResponse(tool_calls=[
    ToolCall(
        id="call-1",
        name="read_file",
        arguments={"path": "README.md"},
    )
])
```

`done=True` 表示模型确认任务已完成；只有文字不代表任务已经完成。使用自己的模型 SDK 时，由调用方把工具定义传给 SDK，并把返回的工具调用转换成 `ToolCall`。如果使用 OpenAI-compatible 服务，可参考 [`ModelAdapter` 的实际接线](../examples/harness_review.py)。

## 3. 加一个自己的工具

工具是一个提供 `schema()` 和 `execute()` 的 Python 对象。下面的例子用一个问候工具演示接线；模型是固定回复，不需要 API Key。

把下面的代码保存为 `greet_demo.py`。为避免启动时查询真实模型服务的上下文窗口，在 Harness 项目目录运行前先设置本地输入预算：

```powershell
$env:CODING_CONTEXT_LENGTH = "32768"
uv run python .\greet_demo.py
```

```python
from pathlib import Path

from auto_coding_machine import (
    AgentResponse,
    ProfileTools,
    ToolCall,
    ToolResult,
    load_profile,
    open_harness_session,
    tool,
)


class GreetTool:
    @staticmethod
    def schema():
        return {
            "type": "function",
            "function": {
                "name": "greet",
                "description": "用名字问候一个人",
                "parameters": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                },
            },
        }

    @staticmethod
    @tool(name="greet", permission="ask")
    def execute(tool_call, _sandbox, _max_output_chars):
        name = tool_call.arguments["name"]
        return ToolResult(tool_call.id, f"你好，{name}！")


def demo_model(messages):
    tool_results = [message for message in messages if message.get("role") == "tool"]
    if tool_results:
        if "用户拒绝" in tool_results[-1]["content"]:
            return AgentResponse(content="你拒绝了，我没有执行问候。", done=True)
        return AgentResponse(content="问候完成。", done=True)

    return AgentResponse(tool_calls=[
        ToolCall("greet-1", "greet", {"name": "小明"})
    ])


workspace = Path.cwd()
profile = load_profile("review")
tools = ProfileTools(workspace, profile)
tools.register(GreetTool)


def observe(event):
    if event.name == "permission_required":
        print("等待确认：", event.data["request"]["summary"])
    elif event.name == "permission_resolved":
        print("权限决议：", event.data["decision"])


session = open_harness_session(
    workspace, demo_model, profile=profile, tools=tools, on_event=observe
)
run = session.begin_run("问候小明")
result = run.start()

if result["status"] == "permission_required":
    request = result["permission_request"]
    print("参数：", request["details"])
    approved = input(f"允许 {request['summary']} 吗？[y/N] ").strip().lower() == "y"
    result = run.resolve_permission(approved=approved)

print(result["status"], result.get("reply", ""))
```

在已按[快速入门](harness-quickstart.md)安装 Harness 的调用方项目根目录运行示例。输入 `y` 后，结果应显示 `success 问候完成。`；输入其他内容会拒绝这次工具调用。

这里有三个关键点：

- `schema()` 告诉模型工具叫什么、需要什么参数。
- `@tool(..., permission="ask")` 表示执行前要由宿主程序征求用户同意。
- `tools.register(GreetTool)` 才会把工具交给 Harness；Profile YAML 不会动态加载 Python 代码。

真实界面应把 `input()` 换成自己的确认 UI；不要在没有用户同意时直接批准。把 `demo_model` 换成真实模型时，还要把 `tools.get_schemas()` 提供给模型服务。

`on_event` 只通知，不负责批准；外部程序从暂停结果读取同一份脱敏请求，再显式调用 `resolve_permission()`。长参数和明显敏感值会在 Harness 发出前隐藏。

## 4. 继续当前会话，或之后恢复

接着上一节的例子，任务完成后可以在同一个进程继续提问：

```python
session.refresh()
next_result = session.begin_run("再总结一下刚才的发现").start()
```

需要跨进程恢复时，调用方要保存会话 ID：

```python
session_id = session.store.session_id
```

之后在程序里重新创建相同的 Profile、工具和模型调用函数，再用同一个工作区和会话 ID 打开它：

```python
session = open_harness_session(
    workspace,
    demo_model,
    profile=profile,
    tools=tools,
    resume=session_id,
)
```

会话原始记录保存在工作区的 `.autocoding/profiles/<profile>/sessions/`。如果任务返回 `session_write_failed`，工具可能已经产生实际影响，但运行记录没有确认写入；先检查现场，不要直接重试同一个操作。

执行中的任务可以通过 `run.cancel()` 请求取消。实际程序通常会在后台运行 `run.start()`，再由 UI 的取消按钮调用 `cancel()`。

## 5. 可选：接入外部记忆

这一节可以跳过。不传 `memory_provider` 时，Harness 不会连接外部记忆。若你已经有可用的 Tencent MemoryCore Gateway，可以显式传入 Adapter 和身份范围；示例中的 `demo_model` 应替换成能读取请求消息的真实模型调用函数：

```python
import os

from auto_coding_machine.memory import MemoryScope, TencentMemoryCoreProvider

provider = TencentMemoryCoreProvider(
    os.environ["TDAI_MEMORY_ENDPOINT"],
    service_id=os.environ["TDAI_MEMORY_SERVICE_ID"],
)
scope = MemoryScope(
    team_id=os.environ["TDAI_MEMORY_TEAM_ID"],
    agent_id=os.environ["TDAI_MEMORY_AGENT_ID"],
    user_id=os.environ["TDAI_MEMORY_USER_ID"],
)

session = open_harness_session(
    workspace,
    demo_model,
    profile=profile,
    memory_provider=provider,
    memory_scope=scope,
)
```

`TDAI_MEMORY_API_KEY` 也必须从本地环境读取，不能写进代码或提交到仓库。上述调用只开启召回；连接失败会跳过记忆，不阻断主任务。写回默认关闭。确实需要写回时，再显式添加 `memory_writer=provider`；每次成功任务只写用户输入和最终回复，不写工具过程。结果中的 `memory_writeback` 会标记 `saved`、`failed` 或 `unknown`。`unknown` 表示服务是否收到数据无法确认，Harness 不会自动重试；远端记忆也不会随本地 `/clear` 删除。Tencent 云端部署尚未验证；更多边界见[架构说明](architecture.md)。

## 6. 运行结果与事件契约

`run.start()` 与 `run.resolve_permission()` 返回普通字典，按 `status` 区分：

| status | 附加字段 | 含义 |
| --- | --- | --- |
| `success` | `reply`；启用写回时另有 `memory_writeback` | 任务完成，`reply` 是最终回复 |
| `need_input` | `reply` | 模型停下等待补充信息，不算成功 |
| `permission_required` | `permission_request` | 等待人工决定，工具尚未执行 |
| `failed` | `error`，可能有 `reply` / `tool_result` | 运行失败；`error` 是原因 |
| `cancelled` | 无 | 协作式取消后的确定结果 |

`permission_request` 是脱敏视图：`tool_name`、`tool_call_id`、`summary`（尽力而为的短描述，具体参数看 `details`）、`details`、`turn`。修改这份视图不会改变真实执行参数。

三类错误分开处理：

- **配置错误**在打开会话时抛异常（`ValueError` / `TypeError`）：Profile YAML 非法、`on_event` 不可调用、`tools` 与 `runtime_components` 同时提供等。任务不会开始。
- **调用方式错误**在运行中抛 `RuntimeError`：重复 `start()`、没有等待中的请求却调用 `resolve_permission()`、重复提交已消费的权限决定。
- **运行失败**不抛异常，通过 `result["status"] == "failed"` 返回；`error` 取值包括 `max_turns`、`session_write_failed`、`verification_required`、`guard_stopped`、`no_tool_call`。

事件顺序遵守以下约定（详见 [ADR 0001](adr/0001-harness-event-contract.md)）：

1. 工具执行前发 `pre_tool`；每个工具调用都有配对的 `post_tool`——被拒绝或因取消未执行的调用也有 `post_tool` 和明确回执。
2. 需要审批时先发 `permission_required`（带 `request`），宿主显式调用 `resolve_permission(approved=...)` 后才发 `permission_resolved`（`decision` 为 `approved` 或 `denied`），然后才是该工具的 `post_tool`。批准只代表获准尝试，工具可能执行失败。
3. 终止事件是 `done` / `need_input` / `failed` / `cancelled` 之一；Run Result 独立返回，事件不能替代结果。

`on_event` 只观察：返回值被忽略，注册回调不会自动批准权限，回调抛异常不会中断任务，异常日志只记录事件名和异常类型，不含原始内容。公开事件一律是脱敏副本：明显凭据被遮盖，超过 200 字符的值整体隐藏。

取消是协作式取消：`run.cancel()` 后循环在下一个检查点退出。等待审批期间取消后，随后到达的批准也不执行工具，同批未执行的调用会补「未执行」回执；已发生的副作用不会被撤回。

订单查询与回复草稿助手（`examples/order_assistant.py`）把以上约定串成一个业务示例：自定义工具、业务提示词、权限交接和确定性演示路径，全程不依赖真实模型或外部服务。

## 接下来

这些例子展示的是可组合的入口和当前明确支持的接口，不代表已经支持动态安装任意 Python 插件。具体模块职责见[架构说明](architecture.md)；从第一次运行开始请回到[快速入门](harness-quickstart.md)。
