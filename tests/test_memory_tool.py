"""memory 工具测试（工具层：schema / 参数校验 / 分发 / 注册）。

不碰 LLM。USER.md 的路径通过 monkeypatch 指到临时目录，
避免测试写到真实的用户主目录。
"""

import pytest

from src.engine.contracts import ToolCall
from src.engine.tool_manager import ToolManager
from src.profiles.coding.sandbox import WorkspaceSandbox
from src.profiles.coding.tools import CodingTools, memory_tool


@pytest.fixture
def sandbox(tmp_path):
    """指向临时目录的沙箱（workspace = tmp_path）。"""
    return WorkspaceSandbox(tmp_path)


@pytest.fixture(autouse=True)
def fake_user_path(tmp_path, monkeypatch):
    """把 USER.md 路径打到临时目录，保护真实主目录。"""
    fake = tmp_path / "fake_home" / ".autocoding" / "USER.md"
    monkeypatch.setattr(memory_tool, "_user_memory_path", lambda: fake)
    return fake


def _call(action=None, target=None, content=None, old_text=None):
    """快捷构造 ToolCall。"""
    args = {}
    if action is not None:
        args["action"] = action
    if target is not None:
        args["target"] = target
    if content is not None:
        args["content"] = content
    if old_text is not None:
        args["old_text"] = old_text
    return ToolCall(id="call_1", name="memory", arguments=args)


# ============================================================
#  schema 与注册
# ============================================================


def test_schema_shape():
    """schema 应是 OpenAI-compatible，参数含 action/target/content/old_text。"""
    s = memory_tool.schema()
    assert s["type"] == "function"
    assert s["function"]["name"] == "memory"
    props = s["function"]["parameters"]["properties"]
    assert "action" in props
    assert "target" in props
    assert "content" in props
    assert "old_text" in props
    assert s["function"]["parameters"]["required"] == ["action", "target"]


def test_permission_is_auto(sandbox):
    """通过 ToolManager 验证装饰器声明的权限是 auto。"""
    manager = ToolManager(sandbox)
    manager.register(memory_tool)
    assert manager.get_permission("memory") == "auto"


def test_registered_in_coding_tools(tmp_path):
    """MEMORY_ENABLED 默认开启时，memory 应出现在 CodingTools 的 schema 里。"""
    tools = CodingTools(tmp_path)
    names = [s["function"]["name"] for s in tools.get_schemas()]
    assert "memory" in names


def test_permission_manager_wiring_allows_memory(tmp_path):
    """回归锁：按 CLI/executor 的接线方式，memory 必须是 AUTO。

    曾经的 bug：PermissionManager() 不传 tool_manager，
    memory / search_skills 被硬编码白名单当未知工具 DENY，
    模型嘴上说记住了实际什么都没写进去（假记忆）。
    """
    from src.engine.contracts import PermissionDecision
    from src.engine.permission_manager import PermissionManager

    tools = CodingTools(tmp_path)
    permission = PermissionManager(tool_manager=tools.get_manager())

    call = ToolCall(id="1", name="memory",
                    arguments={"action": "add", "target": "memory", "content": "x"})
    assert permission.check(call) == PermissionDecision.AUTO

    # 顺带锁住同批受害的技能工具
    call = ToolCall(id="2", name="search_skills", arguments={"query": "x"})
    assert permission.check(call) == PermissionDecision.AUTO


# ============================================================
#  参数校验
# ============================================================


def test_missing_action(sandbox):
    result = memory_tool.execute(_call(target="memory"), sandbox)
    assert result.error is True
    assert result.error_type == "invalid_args"


def test_unknown_action(sandbox):
    result = memory_tool.execute(_call(action="delete", target="memory"), sandbox)
    assert result.error is True
    assert result.error_type == "invalid_args"


def test_unknown_target(sandbox):
    result = memory_tool.execute(_call(action="add", target="global"), sandbox)
    assert result.error is True
    assert result.error_type == "invalid_args"


def test_add_requires_content(sandbox):
    result = memory_tool.execute(_call(action="add", target="memory"), sandbox)
    assert result.error is True
    assert result.error_type == "invalid_args"
    assert "content" in result.content


def test_replace_requires_old_text(sandbox):
    result = memory_tool.execute(
        _call(action="replace", target="memory", content="新内容"), sandbox)
    assert result.error is True
    assert result.error_type == "invalid_args"
    assert "old_text" in result.content


# ============================================================
#  分发执行（写文件走临时目录）
# ============================================================


def test_add_memory_writes_project_file(sandbox, tmp_path):
    """target=memory 应写到 workspace 下的 .autocoding/MEMORY.md。"""
    result = memory_tool.execute(
        _call(action="add", target="memory", content="测试命令是 pytest -q"), sandbox)
    assert result.error is False
    assert "已记录" in result.content

    path = tmp_path / ".autocoding" / "MEMORY.md"
    assert path.is_file()
    assert "- 测试命令是 pytest -q" in path.read_text(encoding="utf-8")


def test_add_user_writes_home_file(sandbox, fake_user_path):
    """target=user 应写到（monkeypatch 后的）用户主目录 USER.md。"""
    result = memory_tool.execute(
        _call(action="add", target="user", content="用户喜欢中文注释"), sandbox)
    assert result.error is False
    assert fake_user_path.is_file()
    assert "- 用户喜欢中文注释" in fake_user_path.read_text(encoding="utf-8")


def test_replace_and_remove_via_tool(sandbox, tmp_path):
    """replace / remove 走完整链路。"""
    memory_tool.execute(
        _call(action="add", target="memory", content="旧的约定"), sandbox)

    result = memory_tool.execute(
        _call(action="replace", target="memory", old_text="旧的约定",
              content="新的约定"), sandbox)
    assert result.error is False

    result = memory_tool.execute(
        _call(action="remove", target="memory", old_text="新的约定"), sandbox)
    assert result.error is False

    text = (tmp_path / ".autocoding" / "MEMORY.md").read_text(encoding="utf-8")
    assert "约定" not in text


def test_match_failure_is_execution_error(sandbox):
    """匹配失败属于执行类错误（模型可改参数后重试）。"""
    result = memory_tool.execute(
        _call(action="remove", target="memory", old_text="不存在"), sandbox)
    assert result.error is True
    assert result.error_type == "execution"


# ============================================================
#  注入构造
# ============================================================


def test_build_memory_injection_empty(tmp_path):
    """没有任何记忆内容时返回 None（跳过注入）。"""
    assert memory_tool.build_memory_injection(tmp_path) is None


def test_build_memory_injection_with_content(sandbox, tmp_path):
    """有记忆内容时返回一条 system 消息。"""
    memory_tool.execute(
        _call(action="add", target="memory", content="项目用 SQLite"), sandbox)

    injection = memory_tool.build_memory_injection(tmp_path)
    assert injection is not None
    assert injection["role"] == "system"
    assert "项目用 SQLite" in injection["content"]
