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

import json
import re
import time

from src.engine.contracts import ToolCall, ToolResult
from src.engine.session_store import list_sessions, sessions_dir_for
from src.engine.tool_manager import tool
from src.profiles.coding.sandbox import WorkspaceSandbox
from src.profiles.coding.tools.helpers import (
    clip_text,
    get_str_arg,
    invalid_result,
    ok_result,
)

# 最多扫描多少个 session（按修改时间从新到旧取）
# 上限是为了控制成本：每次召回都要读文件 + 重建 BM25 索引
RECALL_SESSION_LIMIT = 10

# 单条消息片段截到多少字符（太长会把 5 个命中的配额吃光）
SNIPPET_CHARS = 500

# 最多返回多少个命中
MAX_HITS = 5


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
# 语料收集：把多个 session 读成一个语料表
# =====================================

def _load_corpus(sessions_dir) -> list:
    """读最近几个 session 的全部消息，摊平成一条统一的语料表。

    返回：
      [{"session_id", "mtime", "idx", "msg"}, ...]
      idx 是该消息在本 session 内的行号（用于取前后文）。
      坏行（半截 JSON）直接跳过，不报错。
    """
    corpus = []
    # list_sessions 已经按修改时间从新到旧排好序
    for session in list_sessions(sessions_dir)[:RECALL_SESSION_LIMIT]:
        jsonl_path = sessions_dir / (session["id"] + ".jsonl")
        idx = 0
        try:
            f = open(jsonl_path, "r", encoding="utf-8")
        except OSError:
            continue  # 列目录和读文件之间文件被删了：跳过这个 session
        with f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue  # 坏行跳过（多半是上次崩溃留下的半截）
                corpus.append({
                    "session_id": session["id"],
                    "mtime": session["mtime"],
                    "idx": idx,
                    "msg": msg,
                })
                idx += 1
    return corpus


def _merge_adjacent(hits: list) -> list:
    """把同一 session 里行号相邻的命中合并成一条。

    为什么要合并：
      用户连续两句都含关键词时，两句各命中一次，
      拆成两条会浪费 top 5 配额，合并成一条信息量不变。

    参数：
      hits — [{"session_id", "mtime", "idx", "score"}, ...]（已按 session+idx 排序）

    返回：
      合并后的命中列表（每组取最高分，按 score 排序交给调用方）
    """
    merged = []
    for hit in hits:
        prev = merged[-1] if merged else None
        # 同一个 session 且行号紧挨着 → 并入上一条（保留最高分）
        if (prev is not None
                and prev["session_id"] == hit["session_id"]
                and prev["last_idx"] + 1 == hit["idx"]):
            prev["last_idx"] = hit["idx"]
            prev["score"] = max(prev["score"], hit["score"])
        else:
            merged.append({
                "session_id": hit["session_id"],
                "mtime": hit["mtime"],
                "first_idx": hit["idx"],
                "last_idx": hit["idx"],
                "score": hit["score"],
            })
    return merged


def _coverage_hits(corpus: list, query_tokens: list) -> list:
    """BM25 失效时的兜底：按"命中了几个查询词"打分。

    为什么需要兜底（这个坑很隐蔽）：
      BM25 的 IDF 是 log((总文档数 - 命中数 + 0.5) / (命中数 + 0.5))。
      语料很小时它会算成 0 甚至负数——比如总共 2 条消息、关键词命中 1 条，
      IDF = log(1.5/1.5) = 0，明明存在的关键词也搜不到。
      新开的短会话、只有两三个 session 的工作区都会撞上这个情况。

    兜底策略很简单：数一数这条消息里出现了几个查询词，
    出现得越多排越前。不依赖语料规模，永远稳定。

    返回：
      和 BM25 命中同结构的列表，score 是覆盖到的查询词个数
    """
    hits = []
    for entry in corpus:
        msg = entry["msg"]
        text = (str(msg.get("content", "") or "")
                + " " + str(msg.get("tool_calls") or "")).lower()
        covered = 0
        for token in query_tokens:
            if token in text:
                covered += 1
        if covered > 0:
            hits.append({
                "session_id": entry["session_id"],
                "mtime": entry["mtime"],
                "idx": entry["idx"],
                "score": float(covered),
            })
    return hits


def _format_hit(hit: dict, corpus: list) -> str:
    """把一个命中渲染成文本块：命中消息 + 前后各一条上下文。

    语料是按 session 分段连续存放的，所以同一 session 内
    行号 idx-1 / idx / idx+1 的消息直接按行号找回来即可。
    """
    session_id = hit["session_id"]
    file_time = time.strftime("%Y-%m-%d %H:%M", time.localtime(hit["mtime"]))

    # 先把这个 session 的消息按行号摆好，方便取前后文
    session_msgs = {}
    for entry in corpus:
        if entry["session_id"] == session_id:
            session_msgs[entry["idx"]] = entry["msg"]

    # 命中行：合并命中时可能不止一行（first_idx 到 last_idx 全是命中）
    hit_rows = set(range(hit["first_idx"], hit["last_idx"] + 1))

    # 要展示的行 = 命中行 + 前后各一条。用 set 去重，
    # 否则相邻两行都命中时，它们的"前后文"会互相重复（同一行输出两遍）。
    rows = set(hit_rows)
    rows.add(hit["first_idx"] - 1)
    rows.add(hit["last_idx"] + 1)

    lines = ["[session " + session_id + " | " + file_time + "]"]
    for idx in sorted(rows):
        msg = session_msgs.get(idx)
        if msg is None:
            continue
        role = str(msg.get("role", "?"))
        content = str(msg.get("content", "") or "")
        if len(content) > SNIPPET_CHARS:
            content = content[:SNIPPET_CHARS] + "..."
        # 命中行加标记，上下文行不标（备注：JSONL 行号不是对话轮次）
        mark = "·命中" if idx in hit_rows else ""
        lines.append("  [第" + str(idx) + "轮 " + role + mark + "]: " + content)
    return "\n".join(lines)


# =====================================
# 工具入口
# =====================================

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

    sessions_dir = sessions_dir_for(sandbox.workspace)
    corpus = _load_corpus(sessions_dir)
    if not corpus:
        return ok_result(tool_call, "没找到会话文件（还没有对话历史）")

    # ---- 分词建索引 + BM25 打分 ----
    from rank_bm25 import BM25Okapi

    docs = []
    for entry in corpus:
        msg = entry["msg"]
        content = str(msg.get("content", "") or "")
        tool_calls = msg.get("tool_calls")
        if tool_calls:
            content += " " + str(tool_calls)
        docs.append(tokenize(content))

    bm25 = BM25Okapi(docs)
    scores = bm25.get_scores(tokenize(query))

    # ---- 取所有正分命中，按 session + 行号排好再做相邻合并 ----
    hits = []
    for i, score in enumerate(scores):
        if score > 0:
            hits.append({
                "session_id": corpus[i]["session_id"],
                "mtime": corpus[i]["mtime"],
                "idx": corpus[i]["idx"],
                "score": score,
            })
    if not hits:
        # 语料太小时 BM25 的 IDF 会退化成 0（见 _coverage_hits 的说明），
        # 这时改用关键词覆盖匹配兜底，保证"明明有却搜不到"的情况不发生
        hits = _coverage_hits(corpus, tokenize(query))

    if not hits:
        return ok_result(tool_call, "没找到与 \"" + query + "\" 相关的历史内容")

    hits.sort(key=lambda h: (h["session_id"], h["idx"]))
    merged = _merge_adjacent(hits)

    # ---- 排序：分数高的在前；同分时较新的 session 在前 ----
    merged.sort(key=lambda h: (-h["score"], -h["mtime"]))
    merged = merged[:MAX_HITS]

    # ---- 渲染 + 统一裁剪（不超过 max_output_chars）----
    blocks = [_format_hit(hit, corpus) for hit in merged]
    raw_output = "\n---\n".join(blocks)
    output, truncated = clip_text(raw_output, max_output_chars)
    if truncated:
        notice = "\n（结果过长已截断，可换更精确的关键词再搜）"
        if max_output_chars > 0:
            # 给提示预留空间，并做最后一道硬上限，确保 ToolResult 真正不超预算。
            content_budget = max(max_output_chars - len(notice), 0)
            output, _ = clip_text(raw_output, content_budget)
            output = (output[:content_budget] + notice)[:max_output_chars]
        else:
            output += notice

    return ok_result(tool_call, output, metadata={"matches": len(merged)})


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
