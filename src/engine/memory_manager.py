"""长期记忆管理器：MEMORY.md + USER.md 的读写与注入渲染。

【这文件是干什么的】
  给 Agent 提供"下次还记得"的长期记忆。就两个普通 markdown 文件：
    MEMORY.md — 项目笔记（踩坑教训、常用命令、项目约定）
    USER.md   — 用户画像（偏好、沟通习惯）

  本类只做三件事：
    1. 读文件（load / render_injection）——会话启动时把内容拼进上下文
    2. 写文件（add / replace / remove）——memory 工具转调这里
    3. 容量看守——每个文件有字符上限，写超了就报错，不自动压缩

【设计约定（都是拍板过的）】
  - 条目格式：markdown 列表行，一行一条 "- xxx"
  - 容量满：报错并附当前全部条目，让模型自己合并/删除后再重试
  - replace/remove：用唯一子串匹配（同 edit_file 的 old_text 风格）
  - 完全重复的条目拒绝添加（防模型反复记同一件事）
  - 冻结快照：注入内容在会话开始时读一次，中途写盘不重新注入

【重要边界】
  - 本类是纯文件逻辑：不碰 LLM、不碰数据库、不碰沙箱
  - 路径由调用方传入，本类不猜测文件放哪
    （coding profile 的路径约定在 tools/memory_tool.py 里）

【谁会用】
  - src/profiles/coding/tools/memory_tool.py：memory 工具转调 add/replace/remove
  - Runtime Profile：启动时调 render_injection() 注入
  - tests/test_memory_manager.py：单元测试
"""

from __future__ import annotations

from filelock import FileLock
from pathlib import Path


class MemoryManager:
    """管理两个记忆文件的读写。

    用法例子：
        manager = MemoryManager(
            memory_path=".autocoding/MEMORY.md",
            user_path="~/.autocoding/USER.md",
        )
        manager.add("memory", "测试命令是 pytest tests -q")
        text = manager.render_injection()  # 拼进 system prompt 的文本
    """

    def __init__(
        self,
        memory_path: str | Path,
        user_path: str | Path,
        memory_limit: int = 2200,
        user_limit: int = 1375,
    ):
        """初始化。

        参数：
          memory_path  — MEMORY.md 的路径（项目笔记）
          user_path    — USER.md 的路径（用户画像）
          memory_limit — MEMORY.md 字符上限（默认 2200，约 800 token）
          user_limit   — USER.md 字符上限（默认 1375，约 500 token）
        """
        self.memory_path = Path(memory_path)
        self.user_path = Path(user_path)
        self.memory_limit = memory_limit
        self.user_limit = user_limit

    # ============================================================
    #  读：load / render_injection
    # ============================================================

    def load(self, target: str) -> str:
        """读取某个记忆文件的内容。

        参数：
          target — "memory" 或 "user"

        返回：
          文件内容字符串；文件不存在或 target 不合法返回空字符串。
        """
        info = self._target_info(target)
        if info is None:
            return ""
        path = info["path"]
        if not path.is_file():
            return ""
        try:
            return path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            # 读失败（权限/占用等）按"没有记忆"处理，不让启动崩掉
            return ""

    def render_injection(self) -> str:
        """渲染注入上下文的文本（冻结快照的内容）。

        返回：
          两个记忆段落拼接的文本，每段带头部（名称 + 容量百分比）。
          两个文件都为空时返回空字符串（调用方据此决定不注入）。

        头部长这样：
          【MEMORY 项目笔记 | 67% 1474/2200 字符】

        容量兜底：
          人手动改文件可能写超上限。渲染时按上限截断并加提示，
          保证启动不崩、上下文不被撑爆。
        """
        sections = []
        for target in ["memory", "user"]:
            info = self._target_info(target)
            text = self.load(target).strip()
            if text == "":
                continue

            limit = info["limit"]
            used = len(text)
            # 超上限：截断 + 提示（人手动改坏文件的兜底）
            if used > limit:
                text = text[:limit] + "\n（内容超出上限已截断，请整理该文件）"
                used = limit

            percent = int(used * 100 / limit)
            header = "【" + info["label"] + " | " + str(percent) + "% " \
                     + str(used) + "/" + str(limit) + " 字符】"
            sections.append(header + "\n" + text)

        if len(sections) == 0:
            return ""

        preamble = "以下是你的长期记忆（memory 工具维护，会话开始时的快照，本次会话中的写入下次生效）："
        return preamble + "\n\n" + "\n\n".join(sections)

    # ============================================================
    #  写：add / replace / remove
    # ============================================================

    def add(self, target: str, content: str) -> dict:
        """添加一条记忆。

        参数：
          target  — "memory" 或 "user"
          content — 条目内容（会自动加 "- " 前缀存成列表行）

        返回：
          {"ok": bool, "message": str}
          message 可直接作为 ToolResult 的 content 给模型看。

        规则：
          1. 完全重复的条目拒绝（ok=True 但提示未重复添加）
          2. 写入后超过字符上限 → 报错，附当前全部条目，
             提示模型先 replace 合并或 remove 删除，再重试
        """
        info = self._target_info(target)
        if info is None:
            return {"ok": False, "message": "target 只能是 memory 或 user"}

        # 读取-校验-写回全程持锁，防止并发会话互相覆盖
        with self._write_lock(info["path"]):
            lines = self._load_lines(info["path"])
            new_line = self._to_entry_line(content)

            # 重复检查：一模一样的条目不再加
            if new_line in lines:
                return {"ok": True, "message": "该条目已存在，未重复添加。"}

            new_lines = lines + [new_line]
            over = self._check_capacity(new_lines, info)
            if over is not None:
                return {"ok": False, "message": over}

            self._save(info["path"], new_lines)
        return {"ok": True, "message": "已记录到 " + info["label"]
                + "（已存盘，注入快照下次会话生效）：" + new_line}

    def replace(self, target: str, old_text: str, content: str) -> dict:
        """替换一条记忆（old_text 唯一子串匹配）。

        参数：
          target   — "memory" 或 "user"
          old_text — 用来定位旧条目的子串，必须恰好匹配一条
          content  — 新内容

        返回：
          {"ok": bool, "message": str}

        规则：
          匹配 0 条或多条都报错；替换后同样过容量校验
          （换成更长的内容也可能超限）。
        """
        info = self._target_info(target)
        if info is None:
            return {"ok": False, "message": "target 只能是 memory 或 user"}

        # 读取-校验-写回全程持锁，防止并发会话互相覆盖
        with self._write_lock(info["path"]):
            lines = self._load_lines(info["path"])
            match_index, match_error = self._find_unique_match(lines, old_text)
            if match_error is not None:
                return {"ok": False, "message": match_error}

            new_lines = list(lines)
            new_lines[match_index] = self._to_entry_line(content)
            over = self._check_capacity(new_lines, info)
            if over is not None:
                return {"ok": False, "message": over}

            self._save(info["path"], new_lines)
        return {"ok": True, "message": "已替换 " + info["label"]
                + " 中的条目（已存盘，注入快照下次会话生效）。"}

    def remove(self, target: str, old_text: str) -> dict:
        """删除一条记忆（old_text 唯一子串匹配）。

        参数：
          target   — "memory" 或 "user"
          old_text — 用来定位条目的子串，必须恰好匹配一条

        返回：
          {"ok": bool, "message": str}
        """
        info = self._target_info(target)
        if info is None:
            return {"ok": False, "message": "target 只能是 memory 或 user"}

        # 读取-校验-写回全程持锁，防止并发会话互相覆盖
        with self._write_lock(info["path"]):
            lines = self._load_lines(info["path"])
            match_index, match_error = self._find_unique_match(lines, old_text)
            if match_error is not None:
                return {"ok": False, "message": match_error}

            removed = lines[match_index]
            new_lines = list(lines)
            del new_lines[match_index]

            self._save(info["path"], new_lines)
        return {"ok": True, "message": "已删除条目：" + removed}

    # ============================================================
    #  内部小工具
    # ============================================================

    def _target_info(self, target: str):
        """把 target 字符串翻译成 路径/上限/展示名。不合法返回 None。"""
        if target == "memory":
            return {"path": self.memory_path, "limit": self.memory_limit,
                    "label": "MEMORY 项目笔记"}
        if target == "user":
            return {"path": self.user_path, "limit": self.user_limit,
                    "label": "USER 用户画像"}
        return None

    def _load_lines(self, path: Path) -> list:
        """把文件读成条目行列表（去掉空行）。文件不存在返回空列表。"""
        if not path.is_file():
            return []
        # 写路径里的读取失败不能当成“空文件”。否则瞬时 I/O 错误后继续
        # add/replace/remove，会用不完整内容覆盖旧记忆，造成数据丢失。
        # 这里让异常向上传播，由 memory 工具转成 ToolResult 错误。
        text = path.read_text(encoding="utf-8", errors="ignore")
        lines = []
        for line in text.splitlines():
            if line.strip() != "":
                lines.append(line.rstrip())
        return lines

    def _to_entry_line(self, content: str) -> str:
        """把内容规整成一条列表行："- xxx"。

        多行内容压成一行（换行换成空格），保证"一行一条"格式不被破坏。
        """
        text = " ".join(str(content).split())
        if text.startswith("- "):
            return text
        return "- " + text

    def _match_lines(self, lines: list, old_text: str) -> list:
        """返回包含 old_text 子串的行下标列表。"""
        matched = []
        for i, line in enumerate(lines):
            if old_text in line:
                matched.append(i)
        return matched

    def _find_unique_match(self, lines: list, old_text: str) -> tuple:
        """返回唯一匹配下标；匹配失败时返回可直接展示的错误。"""
        matched = self._match_lines(lines, old_text)
        if len(matched) == 0:
            message = ("没有找到包含 \"" + old_text + "\" 的条目。"
                       + self._entries_hint(lines))
            return None, message
        if len(matched) > 1:
            message = ("有 " + str(len(matched)) + " 条条目都包含 \""
                       + old_text + "\"，请换一个更精确的 old_text。"
                       + self._entries_hint(lines))
            return None, message
        return matched[0], None

    def _check_capacity(self, lines: list, info: dict):
        """容量校验。超限返回错误信息字符串（附当前条目），没超返回 None。

        不自动压缩：把"记什么、删什么"的判断留给模型，
        它收到错误后应该先 replace 合并或 remove 旧条目，再重试。
        """
        total = len("\n".join(lines))
        limit = info["limit"]
        if total <= limit:
            return None
        return (info["label"] + " 已满（写入后 " + str(total) + "/" + str(limit)
                + " 字符）。请先用 replace 合并相近条目、或用 remove 删除过时条目腾出空间，"
                + "然后在本轮内重试本次写入。当前条目：\n" + "\n".join(lines))

    def _entries_hint(self, lines: list) -> str:
        """生成"当前条目"提示，拼在匹配失败的错误信息后面。"""
        if len(lines) == 0:
            return "（该记忆文件目前是空的）"
        return "当前条目：\n" + "\n".join(lines)

    def _write_lock(self, path: Path) -> FileLock:
        """写操作的过程锁：读取-校验-写回全程持有，防止并发会话互相覆盖。

        锁文件放同目录（<memory-file>.lock），命名与 session_store 的
        "<file>.lock" 模式一致。timeout=10 防止死锁——拿不到锁就报错，
        由调用方（memory 工具）转成友好的错误信息。
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        return FileLock(str(path) + ".lock", timeout=10)

    def _save(self, path: Path, lines: list) -> None:
        """把条目行写回文件（原子替换）。要求调用方已持有 _write_lock。

        原子替换的意思：先写同目录临时文件，写成功后一步替换目标文件。
        读方不用加锁——要么读到旧的完整版，要么读到新的完整版，没有中间态。
        写失败时删掉临时文件，旧文件一个字节都不会坏。
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        text = "" if len(lines) == 0 else "\n".join(lines) + "\n"
        tmp = path.with_suffix(path.suffix + ".tmp")
        try:
            tmp.write_text(text, encoding="utf-8")
            tmp.replace(path)
        except OSError:
            # 写失败：清理临时文件；目标文件没被碰过，保持完整
            try:
                tmp.unlink()
            except OSError:
                pass
            raise
