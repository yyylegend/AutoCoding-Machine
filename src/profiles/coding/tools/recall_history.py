"""recall_history 工具：从当前会话的 JSONL 原文中检索历史片段。

【大白话】
  上下文压缩后旧消息变成了摘要，具体数字、路径、报错原文丢了。
  但 JSONL 文件里存着全部原文（ADR-0002：只增不减）。
  本工具让模型主动"翻笔记"——搜一下就能精确找回被压缩掉的细节。

【设计决策（ADR-0004）】
  - 只搜当前会话（最新的那个 JSONL 文件）
  - BM25 稀疏检索（关键词匹配），不用向量数据库
  - 分词：中文 bigram 滑窗 + 英文按空格/符号切
  - 返回 top 5，每条截到 500 字符
  - 权限 AUTO（纯只读，无副作用）
"""

import json
import re

from src.engine.contracts import ToolCall, ToolResult
from src.engine.session_store import latest_session_id, sessions_dir_for
from src.engine.tool_manager import tool
from src.profiles.coding.sandbox import WorkspaceSandbox
from src.profiles.coding.tools.helpers import (
    get_str_arg,
    invalid_result,
    ok_result,
)


# =====================================
# 分词：中文 bigram + 英文空格切
# =====================================

def tokenize(text: str) -> list:
    """把一段文本切成词列表（供 BM25 用）。

    策略：
      - 英文/数字/标识符：按空格和常见符号切（保留完整标识符如 MAX_RETRY）
      - 中文：相邻两字一组（bigram 滑窗）
    """
    tokens = []
    parts = re.findall(r'[a-zA-Z0-9_./\\-]+|[\u4e00-\u9fff]+', text)

    for part in parts:
        if '\u4e00' <= part[0] <= '\u9fff':
            # 中文：bigram 滑窗
            if len(part) == 1:
                tokens.append(part)
            else:
                for i in range(len(part) - 1):
                    tokens.append(part[i:i+2])
        else:
            # 英文/数字：整块当一个 token
            tokens.append(part.lower())

    return tokens


# =====================================
# 工具入口
# =====================================

@tool(name="recall_history", permission="auto")
def execute(
    tool_call: ToolCall,
    sandbox: WorkspaceSandbox,
    max_output_chars: int,
) -> ToolResult:
    """执行 recall_history：从会话历史中检索相关片段。

    参数：
      query — 必填，搜索关键词（文件名、变量名、报错信息、中文短语都行）
    """
    query = get_str_arg(tool_call, "query")
    if query is None:
        return invalid_result(tool_call, "recall_history 需要参数 query")

    # ---- 第 1 步：找当前会话文件（复用 session_store）----
    sessions_dir = sessions_dir_for(sandbox.workspace)
    session_id = latest_session_id(sessions_dir)
    if session_id is None:
        return ok_result(tool_call, "没找到会话文件（还没有对话历史）")

    jsonl_path = sessions_dir / f"{session_id}.jsonl"

    # ---- 第 2 步：读全部原始消息（坏行跳过）----
    messages = []
    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                messages.append(json.loads(line))
            except (json.JSONDecodeError, ValueError):
                continue

    if not messages:
        return ok_result(tool_call, "会话为空，没有可搜索的历史")

    # ---- 第 3 步：分词建索引 ----
    corpus = []
    for msg in messages:
        content = str(msg.get("content", "") or "")
        tool_calls = msg.get("tool_calls")
        if tool_calls:
            content += " " + str(tool_calls)
        corpus.append(tokenize(content))

    # ---- 第 4 步：BM25 打分 ----
    from rank_bm25 import BM25Okapi

    bm25 = BM25Okapi(corpus)
    query_tokens = tokenize(query)
    scores = bm25.get_scores(query_tokens)

    # ---- 第 5 步：取 top N，过滤零分 ----
    ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)
    results = []
    SNIPPET_CHARS = 500

    for idx, score in ranked:
        if score <= 0 or len(results) >= 5:
            break

        msg = messages[idx]
        role = msg.get("role", "?")
        content = str(msg.get("content", "") or "")
        # 截断
        if len(content) > SNIPPET_CHARS:
            content = content[:SNIPPET_CHARS] + "..."
        # 备注：JSONL 行号不是对话轮次
        results.append(f"[第{idx}轮 {role}]: {content}")

    if not results:
        return ok_result(tool_call, f"没找到与 \"{query}\" 相关的历史内容")

    output = "\n---\n".join(results)
    return ok_result(tool_call, output, metadata={"matches": len(results)})


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
                "从本次会话的完整历史中搜索相关片段。"
                "当摘要信息不够、需要回忆早期对话细节时使用。"
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
