from auto_coding_machine.engine import PermissionDecision


class ScriptedPermission:
    """按工具名返回固定权限决定，模拟写操作需要确认。"""

    def __init__(self, ask_names=()):
        self.ask_names = set(ask_names)

    def check(self, tool_call):
        if tool_call.name in self.ask_names:
            return PermissionDecision.ASK
        return PermissionDecision.AUTO


def scripted_model(*responses):
    """按顺序返回脚本化回复，并记录每次模型调用收到的完整历史。"""
    remaining = list(responses)

    def model_fn(messages):
        model_fn.calls.append(list(messages))
        return remaining.pop(0)

    model_fn.calls = []
    return model_fn


async def ready(app, pilot):
    """等待 Textual 应用结束当前后台操作；超时直接失败。"""
    for _ in range(100):
        await pilot.pause(0.02)
        if not app.busy:
            return
    raise AssertionError("界面操作未在限定时间内结束")
