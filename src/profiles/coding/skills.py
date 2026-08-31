"""技能发现与加载（渐进式披露的共享逻辑）。

【这文件是干什么的】
  把"扫描技能、解析技能、读取技能"这套逻辑抽出来单独放，
  因为有两个地方都要用：
    1. CLI（cli.py）：启动时扫描技能清单注入 system prompt（L1 元数据层）
    2. load_skill 工具：模型按需加载某个技能的正文（L2 说明层）

【渐进式披露是什么】
  对标 Claude Code / Agent Skills 的三层按需加载，避免一次性
  把所有技能内容塞进上下文：
    L1 元数据层：只有 name + description，启动就加载（极小）
    L2 说明层：  完整 SKILL.md 正文，用到才加载（本模块的 load_skill_content）
    L3 资源层：  SKILL.md 引用的脚本/模板，用 read_file 按需读

【技能目录约定】
  在两个位置找 <目录名>/SKILL.md：
    1. 全局：~/.agents/skills/
    2. 项目：<workspace>/.agents/skills/
  同名时项目级覆盖全局级（越靠近项目越优先）。

  SKILL.md 用 YAML frontmatter 声明元数据（Claude Code 同款格式）：
    ---
    name: my-skill
    description: 这个技能是干什么的
    ---
    正文...

【谁会用】
  src/profiles/coding/cli.py（L1 清单）
  src/profiles/coding/tools/load_skill.py（L2 加载）
"""

from pathlib import Path

import yaml


# 单个技能正文注入/返回的上限（字符数）。
# 技能正文可能很长，截断防止一次加载吃掉太多上下文。
MAX_SKILL_CHARS = 8000


def discover_skills(workspace: Path) -> list[dict]:
    """扫描技能目录，返回技能清单（L1 元数据）。

    干什么：
      在全局和项目两个技能目录里找 <目录名>/SKILL.md，
      解析每个的 name/description，按 name 排序返回。
      同名时项目级覆盖全局级。

    参数：
      workspace — 项目根目录（用来定位项目级技能目录）

    返回：
      [{"name": ..., "description": ..., "path": Path}, ...]
      按 name 排序。

    谁调用：
      cli.py 启动时（生成 L1 清单）
      load_skill 工具执行时（按名字定位技能）
    """
    # 两个技能目录：先全局后项目，项目级同名会覆盖全局级
    skill_dirs = [
        Path.home() / ".agents" / "skills",   # 全局
        workspace / ".agents" / "skills",      # 项目级（后扫描，同名覆盖）
    ]

    found = {}  # name -> 技能信息字典
    for base in skill_dirs:
        if not base.is_dir():
            continue
        # 每个技能是一个子目录，里面有一个 SKILL.md
        for skill_md in sorted(base.glob("*/SKILL.md")):
            info = _parse_skill_md(skill_md)
            if info:
                found[info["name"]] = info

    return sorted(found.values(), key=lambda s: s["name"])


def _parse_skill_md(path: Path) -> dict | None:
    """解析单个 SKILL.md 的 YAML frontmatter，提取 name/description。

    干什么：
      读出文件开头的 --- 包围的 YAML 头，拿 name 和 description。
      没有 frontmatter 或解析失败时，用目录名当 name 兜底。

    参数：
      path — SKILL.md 的路径

    返回：
      {"name": ..., "description": ..., "path": path}
      文件读不了时返回 None。
    """
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None

    # 目录名兜底（比如 ~/.agents/skills/pdf-tools/SKILL.md -> "pdf-tools"）
    name = path.parent.name
    description = ""

    # 提取 frontmatter：文件以 --- 开头，到下一个 --- 结束
    if text.startswith("---"):
        parts = text.split("---", 2)  # ["", frontmatter, 正文]
        if len(parts) >= 3:
            try:
                meta = yaml.safe_load(parts[1]) or {}
                if isinstance(meta, dict):
                    name = str(meta.get("name", name))
                    description = str(meta.get("description", ""))
            except yaml.YAMLError:
                pass  # frontmatter 写坏了，用目录名兜底

    return {"name": name, "description": description, "path": path}


def load_skill_content(skills: list[dict], name: str) -> str | None:
    """按名字读取某个技能的正文（L2 说明层），截断后返回。

    干什么：
      在技能清单里找到名字匹配的技能，读出它的 SKILL.md 全文，
      截断到 MAX_SKILL_CHARS。

    参数：
      skills — discover_skills() 的返回值
      name   — 技能名（精确匹配）

    返回：
      SKILL.md 正文（截断到 MAX_SKILL_CHARS）；找不到或读失败返回 None。

    谁调用：
      cli.py 的 /skill 命令（用户手动加载）
      load_skill 工具（模型自主加载）
    """
    for skill in skills:
        if skill["name"] == name:
            try:
                text = skill["path"].read_text(encoding="utf-8", errors="ignore")
            except OSError:
                return None
            return text[:MAX_SKILL_CHARS]
    return None
