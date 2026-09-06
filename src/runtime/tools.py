"""按 Profile 组装现有工具；不注册的能力既不展示，也不能执行。"""

from src.config.settings import settings
from src.engine.tool_manager import ToolManager
from src.profiles.coding.sandbox import WorkspaceSandbox
from src.profiles.coding.tools import (
    read_file, list_dir, glob_tool, grep, write_file, edit_file, run_test,
    run_bash, load_skill, search_skills, memory_tool, recall_history,
)
from src.runtime.memory import create_memory_manager
from src.runtime.skills import select_skills


class ToolEnvironment(WorkspaceSandbox):
    """工具所需的运行环境：工作区负责文件边界，状态目录负责 Profile 隔离。"""

    def __init__(self, workspace, profile):
        super().__init__(workspace)
        self.sessions_dir = profile.state_dir(workspace) / "sessions"
        self.skills = select_skills(self.workspace, profile.skills)
        self.memory_manager = create_memory_manager(
            workspace, state_dir=None if profile.name == "coding" else profile.state_dir(workspace),
        )


class ProfileTools:
    """共享工具集合，继续使用 Engine 已有的工具协议。"""

    def __init__(self, workspace, profile, max_output_chars=5000):
        self.sandbox = ToolEnvironment(workspace, profile)
        self._manager = ToolManager(self.sandbox, max_output_chars)
        # 顺序保持稳定：避免配置列表换个顺序就改变发送给模型的工具定义。
        modules = (read_file, list_dir, glob_tool, grep, write_file, edit_file,
                   run_test, run_bash, load_skill, search_skills, memory_tool, recall_history)
        for module in modules:
            name = module.schema()["function"]["name"]
            # 没有可用技能时不注册空入口，避免模型反复搜索；执行层也会拒绝调用。
            if name in ("load_skill", "search_skills") and not self.sandbox.skills:
                continue
            if name in profile.tools and (name != "memory" or settings.MEMORY_ENABLED):
                self._manager.register(module)

    def get_manager(self):
        return self._manager

    def get_schemas(self):
        return self._manager.get_schemas()

    def execute(self, tool_call):
        return self._manager.execute(tool_call)
