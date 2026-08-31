"""Coding 路径沙箱。

【这文件是干什么的】
  模型会传各种路径，比如：
    src/main.py
    ../secret.txt
    ../../etc/passwd
    C:\\Windows\\System32\\...

  如果直接拿这些路径去读文件，很危险。
  本文件负责：
    1. 把用户路径解析成真实绝对路径
    2. 检查它是不是还在 workspace 里面
    3. 不在就拒绝

【大白话】
  workspace = 允许活动的院子
  用户路径 = 想去的地方
  resolve() = 算算真实位置，并检查有没有翻墙出去

【谁会用】
  src/profiles/coding/tools/ 里的只读工具

【安全细节】
  - 用 Path.resolve() 展开 ..、符号链接和绝对路径
  - 用 relative_to(workspace) 检查是否越界
  - Windows 的 C:\\... 也会被拦住
  - symlink 指向外部也会被拦住
"""

from __future__ import annotations

from pathlib import Path


class WorkspaceSandbox:
    """限制所有文件操作都只能发生在 workspace 内。

    用法例子：
        sandbox = WorkspaceSandbox("/home/user/project")

        # 成功：返回安全路径
        safe = sandbox.resolve("src/main.py")
        print(safe)  # /home/user/project/src/main.py

        # 失败：返回 None
        unsafe = sandbox.resolve("../../etc/passwd")
        print(unsafe)  # None
    """

    def __init__(self, workspace: str | Path):
        """初始化沙箱。

        参数：
          workspace — 项目根目录，或允许操作的目录。
                      会先 resolve() 成绝对路径，避免相对路径歧义。

        大白话：
          传 "." 就是当前目录
          传 "/home/user/project" 就是那个目录
        """
        self.workspace = Path(workspace).resolve()

    def resolve(self, user_path: str) -> Path | None:
        """把用户路径解析成安全路径。

        成功：返回 workspace 内的绝对 Path
        失败：返回 None（越界、空路径、非法路径）

        处理步骤：
          1. 空路径直接拒绝
          2. 绝对路径：直接 resolve
          3. 相对路径：拼到 workspace 后再 resolve
          4. 用 relative_to(workspace) 检查有没有跑出院子
          5. 跑出去就返回 None

        注意：
          resolve() 会展开 .. 和符号链接（symlink）。
          所以即使写 ../../etc/passwd，最终也会被检查出来。

        例子：
          sandbox = WorkspaceSandbox("/home/user/project")

          # 成功
          sandbox.resolve("src/main.py")
          -> /home/user/project/src/main.py

          # 失败（路径穿越）
          sandbox.resolve("../../etc/passwd")
          -> None

          # 失败（绝对路径在外面）
          sandbox.resolve("/etc/passwd")
          -> None

          # 失败（空路径）
          sandbox.resolve("")
          -> None
        """
        if user_path is None:
            return None

        text = str(user_path).strip()
        if not text:
            return None

        try:
            raw = Path(text)
            if raw.is_absolute():
                # 绝对路径直接 resolve
                full = raw.resolve()
            else:
                # 相对路径拼到 workspace 后 resolve
                full = (self.workspace / raw).resolve()

            # 关键检查：如果不在 workspace 内，relative_to 会抛 ValueError
            full.relative_to(self.workspace)
            return full
        except (OSError, ValueError):
            # OSError：路径非法（比如包含不能用的字符）
            # ValueError：不在 workspace 内
            return None

    def is_inside(self, user_path: str) -> bool:
        """判断用户路径是否在 workspace 内。

        快捷方法：
          return resolve(user_path) is not None
        """
        return self.resolve(user_path) is not None

    def relpath(self, full_path: Path) -> str:
        """把绝对路径转回相对 workspace 的字符串，方便展示。

        例子：
          sandbox = WorkspaceSandbox("/home/user/project")
          full = Path("/home/user/project/src/main.py")
          sandbox.relpath(full)
          -> "src/main.py"

        注意：
          统一用 / 作为分隔符，即使在 Windows 上也这样，
          方便展示给模型看（避免 \\ 转义混乱）。
        """
        try:
            return str(full_path.resolve().relative_to(self.workspace)).replace("\\", "/")
        except Exception:
            return str(full_path)
