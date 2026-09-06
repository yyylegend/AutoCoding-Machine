"""recall_history 工具：从工作区近期会话的 JSONL 原文中检索历史片段。

【大白话】
  上下文压缩后旧消息变成了摘要，具体数字、路径、报错原文丢了。
  但 JSONL 文件里存着全部原文（ADR-0002：只增不减）。
  本工具让模型主动"翻笔记"——搜一下就能精确找回被压缩掉的细节。

【设计决策】
  - ADR-0004 原为「只搜当前会话」，2026-08-31 修订为跨会话召回：
    压缩丢掉的细节常常在上一次会话里，只搜当前会话不够用。
  - 只扫最近 RECALL_SESSION_LIMIT 个 session（按文件修改时间从新到旧），
    控制每次召回的成本（全量重建 BM25 索引，成本随 session 数线性涨）。
  - BM25 稀疏检索（关键词匹配），不用向量数据库
  - 分词：中文 bigram 滑窗 + 英文按空格/符号切
  - 正分结果按 BM25 分数排序，同分时优先较新的 session；
    同一 session 里相邻的重复命中合并成一条
  - 返回 top 5，每条带 session ID、文件时间、命中角色，
    以及命中消息前后各一条上下文
  - 最终输出统一过 clip_text，不超过 max_output_chars
  - 权限 AUTO（纯只读，无副作用）
"""

from src.engine.contracts import ToolCall, ToolResult
from src.engine.session_store import sessions_dir_for
from src.engine.tool_manager import tool
from src.profiles.coding.sandbox import WorkspaceSandbox
from src.profiles.coding.tools.helpers import (
    get_str_arg,
    invalid_result,
    ok_result,
)

from src.runtime.history import search_history, tokenize


@tool(name="recall_history", permission="auto")
def execute(
    tool_call: ToolCall,
    sandbox: WorkspaceSandbox,
    max_output_chars: int,
) -> ToolResult:
    """执行 recall_history：从工作区近期会话中检索相关片段。

    参数：
      query — 必填，搜索关键词（文件名、变量名、报错信息、中文短语都行）
    """
    query = get_str_arg(tool_call, "query")
    if query is None:
        return invalid_result(tool_call, "recall_history 需要参数 query")

    result = search_history(
        sessions_dir=getattr(sandbox, "sessions_dir", sessions_dir_for(sandbox.workspace)),
        query=query,
        max_output_chars=max_output_chars,
    )
    return ok_result(
        tool_call,
        result["content"],
        metadata={"matches": result["matches"]},
    )


# =====================================
# Schema
# =====================================

def schema() -> dict:
    """返回 OpenAI-compatible 的工具 schema。"""
    return {
        "type": "function",
        "function": {
            "name": "recall_history",
            "description": (
                "从本工作区近期会话的完整历史中搜索相关片段。"
                "当摘要信息不够、需要回忆早期对话或上次会话的细节时使用。"
                "支持搜文件名、变量名、报错关键词、中文短语。"
                "结果不够时换关键词再搜。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索关键词（如 MAX_RETRY、config.py、TypeError、重试配置）",
                    },
                },
                "required": ["query"],
            },
        },
    }
