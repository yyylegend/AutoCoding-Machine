"""订单查询与回复草稿助手：一个业务调用方如何组合 Harness。

演示内容：
  - 本地字典提供演示订单数据，不连接任何生产系统；
  - 注册两个自定义工具：查询订单（自动放行）和保存回复草稿（需要人工确认）；
  - 用业务提示词通过公共入口 open_harness_session() 运行；
  - 展示结果和事件；示例只保存草稿，不发送真实消息；
  - 默认使用确定性演示模型，无需 API Key；--live 才会按环境配置调用真实模型。

运行（在仓库根目录）：
  uv run python examples/order_assistant.py            # 确定性演示
  uv run python examples/order_assistant.py --live     # 使用 .env 里的模型

业务调用方不需要构造 MachineLoop，也不用复制权限恢复逻辑：
暂停结果给出脱敏请求视图，宿主显式调用 resolve_permission() 提交决定。
"""

import argparse
import json
import sys
from pathlib import Path

from auto_coding_machine import (
    AgentResponse,
    ModelAdapter,
    Profile,
    ProfileTools,
    ToolCall,
    ToolResult,
    open_harness_session,
    tool,
)

# 演示订单数据：进程内字典，代表调用方自己的业务数据源。
DEMO_ORDERS = {
    "A1001": {"status": "已发货", "carrier": "顺风", "tracking": "SF1234567890"},
    "A1002": {"status": "待付款", "amount": "¥299.00"},
}

# 演示草稿箱：只存在进程内，代表调用方自己的草稿存储；示例从不发送。
SAVED_DRAFTS: list[dict] = []

BUSINESS_PROMPT = (
    "你是订单客服助手。根据查询到的订单信息起草给客户的回复，"
    "保存为草稿后再汇报。绝不发送真实消息；不要编造查不到的订单信息。"
)


class QueryOrderTool:
    """按订单号查询本地演示订单；只读，自动放行。"""

    @staticmethod
    def schema():
        return {
            "type": "function",
            "function": {
                "name": "query_order",
                "description": "按订单号查询订单状态",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "order_id": {"type": "string", "description": "订单号"},
                    },
                    "required": ["order_id"],
                },
            },
        }

    @staticmethod
    @tool(name="query_order", permission="auto")
    def execute(tool_call, _sandbox, _max_output_chars):
        order_id = str(tool_call.arguments.get("order_id") or "").strip()
        order = DEMO_ORDERS.get(order_id)
        if order is None:
            return ToolResult(
                tool_call.id,
                f"没有找到订单 {order_id}",
                error=True,
                error_type="invalid_args",
            )
        return ToolResult(
            tool_call.id,
            json.dumps({"order_id": order_id, **order}, ensure_ascii=False),
        )


class SaveReplyDraftTool:
    """保存一条回复草稿；演示人工确认（ask）。只写演示草稿箱，不发送消息。"""

    @staticmethod
    def schema():
        return {
            "type": "function",
            "function": {
                "name": "save_reply_draft",
                "description": "保存一条给客户的回复草稿",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "order_id": {"type": "string", "description": "订单号"},
                        "content": {"type": "string", "description": "回复草稿内容"},
                    },
                    "required": ["order_id", "content"],
                },
            },
        }

    @staticmethod
    @tool(name="save_reply_draft", permission="ask")
    def execute(tool_call, _sandbox, _max_output_chars):
        content = str(tool_call.arguments.get("content") or "").strip()
        if not content:
            return ToolResult(
                tool_call.id,
                "草稿内容不能为空",
                error=True,
                error_type="invalid_args",
            )
        SAVED_DRAFTS.append({
            "order_id": str(tool_call.arguments.get("order_id") or ""),
            "content": content,
        })
        return ToolResult(
            tool_call.id,
            f"草稿已保存（草稿箱共 {len(SAVED_DRAFTS)} 条），未发送",
        )


def demo_model(messages) -> AgentResponse:
    """确定性演示模型：查订单 → 起草并保存 → 汇报；不访问网络。"""
    receipts = [m for m in messages if m.get("role") == "tool"]
    if not receipts:
        return AgentResponse(
            content="先查订单",
            tool_calls=[ToolCall("q-1", "query_order", {"order_id": "A1001"})],
        )
    if len(receipts) == 1:
        order = json.loads(receipts[0]["content"])
        draft = (
            f"您好，您的订单 {order['order_id']} 目前{order['status']}"
            f"（承运商 {order.get('carrier', '—')}，运单号 {order.get('tracking', '—')}）。"
        )
        return AgentResponse(
            content="起草并保存回复",
            tool_calls=[ToolCall(
                "d-1", "save_reply_draft",
                {"order_id": order["order_id"], "content": draft},
            )],
        )
    return AgentResponse(content="草稿已保存，未发送任何消息。", done=True)


def build_session(workspace, *, model_fn=None, on_event=None, live=False):
    """按业务 Profile 和自定义工具组装会话；复用公共入口，不触碰内部循环。"""
    profile = Profile(
        name="orders",
        kind="review",
        tools=(),          # 不注册任何内置文件/命令工具
        skills=(),         # 不使用技能，跳过技能目录扫描
        load_instructions=False,
        context_budget=8000,
        prompt=BUSINESS_PROMPT,
    )
    tools = ProfileTools(workspace, profile)
    tools.register(QueryOrderTool)
    tools.register(SaveReplyDraftTool)
    if live and model_fn is None:
        model_fn = ModelAdapter(tools.get_schemas(), model=profile.model).call
    return open_harness_session(
        workspace,
        model_fn or demo_model,
        profile=profile,
        tools=tools,
        on_event=on_event,
    )


def run_order_task(workspace, question, *, model_fn=None, on_event=None, approve=True, live=False):
    """运行一次订单任务；approve=False 时把权限暂停原样交给宿主决定。"""
    session = build_session(workspace, model_fn=model_fn, on_event=on_event, live=live)
    run = session.begin_run(question)
    result = run.start()
    while result.get("status") == "permission_required":
        if not approve:
            return result
        # 宿主显式提交决定；演示路径自动批准，真实界面应交给用户。
        result = run.resolve_permission(approved=True)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="订单查询与回复草稿助手（演示）")
    parser.add_argument(
        "question",
        nargs="?",
        default="客户问订单 A1001 到哪了，请起草回复",
    )
    parser.add_argument(
        "--live", action="store_true",
        help="使用 .env 配置的真实模型；默认走确定性演示模型",
    )
    parser.add_argument(
        "--workspace", type=Path, default=Path.cwd(),
        help="会话数据存放的工作区（默认当前目录）",
    )
    args = parser.parse_args()

    events = []

    def observe(event):
        events.append(event.name)
        if event.name == "permission_required":
            request = event.data["request"]
            # 摘要是尽力而为的短描述；具体参数看脱敏后的 details。
            print(f"[事件] 等待确认：{request['summary']}，参数：{request['details']}")
        elif event.name == "permission_resolved":
            print(f"[事件] 权限决议：{event.data['decision']}")
        else:
            print(f"[事件] {event.name}")

    result = run_order_task(
        args.workspace, args.question,
        live=args.live, on_event=observe,
    )

    print(f"\n状态：{result['status']}")
    if result.get("reply"):
        print(f"最终回复：{result['reply']}")
    for draft in SAVED_DRAFTS:
        print(f"草稿（未发送）[{draft['order_id']}]：{draft['content']}")
    return 0 if result["status"] == "success" else 1


if __name__ == "__main__":
    sys.exit(main())
