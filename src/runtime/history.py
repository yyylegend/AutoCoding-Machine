"""从 JSONL 会话检索历史；工具和自动召回共用同一实现。"""

import json
import re
import time
from src.engine.session_store import list_sessions
from src.common.text import clip_text

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

def _load_corpus(sessions_dir, exclude_session_id: str | None = None) -> list:
    """读最近几个 session 的全部消息，摊平成一条统一的语料表。

    返回：
      [{"session_id", "mtime", "idx", "msg"}, ...]
      idx 是该消息在本 session 内的行号（用于取前后文）。
      坏行（半截 JSON）直接跳过，不报错。
    """
    corpus = []
    # list_sessions 已经按修改时间从新到旧排好序
    for session in list_sessions(sessions_dir)[:RECALL_SESSION_LIMIT]:
        if session["id"] == exclude_session_id:
            continue
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


def _coverage_hits(corpus: list, query_tokens: list, min_token_matches: int = 1) -> list:
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
    unique_query_tokens = set(query_tokens)
    for entry in corpus:
        msg = entry["msg"]
        text = (str(msg.get("content", "") or "")
                + " " + str(msg.get("tool_calls") or "")).lower()
        covered = 0
        for token in unique_query_tokens:
            if token in text:
                covered += 1
        if covered >= min_token_matches:
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

def search_history(
    sessions_dir,
    query: str,
    max_output_chars: int,
    max_hits: int = MAX_HITS,
    exclude_session_id: str | None = None,
    min_token_matches: int = 1,
) -> dict:
    """搜索会话历史，返回适合直接注入或展示的文本。

    recall_history 工具和 ContextSelector 共用这一入口，避免两套 BM25
    逻辑逐渐产生不同结果。exclude_session_id 用于自动召回时排除当前会话。
    """
    corpus = _load_corpus(sessions_dir, exclude_session_id=exclude_session_id)
    if not corpus:
        return {"content": "没找到会话文件（还没有对话历史）", "matches": 0}

    # ---- 分词建索引 + BM25 打分 ----
    from rank_bm25 import BM25Okapi

    query_tokens = tokenize(query)
    if not query_tokens:
        return {"content": "没找到与当前问题相关的历史内容", "matches": 0}
    required_matches = min(min_token_matches, len(set(query_tokens)))

    docs = []
    for entry in corpus:
        msg = entry["msg"]
        content = str(msg.get("content", "") or "")
        tool_calls = msg.get("tool_calls")
        if tool_calls:
            content += " " + str(tool_calls)
        docs.append(tokenize(content))

    bm25 = BM25Okapi(docs)
    scores = bm25.get_scores(query_tokens)

    # ---- 取所有正分命中，按 session + 行号排好再做相邻合并 ----
    hits = []
    for i, score in enumerate(scores):
        token_matches = len(set(docs[i]).intersection(set(query_tokens)))
        if score > 0 and token_matches >= required_matches:
            hits.append({
                "session_id": corpus[i]["session_id"],
                "mtime": corpus[i]["mtime"],
                "idx": corpus[i]["idx"],
                "score": score,
            })
    if not hits:
        # 语料太小时 BM25 的 IDF 会退化成 0（见 _coverage_hits 的说明），
        # 这时改用关键词覆盖匹配兜底，保证"明明有却搜不到"的情况不发生
        hits = _coverage_hits(corpus, query_tokens, min_token_matches=required_matches)

    if not hits:
        return {"content": "没找到与 \"" + query + "\" 相关的历史内容", "matches": 0}

    hits.sort(key=lambda h: (h["session_id"], h["idx"]))
    merged = _merge_adjacent(hits)

    # ---- 排序：分数高的在前；同分时较新的 session 在前 ----
    merged.sort(key=lambda h: (-h["score"], -h["mtime"]))
    merged = merged[:max_hits]

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

    return {"content": output, "matches": len(merged)}
