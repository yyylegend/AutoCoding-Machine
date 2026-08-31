"""权限管理器：决定工具能不能执行。

【这文件是干什么的】
  每个工具调用执行前，都要先过权限检查：
    read_file   -> AUTO（绿灯）
    write_file  -> ASK（黄灯）
    rm -rf      -> DENY（红灯）

【三种决策】
  AUTO — 直接执行
  ASK  — 先问用户
  DENY — 直接拒绝

【当前阶段】
  Phase 4：只读工具 AUTO，写操作 ASK，未知工具 DENY。
  当前 MachineLoop 里 ASK 按 AUTO 执行（CLI 模式直接放行），
  后续 Phase 再补真正的用户确认流程。

【权限来源（优先级）】
  1. 传入 tool_manager 时：先读工具 @tool 装饰器声明的权限
  2. 拿不到（未注册 / 没传 tool_manager）：回落到 tool_defaults 硬编码表
  3. 表里也没有：DENY

【谁会用】
  src/engine/machine_loop.py
"""

from src.engine.contracts import PermissionDecision, ToolCall


class PermissionManager:
    """权限管理器。

    用法例子：
        perm = PermissionManager()
        decision = perm.check(ToolCall(id="1", name="read_file", arguments={...}))
        if decision == PermissionDecision.DENY:
            # 拒绝
    """

    def __init__(self, tool_manager=None, auto_approve=False, plan_mode=False):
        """初始化。

        参数：
          tool_manager — 可选的 ToolManager。传了就优先读工具
                         @tool 装饰器声明的权限；不传时行为
                         和以前完全一样（只查 tool_defaults 表）。
          auto_approve — 无人值守开关（评测/benchmark 用）。True 时把
                         ASK（黄灯）放行成 AUTO，但 DENY（红灯）仍然拒绝，
                         保留安全底线。默认 False，生产行为不变。
          plan_mode — Plan Mode 开关。True 时写工具一律 DENY，只读工具仍可执行。
                         默认 False，生产行为不变。

        Phase 2 硬编码规则。
        Phase 3 可以改成从配置读取，或按 workspace 动态判断。
        """
        self.tool_manager = tool_manager
        self.auto_approve = auto_approve
        self.plan_mode = plan_mode

        # 工具默认权限（fallback 表：没有 tool_manager 或工具未注册时用）
        self.tool_defaults = {
            # 只读工具：绿灯，直接执行
            "read_file": PermissionDecision.AUTO,
            "list_dir": PermissionDecision.AUTO,
            "glob": PermissionDecision.AUTO,
            "grep": PermissionDecision.AUTO,
            # 写操作工具：黄灯，需要确认
            # 当前 MachineLoop 里 ASK 按 AUTO 执行（CLI 模式直接放行）
            "write_file": PermissionDecision.ASK,
            "edit_file": PermissionDecision.ASK,
            "run_test": PermissionDecision.ASK,
            "run_bash": PermissionDecision.ASK,
            # Browser 子任务工具：黄灯，需要确认
        }

        # Plan Mode 下拒绝的写工具集合
        self._WRITE_TOOLS = {
            "write_file", "edit_file", "run_bash", "run_test",
        }

    def check(self, tool_call: ToolCall) -> PermissionDecision:
        """检查工具调用是否允许。

        返回：
          AUTO — 直接执行
          ASK  — 需要确认
          DENY — 直接拒绝

        权限来源（优先级）：
          1. tool_manager 里工具装饰器声明的权限（工具已注册时）
          2. tool_defaults 硬编码表
          3. 都没有 -> DENY

        auto_approve=True 时（评测场景），ASK 会被放行成 AUTO，
        但 DENY 仍然拒绝。
        """
        name = ""
        if tool_call.name is not None:
            name = str(tool_call.name).strip()

        # Plan Mode：写工具一律拒绝（只读模式的核心保障）
        if self.plan_mode and name in self._WRITE_TOOLS:
            return PermissionDecision.DENY

        decision = self._resolve_level(name)

        # 无人值守：ASK 放行成 AUTO；DENY 不动，保留安全底线
        if self.auto_approve and decision == PermissionDecision.ASK:
            return PermissionDecision.AUTO
        return decision

    def _resolve_level(self, name: str) -> PermissionDecision:
        """按优先级解析工具的原始权限级别（不含 auto_approve 转换）。

        优先级：工具装饰器声明 > tool_defaults 表 > DENY。
        """
        # 优先问 ToolManager：工具自己用 @tool 装饰器声明的权限最准
        # 只在工具确实注册过时采信；未注册时回落到硬编码表，
        # 保证老用法（不传 tool_manager）行为完全不变
        if self.tool_manager is not None and self.tool_manager.is_registered(name):
            level = self.tool_manager.get_permission(name)
            # 字符串 -> 枚举；意外值一律当 DENY，宁严勿松
            if level == "auto":
                return PermissionDecision.AUTO
            if level == "ask":
                return PermissionDecision.ASK
            return PermissionDecision.DENY

        # 查表
        decision = self.tool_defaults.get(name)
        if decision is not None:
            return decision

        # 默认：未知工具拒绝（不让模型瞎调工具跑危险动作）
        return PermissionDecision.DENY
