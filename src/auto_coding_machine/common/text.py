"""共用的文本截断规则。"""

def clip_text(text: str, max_chars: int):
    """截断过长文本（保头 + 保尾）。

    返回两个值：
      1. 截断后的文本
      2. 有没有发生截断（True / False）

    截断策略（ADR-0004）：
      保留头部 40% + 尾部 60%，中间插入省略标注。
      尾部占比更大：pytest 结论、traceback 报错、命令退出状态多在尾部。

    例子：
        clip_text("hello", 100)
        -> ("hello", False)

        clip_text("x" * 200, 100)
        -> ("xxx...[\u7701\u7565 132 \u5b57\u7b26]...yyy", True)
    """
    if text is None:
        return "", False
    if max_chars <= 0:
        return text, False
    if len(text) <= max_chars:
        return text, False

    # 留一点位置给中间的省略标注（约 20 字符）
    keep = max_chars - 20
    if keep < 10:
        # max_chars 太小，直接硬切
        return text[:max_chars], True

    head_len = int(keep * 0.4)  # 头部 40%
    tail_len = keep - head_len   # 尾部 60%
    omitted = len(text) - head_len - tail_len

    head = text[:head_len]
    tail = text[-tail_len:] if tail_len > 0 else ""
    marker = f"\n...[省略 {omitted} 字符]...\n"

    return head + marker + tail, True
