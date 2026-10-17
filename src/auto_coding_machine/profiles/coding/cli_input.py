"""Textual 输入框的斜杠命令参数补全与 Profile 清单。

命令名本身的菜单由 tui/app.py 的 COMMANDS 提供；这里只补命令后面的参数：
  - /profile <片段> 补 Profile 名称或 YAML 路径
  - /resume <片段>  补会话 id
  - /skill <片段>   补技能名
"""

import time
from pathlib import Path

from prompt_toolkit.completion import Completer, Completion
from auto_coding_machine.profiles.config import BUILTINS, load_profile


# 会话清单缓存时长（秒）。
# 补全每敲一键都会触发查询，list_sessions 要扫目录读文件，
# 不缓存的话输入会卡；3 秒内的新会话用旧的清单也够用。
_SESSIONS_CACHE_TTL = 3.0


class SlashCompleter(Completer):
    """斜杠命令参数补全器。

    1. /profile <片段> -> 补 Profile（右边显示配置名称）
    2. /skill <片段>   -> 补技能名（右边显示技能描述）
    3. /resume <片段>  -> 补会话 id（右边显示会话标题）

    其它情况不提供补全。
    """

    def __init__(self, skills: list, get_sessions, profiles=None):
        """初始化。

        参数：
          skills       - 技能清单 [{"name", "description"}, ...]
                         （启动时扫描一次，之后不变）
          get_sessions - 返回会话清单的函数 [{"id", "title", "mtime"}, ...]
                         （会话会变，每次现查，内部带 TTL 缓存）
          profiles     - profile_choices() 返回的清单
        """
        self.skills = skills
        self.profiles = profiles or []
        self.get_sessions = get_sessions
        self._sessions_cache = None
        self._sessions_cache_at = 0.0

    # ---- 会话清单：带 TTL 缓存 ----
    def _cached_sessions(self) -> list:
        now = time.time()
        if self._sessions_cache is None or now - self._sessions_cache_at > _SESSIONS_CACHE_TTL:
            try:
                self._sessions_cache = self.get_sessions() or []
            except Exception:
                # 查会话失败不该影响输入（目录被删之类）
                self._sessions_cache = []
            self._sessions_cache_at = now
        return self._sessions_cache

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor

        if text.startswith("/profile "):
            fragment = text[len("/profile "):]
            for item in self.profiles:
                if item["value"].startswith(fragment):
                    yield Completion(item["value"], start_position=-len(fragment), display_meta=item["name"])
            return

        # ---- /resume <片段>：补会话 id ----
        if text.startswith("/resume "):
            fragment = text[len("/resume "):]
            for s in self._cached_sessions():
                if s.get("id", "").startswith(fragment):
                    yield Completion(
                        s["id"],
                        start_position=-len(fragment),
                        display_meta=(s.get("title") or "")[:40],
                    )
            return

        # ---- /skill <片段>：补技能名 ----
        if text.startswith("/skill "):
            fragment = text[len("/skill "):]
            for s in self.skills:
                if s.get("name", "").startswith(fragment):
                    yield Completion(
                        s["name"],
                        start_position=-len(fragment),
                        display_meta=(s.get("description") or "")[:40],
                    )


def profile_choices(workspace):
    """清单与补全共用；只扫描约定目录，坏配置不阻断输入。"""
    choices = [{"value": name, "name": name} for name in BUILTINS]
    # 用户配置放在仓库根目录的 profile_configs；src/profiles 是程序源码。
    for path in sorted((Path(workspace) / "profile_configs").glob("*.y*ml")):
        try:
            profile = load_profile(str(path))
        except (ValueError, OSError):
            continue
        choices.append({"value": path.relative_to(workspace).as_posix(), "name": profile.name})
    return choices
