"""run_bash 工具：在白名单内执行安全的 bash 命令。

【大白话】
  模型说"跑一下 pytest tests/"或"pip install xxx"，
  这个工具就去执行白名单内的命令，然后把输出塞进 ToolResult。

【安全】
  1. 白名单：只允许 pytest / python / npm / pip / git 开头的命令
  2. Shell 元字符黑名单：禁止 | ; && || > < $ ` ( ) 防止注入攻击
  3. 工作目录锁定 workspace，不能跑到外面去
  4. 超时：默认 180 秒，超时自动杀进程
  5. shell=False，避免命令注入

【权限】
  permission="ask"（需要用户确认），因为执行命令是危险操作。

【谁会用】
  MachineLoop → ToolManager → execute() 调用此工具的 execute 函数
"""

import os
import platform
import shlex
import subprocess
from typing import List, Optional

from src.engine.contracts import ToolCall, ToolResult
from src.engine.tool_manager import tool
from src.profiles.coding.sandbox import WorkspaceSandbox
from src.profiles.coding.tools.helpers import (
    clip_text,
    execution_result,
    get_str_arg,
    invalid_result,
    ok_result,
    permission_result,
)


# =====================================
# 白名单配置
# =====================================

ALLOWED_COMMANDS: List[str] = [
    "pytest",      # Python 测试框架
    "python",      # Python 解释器（限制运行脚本文件）
    "npm",         # Node.js 包管理器
    "pip",         # Python 包管理器
    "git",         # 版本控制系统
]

# Shell 元字符黑名单（防止注入攻击）
FORBIDDEN_CHARS: List[str] = [
    "|",   # 管道，可拼接其他命令
    ";",   # 命令分隔符
    "&&",  # 条件执行
    "||",  # 条件或
    ">",   # 重定向输出
    "<",   # 重定向输入
    "$",   # 变量展开或命令替换
    "`",   # 反引号命令替换
    "(",   # 子 shell
    ")",   # 子 shell 结束
]


def _is_safe_command(command: str) -> bool:
    """检查命令是否包含非法的 shell 元字符。

    干什么：
        遍历 FORBIDDEN_CHARS，如果发现任何字符在命令中，返回 False
    参数：
        command: 要检查的命令字符串
    返回：
        True = 安全，不包含元字符
        False = 不安全，包含至少一个元字符
    谁调用：
        execute() 在第二步调用此函数进行安全检查
    """
    for char in FORBIDDEN_CHARS:
        if char in command:
            # 发现危险字符，直接拒绝
            return False
    return True


def _check_whitelist(command: str) -> bool:
    """检查命令是否在白名单内。

    干什么：
        提取命令的第一个词（去掉空格后的第一个部分），
        检查它是否匹配任何白名单前缀
    参数：
        command: 要检查的命令字符串
    返回：
        True = 在白名单内
        False = 不在白名单内
    谁调用：
        execute() 在第三步调用此函数进行白名单检查
    """
    # 去除前后空格后按空格拆分
    parts = command.strip().split()
    if not parts:
        # 空命令或全是空格，直接拒绝
        return False

    # 取第一个词作为命令名
    first_word = parts[0]

    # 处理完整路径的情况：
    # 模型可能传 "/usr/bin/python" 或 "e:\\...\\python.exe"
    # 这时 first_word 不以 "python" 开头，会被误拒。
    # 解决：取 basename（文件名部分），再去掉 .exe 后缀（Windows）
    basename = os.path.basename(first_word)
    # Windows 上可执行文件带 .exe 后缀，去掉方便匹配
    if basename.lower().endswith(".exe"):
        basename = basename[:-4]

    # 检查 basename 是否匹配任何白名单前缀
    for allowed in ALLOWED_COMMANDS:
        if basename.startswith(allowed):
            # 匹配成功，例如 "python" 匹配 "python script.py"
            return True

    return False


# =====================================
# 工具定义
# =====================================

@tool(name="run_bash", permission="ask", timeout=180)
def execute(
    tool_call: ToolCall,
    sandbox: WorkspaceSandbox,
    max_output_chars: int = 10000,
) -> ToolResult:
    """在受控的工作区内执行白名单允许的 bash 命令。

    功能说明：
    - 只允许白名单内的命令：pytest, python, npm, pip, git
    - 禁止使用 shell 元字符（防止命令注入）
    - 强制 cwd 锁定到 sandbox.workspace（路径沙箱）
    - 设置超时保护（默认 180 秒）

    参数：
        tool_call: 工具调用对象，包含 arguments["command"]
        sandbox: 工作区沙箱，用于路径解析和隔离
        max_output_chars: 最大输出长度，超长会被截断

    返回：
        ToolResult:
        - 成功：exit_code=0，输出放在 content 中
        - 白名单拒绝：error_type="permission"，content="命令不在白名单内"
        - 非法命令：error_type="permission"，content="命令非法或包含 shell 元字符"
        - 执行失败：error_type="execution"，content 包含错误信息
        - 超时：error_type="timeout"，content="命令执行超时"
    """
    # ---- 第一步：获取并验证 command 参数 ----
    command = get_str_arg(tool_call, "command")
    if command is None or command.strip() == "":
        # 缺少必要参数，返回参数错误
        return invalid_result(
            tool_call,
            "run_bash 需要 'command' 参数，值为要执行的 bash 命令字符串"
        )

    # ---- 第二步：安全检查 - 禁止 shell 元字符 ----
    # 先检查是否包含危险的 shell 元字符，防止命令注入攻击
    if not _is_safe_command(command):
        return permission_result(
            tool_call,
            f"命令包含非法的 shell 元字符（如 | ; && $ 等），为防止注入攻击已被拒绝\n"
            f"当前命令：{command}"
        )

    # ---- 第三步：白名单检查 ----
    # 再检查命令是否在白名单内
    if not _check_whitelist(command):
        return permission_result(
            tool_call,
            f"命令 '{command}' 不在白名单内。\n"
            f"允许的命令前缀：{', '.join(ALLOWED_COMMANDS)}"
        )

    # ---- 第四步：获取工作区绝对路径 ----
    # 直接用 sandbox.workspace（构造时已经 resolve 成绝对路径）
    # 注意：不能用 sandbox.resolve("")，因为空路径会被 resolve 拒绝返回 None
    full_workspace = sandbox.workspace

    # ---- 第五步：执行命令 ----
    try:
        # 注意：shell=False 防止命令注入
        # cwd=full_workspace 锁定工作目录
        # timeout=180 秒防止死循环

        # 分割命令字符串为参数列表
        # Windows 上 shlex.split 会把 \ 当转义符，破坏 e:\xxx 路径
        # 所以 Windows 直接按空格拆分（参考原 run_test 做法）
        if platform.system() == "Windows":
            args = command.split()
        else:
            args = shlex.split(command)

        proc = subprocess.run(
            args,                  # 分割后的命令参数列表
            cwd=str(full_workspace),  # 锁定工作目录为 workspace
            capture_output=True,     # 捕获 stdout/stderr
            text=True,               # 返回文本而非 bytes
            timeout=180,             # 180 秒超时
            shell=False,             # 禁用 shell，防注入
        )
    except subprocess.TimeoutExpired:
        # 超时情况
        return execution_result(
            tool_call,
            f"命令执行超时（超过 180 秒）\n命令：{command}"
        )
    except FileNotFoundError as e:
        # 常见情况：未找到指定的命令（如 pip 未安装）
        cmd_name = command.split()[0]
        return execution_result(
            tool_call,
            f"找不到命令 '{cmd_name}'\n{str(e)}\n请确保该工具已安装在系统 PATH 中"
        )
    except Exception as e:
        # 其他意外异常
        return execution_result(
            tool_call,
            f"执行命令时发生异常：{str(e)}\n命令：{command}"
        )

    # ---- 第六步：合并输出并截断 ----
    output = proc.stdout
    if proc.stderr:
        # 把 stderr 追加到 stdout 后面
        output += "\n" + proc.stderr

    # 截断过长输出，防止上下文爆炸
    output_content, truncated = clip_text(output, max_output_chars)

    # ---- 第七步：构造结果 ----
    metadata = {
        "exit_code": proc.returncode,
        "command": command,
        "truncated": truncated,
    }

    # 无论 exit_code 是多少，都返回成功（只是标记返回码）
    # 模型自己根据 exit_code 判断命令是否成功
    status_text = "成功" if proc.returncode == 0 else f"完成（退出码：{proc.returncode}）"

    return ok_result(
        tool_call,
        f"命令执行{status_text}\n\n{output_content}",
        metadata,
    )


def schema():
    """返回 OpenAI-Compatible 的工具 Schema。

    干什么：
        返回符合 OpenAI Function Calling 规范的 schema 字典
    返回值结构：
        {
          "type": "function",
          "function": {
            "name": "...",
            "description": "...",
            "parameters": {...}
          }
        }
    谁调用：
        ToolManager.get_schemas() 会自动调用各模块的 schema()
    """
    return {
        "type": "function",
        "function": {
            "name": "run_bash",
            "description": "在工作区中执行安全的 bash 命令（白名单模式）。允许的命令：pytest, python, npm, pip, git。禁用 shell 元字符以防止注入攻击。超时保护：180 秒。",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "要执行的 bash 命令字符串，例如 'pytest tests/test_foo.py' 或 'npm install'",
                    }
                },
                "required": ["command"],
                "additionalProperties": False,  # 不允许额外参数
            },
        },
    }
