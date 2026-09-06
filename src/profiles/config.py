"""Profile 是一份执行配置：选择能力，不复制 Agent 循环。

内置配置也走同一个数据结构。自定义 YAML 只能选择已有能力，不能加载 Python。
"""

from dataclasses import asdict, dataclass, replace
from pathlib import Path
import re

import yaml


READ_TOOLS = ("read_file", "list_dir", "glob", "grep", "load_skill", "search_skills", "recall_history")
CODING_TOOLS = READ_TOOLS + ("write_file", "edit_file", "run_test", "run_bash", "memory")
COMPANION_TOOLS = ("memory", "recall_history", "load_skill", "search_skills")


@dataclass(frozen=True)
class Profile:
    """不可变配置，避免两个运行时通过修改全局 settings 互相影响。"""

    name: str = "coding"
    kind: str = "coding"
    prompt: str = ""  # Coding 为空时沿用原有系统提示词。
    tools: tuple[str, ...] = CODING_TOOLS
    skills: tuple[str, ...] | None = None  # None=全部；空元组=不加载。
    model: str | None = None
    context_budget: int | None = None  # 输入预算，不是模型完整上下文窗口。
    load_instructions: bool = True
    trace_enabled: bool = False  # 只在评测或排查问题时保存详细运行轨迹。

    @property
    def verify_changes(self) -> bool:
        return self.kind == "coding"

    def state_dir(self, workspace) -> Path:
        root = Path(workspace).resolve() / ".autocoding"
        # 所有 Profile 使用同一层级；这样 Coding 不会和其他 Profile 走两套路径。
        return root / "profiles" / self.name

    def snapshot(self) -> dict:
        return asdict(self)


BUILTINS = {
    "coding": Profile(),
    "review": Profile(
        name="review", kind="review", tools=READ_TOOLS,
        prompt="你是代码审查助手。读取代码，报告有证据的问题与修改建议。"
               "你不能修改文件或执行命令；不能声称已经修复或运行测试。",
    ),
    "companion": Profile(
        name="companion", kind="companion", tools=COMPANION_TOOLS, skills=(),
        load_instructions=False,
        prompt="你是温暖、自然的 AI 陪伴角色，认真倾听，以用户喜欢的方式交流。"
               "可以参与双方约定的浪漫角色互动。人格设定与真实记忆分开："
               "只记录用户明确提供的偏好和事实，不把想象的共同经历写成事实。"
               "不确定的往事先询问，不编造记忆。尊重用户的自主选择与现实关系。",
    ),
}


def load_profile(value: str = "coding") -> Profile:
    """接受内置名称或 YAML 路径；配置错误在启动时明确报出。"""
    if value in BUILTINS:
        return BUILTINS[value]
    path = Path(value)
    if not path.is_file():
        raise ValueError(f"找不到 Profile：{value}；可用 coding / review / companion 或 YAML 路径")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"Profile YAML 格式错误：{path}") from exc
    if not isinstance(data, dict):
        raise ValueError("Profile YAML 必须是键值配置")
    allowed = {
        "name", "kind", "prompt", "tools", "skills", "model", "context_budget",
        "load_instructions", "trace_enabled",
    }
    if set(data) - allowed:
        raise ValueError(f"未知 Profile 字段：{', '.join(sorted(set(data) - allowed))}")
    name, kind = data.get("name"), data.get("kind", "coding")
    if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", name):
        raise ValueError("name 必须以小写字母开头，只含小写字母、数字、下划线或短横线")
    if name in BUILTINS:
        raise ValueError("自定义 Profile 请使用新名称，避免与内置状态目录混用")
    if not isinstance(kind, str) or kind not in BUILTINS:
        raise ValueError("kind 必须是 coding、review 或 companion")
    for field in ("tools", "skills"):
        if field in data:
            value = data[field]
            if field == "skills" and value is None:
                continue
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                raise ValueError(f"{field} 必须是字符串列表")
            data[field] = tuple(dict.fromkeys(value))
    if "tools" in data and set(data["tools"]) - set(BUILTINS[kind].tools):
        raise ValueError(f"{kind} 的 tools 只能从该类型的内置工具中选择")
    if "context_budget" in data and (type(data["context_budget"]) is not int or data["context_budget"] <= 0):
        raise ValueError("context_budget 必须是正整数")
    if "load_instructions" in data and type(data["load_instructions"]) is not bool:
        raise ValueError("load_instructions 必须是 true 或 false")
    if "trace_enabled" in data and type(data["trace_enabled"]) is not bool:
        raise ValueError("trace_enabled 必须是 true 或 false")
    for field in ("prompt", "model"):
        if field in data and (not isinstance(data[field], str) or not data[field].strip()):
            raise ValueError(f"{field} 必须是非空文本")
    return replace(BUILTINS[kind], **data)
