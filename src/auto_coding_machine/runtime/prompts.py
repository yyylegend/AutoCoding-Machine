"""共享的指令文件读取与上下文注入；不依赖终端。"""

from pathlib import Path

_PROJECT_INSTRUCTION_FILES = ["AGENTS.md", "CLAUDE.md"]  # 项目层候选文件名（主名在前，备胎在后）
_MAX_GLOBAL_CHARS = 5000   # 全局层上限（个人偏好 + 人格设定，给足空间）
_MAX_PROJECT_CHARS = 8000  # 项目层上限（团队约定，详细）

# 注：上下文 token 预算的计算已统一收到 context_setup.py（单一真相源）


def _truncate_at_section(text: str, limit: int) -> tuple:
    """把文本截断到 limit 字符内，尽量在章节标题处切。

    返回 (截断后的文本, 是否发生了截断)。
    """
    if len(text) <= limit:
        return text, False
    # 在 limit 之前找最后一个章节标题（# 或 ## 开头的行），从那里切
    cut = text.rfind("\n#", 0, limit)
    if cut > limit // 2:  # 至少保留一半内容，否则就硬切
        return text[:cut].rstrip() + "\n\n（后续章节已省略）", True
    return text[:limit], True


def _global_instruction_paths() -> list:
    """返回全局指令文件的候选路径（按优先级）。

    兼容两个主流约定：
      1. ~/.agents/AGENTS.md  — 新兴标准
      2. ~/.claude/CLAUDE.md  — Claude Code 的主流约定
    """
    home = Path.home()
    return [
        home / ".agents" / "AGENTS.md",
        home / ".claude" / "CLAUDE.md",
    ]


def _load_first_found(candidate_paths: list, cap: int):
    """在候选路径里找第一个存在的文件，截断后返回。

    返回：
      找到：{"path": Path, "content": str, "truncated": bool}
      没找到：None
    """
    for path in candidate_paths:
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        content, truncated = _truncate_at_section(text, cap)
        return {"path": path, "content": content, "truncated": truncated}
    return None


def load_instructions(workspace: Path) -> dict:
    """加载全局 + 项目两层指令文件。

    规则：
      - 层内：主备二选一（AGENTS.md 优先，没有才用 CLAUDE.md）
      - 层间：全局 + 项目都加载，拼接（不是覆盖）

    返回：
      {"global": {...} 或 None, "project": {...} 或 None}
    """
    global_info = _load_first_found(_global_instruction_paths(), _MAX_GLOBAL_CHARS)
    project_paths = [workspace / name for name in _PROJECT_INSTRUCTION_FILES]
    project_info = _load_first_found(project_paths, _MAX_PROJECT_CHARS)
    return {"global": global_info, "project": project_info}


def build_injections(instructions: dict, skills: list) -> list:
    """把指令文件（全局+项目）+ 技能清单组装成 dynamic_injections。"""
    injections = []

    # ---- 指令文件：全局 + 项目两层拼接 ----
    parts = []
    src_paths = []
    g = instructions.get("global")
    p = instructions.get("project")
    if g:
        parts.append("【全局约定】\n" + g["content"])
        src_paths.append(str(g["path"]))
    if p:
        parts.append("【项目约定】\n" + p["content"])
        src_paths.append(str(p["path"]))

    if parts:
        header = "以下是项目约定（已截取关键部分）。如需完整细节，用 read_file 读取原文件：\n"
        header += "原文件路径：" + "、".join(src_paths) + "\n\n"
        injections.append({"role": "system", "content": header + "\n\n".join(parts)})

    # ---- 技能清单：只注入名字（极省 token）----
    if skills:
        names = ", ".join(s["name"] for s in skills)
        injections.append({"role": "system", "content": (
            f"用 load_skill 按名字加载需要的技能说明。当前共 {len(skills)} 个可用技能：\n{names}"
        )})
    return injections
