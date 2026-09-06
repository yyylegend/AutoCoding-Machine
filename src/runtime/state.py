"""Profile 状态目录与旧路径迁移。

运行数据不是配置：这里保存会话、运行记录和输入历史的位置。
迁移只移动旧文件，不覆盖目标文件，也不删除任何记录。
"""

from pathlib import Path
import shutil

from src.common.logger import get_logger


logger = get_logger(__name__)


def migrate_legacy_coding_state(workspace) -> list[Path]:
    """把旧版 Coding 状态移到统一的 Profile 目录。

    旧版路径：``.autocoding/sessions``、``runs``、``input_history``。
    目标路径：``.autocoding/profiles/coding/`` 下对应位置。

    返回实际移动的源路径，方便启动日志和测试查看；遇到同名文件时保留源文件。
    """
    root = Path(workspace).resolve() / ".autocoding"
    target_root = root / "profiles" / "coding"
    moved: list[Path] = []

    for name in ("sessions", "runs"):
        source_dir = root / name
        if not source_dir.is_dir():
            continue
        target_dir = target_root / name
        target_dir.mkdir(parents=True, exist_ok=True)
        for source in sorted(source_dir.iterdir()):
            target = target_dir / source.name
            if target.exists():
                logger.warning("跳过重复的旧 Profile 文件：%s", source)
                continue
            try:
                shutil.move(str(source), str(target))
            except OSError as exc:
                logger.warning("旧 Profile 文件迁移失败：%s (%s)", source, exc)
                continue
            moved.append(source)
        # 只清理已经变空的旧目录，目录本身不是用户记录。
        try:
            source_dir.rmdir()
        except OSError:
            pass

    source_history = root / "input_history"
    target_history = target_root / "input_history"
    if source_history.is_file() and not target_history.exists():
        target_root.mkdir(parents=True, exist_ok=True)
        try:
            shutil.move(str(source_history), str(target_history))
        except OSError as exc:
            logger.warning("旧输入历史迁移失败：%s (%s)", source_history, exc)
        else:
            moved.append(source_history)

    return moved
