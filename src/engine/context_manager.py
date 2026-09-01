"""上下文管理器：组装 + 压缩。

【作用】
  token_utils → 精确 token 计数；
  context_manager → assemble + compact（安全切分点）。

【这文件是干什么的】
  对标 Claude Code agent loop 的两个阶段：
    1. assemble — 每轮调模型前，把消息按固定顺序拼好
    2. compact  — 消息太长时，安全截断

  合在一起的原因：
    SPEC 把上下文相关逻辑都归 ContextManager，
    assemble 和 compact 都是“管上下文”的事。

【assemble 部分】
  拼装顺序（Prompt Cache 友好）：
    1. system prompt（稳定前缀，每轮一样）
    2. 动态注入（Working Memory / Skill，偶尔变）
    3. 对话历史（user / assistant / tool，每轮都变）

  Prompt Cache 原理（大白话）：
    vLLM / OpenAI 会缓存消息前缀的计算结果。
    前缀不变就不用重新算，省时间。
    所以：稳定的放前面，变化的放后面。

【compact 部分】
  核心约束：不能切断 assistant(tool_calls) 和 tool(result) 的配对。

  消息结构示例：
    [system]           ← 永远保留
    [user]             ← 可以被截掉
    [assistant+tools]  ← 不能和下面的 tool 分开
    [tool result]      ← 不能和上面的 assistant 分开
    [user]             ← 最近的消息，保留

【当前阶段】
  Phase 3：assemble + 安全切分点截断。

【未来优化方向（Phase 6）】
  借鉴 SSD 分层思路：
    热层 L1：最近 5 组完整消息（context window）
    温层 L2：再往前 10 组的摘要（SSD）
    冷层 L3：DB 里的历史（HDD，按需召回）

  或者 Claude Code 的 GSSC：
    Gate      → 检查是否超限
    Select    → 找安全切分点
    Summarize → 摘要老消息
    Compress  → 截断单条结果

【谁会用】
  - src/engine/machine_loop.py 每轮调模型前调 maybe_compact()
  - CLI / executor 调 assemble() 拼初始消息
"""

# ============================================================
# 导入依赖
# ============================================================

from __future__ import annotations

import hashlib
import re
import time

from src.common.logger import get_logger
from src.common.token_utils import count_tokens_old_style as count_tokens

logger = get_logger(__name__)

# 摘要失败后的冷却时长（秒）：同一批旧消息失败后 10 分钟内不再重试，
# 直接复用确定性摘录，避免每轮压缩都白调一次注定失败的 LLM。
SUMMARY_FAILURE_COOLDOWN_SEC = 600

# 确定性摘录的总字符上限
EXCERPT_MAX_CHARS = 4000


# ============================================================
#  assemble：上下文组装
# ============================================================

def assemble(
    system_prompt: str,
    history: list[dict],
    dynamic_injections: list[dict] | None = None,
) -> list[dict]:
    """组装模型要看的完整消息列表。

    参数：
      system_prompt      — 系统提示（稳定前缀，每轮一样）
      history            — 对话历史（user / assistant / tool 消息）
                           注意：history 里不应该包含 system 消息，
                           system 由本函数统一放第一条。
      dynamic_injections — 可选，动态注入的消息列表。
                           比如 Working Memory 摘要、Skill 提示。
                           它们会被放在 system 之后、history 之前。
                           格式：[{"role": "system", "content": "..."}]

    返回：
      拼好的消息列表，顺序：
        [system_prompt] + [dynamic_injections] + [history]

    用法例子：
        messages = assemble(
            system_prompt="你是一个 Coding Agent...",
            history=conversation_history,
            dynamic_injections=[
                {"role": "system", "content": "Working Memory: 上次读了 main.py"},
            ],
        )
        response = model_fn(messages)
    """
    # ---- 第 1 层：稳定前缀（system prompt）----
    # 永远放第一条，保证 Prompt Cache 前缀稳定
    assembled = [{"role": "system", "content": system_prompt}]

    # ---- 第 2 层：动态注入（Working Memory / Skill 等）----
    # 偶尔变化，放在 system 之后、历史之前
    if dynamic_injections:
        for injection in dynamic_injections:
            assembled.append(injection)

    # ---- 第 3 层：对话历史（每轮都变）----
    # user / assistant / tool 消息按时间顺序追加
    assembled.extend(history)

    return assembled


# ============================================================
#  ContextManager：上下文压缩
# ============================================================


class ContextManager:
    """上下文管理器。

    用法例子：
        ctx_mgr = ContextManager(max_messages=20)
        messages = ctx_mgr.maybe_compact(messages)
        response = model_fn(messages)
    """

    def __init__(self, max_messages: int | None = 20, max_tokens: int | None = None, summarizer_fn=None):
        """初始化。

        参数：
          max_messages  — 最多保留多少条消息（包括 system）。
                          传 None 则关闭条数维度，只看 token 预算
                          （Coding 场景用：工具调用消息多，20 条约等于 3 轮，
                          按条数压缩太早，信息白白丢掉）。
          max_tokens    — 可选的 token 预算上限。
                          传 None（默认）时只按消息条数触发压缩（旧行为）。
                          传一个整数时：消息总 token 超过它也会触发压缩，
                          并按 token 预算决定保留多少近期消息。
                          两个维度任一超限就压缩。
          summarizer_fn — 可选的摘要函数。
                          传 None（默认）时只做纯截断，行为和旧版完全一致。
                          传一个函数时：输入是被切掉的旧消息列表，
                          返回一段摘要字符串。
                          摘要失败或返回空时降级为确定性历史摘录，
                          并为同一批旧消息进入 10 分钟失败冷却。

        大白话：
          超过 max_messages 或 max_tokens 就开始截断。
          max_tokens 是把上下文当稀缺资源管：哪怕条数没超，
          只要 token 总量超预算就压缩（对应 1M 上下文也要省着用的思路）。
          如果传了 summarizer_fn，截断前会把要丢的旧消息
          让它总结成摘要保留下来，避免信息全丢。
        """
        self.max_messages = max_messages
        self.max_tokens = max_tokens
        self.summarizer_fn = summarizer_fn
        # 摘要的进程内缓存：旧消息指纹 → 摘要文本。
        # 为什么需要：compact 结果不落盘（ADR-0002），CLI 每轮从 JSONL
        # 重建完整历史，同样的旧消息每轮都会重新被切一次。
        # 没缓存的话每轮都多花一次摘要 LLM 调用，而且每次总结还不一样。
        # 只存在内存：不落盘，不违反"session 只存原始历史"的约定。
        self._summary_cache: dict[str, str] = {}

        # 摘要失败冷却表：旧消息指纹 → 失败时间戳。
        # 摘要失败也必须记下来，否则每轮压缩都会重试一次注定失败的调用。
        self._failure_cache: dict[str, float] = {}

        # 只读诊断状态：描述最近一次 maybe_compact 调用的行为。
        # mode 取值："none"（没压缩）/ "truncated"（纯截断）/
        #            "summary"（LLM 摘要成功）/ "excerpt"（降级为确定性摘录）
        self.last_compaction_mode = "none"
        self.last_compaction_error = None   # 摘要失败时的错误描述
        self.last_dropped_count = 0         # 本次压缩丢掉的消息数

    def maybe_compact(self, messages: list, force: bool = False) -> list:
        """如果消息太多，就安全截断（四步流程）。

        参数：
          messages — 当前消息列表
          force    — True 时跳过 Gate 强制压缩（/compact 手动触发用）

        返回：
          压缩后的消息列表

        四步流程：
          1. Gate — 没超限就原样返回（不截断）
          2. Select — 分离 system，找安全切分点
          3. Summarize — 如果传了 summarizer_fn 且旧消息≥4 条，
                       调用它生成摘要消息（插入在 system 后、近期消息前）
          4. Truncate — 组装：system + [摘要] + recent

        规则：
          1. 没超限 -> 原样返回
          2. 超限了 -> 保留 system 前缀 + 最近几组完整消息
          3. 切分点必须在“安全边界”上（不切断 tool 配对）
          4. 摘要是可选的：summarizer_fn 不存在、旧消息<4 → 纯截断；
             摘要异常或为空 → 降级为确定性摘录（任务不中断），
             且同一批旧消息进入 10 分钟失败冷却，冷却期内不再调 LLM；
             force=True（手动 /compact）绕过冷却立即重试。

        什么是“安全边界”：
          一条消息如果是 role="tool"，它必须和前面的 assistant(tool_calls) 在一起。
          所以切分点不能落在 tool 消息上，也不能落在带 tool_calls 的 assistant 上。
          安全的切分点：user 消息、不带 tool_calls 的 assistant 消息。
        """
        # ---- 诊断状态复位：每次调用重新描述"这一次"的行为 ----
        self.last_compaction_mode = "none"
        self.last_compaction_error = None
        self.last_dropped_count = 0

        # ---- Gate：两个维度任一超限才压缩（force=True 时跳过）----
        # 维度 1：消息条数超过 max_messages（传 None 则关闭这个维度）
        # 维度 2：token 总量超过 max_tokens（把上下文当稀缺资源管）
        if force:
            # 手动触发：强制走 token 预算切分策略
            over_tokens = True
        else:
            over_count = self.max_messages is not None and len(messages) > self.max_messages
            over_tokens = self.max_tokens is not None and count_tokens(messages) > self.max_tokens
            if not over_count and not over_tokens:
                return messages

        # ---- 第 1 步：分离 system 前缀 ----
        # system 消息（可能不止一条，比如 assemble 注入的动态 system）永远保留
        system_prefix = []
        rest = messages
        for i, msg in enumerate(messages):
            if msg.get("role") == "system":
                system_prefix.append(msg)
            else:
                # 遇到第一条非 system 消息，停止
                rest = messages[i:]
                break
        else:
            # 全是 system（极端情况）
            return messages

        # ---- 第 2 步：算保留区起始下标 ----
        if force:
            # 手动压缩：和自动压缩同一套 token 切分算法，只是预算收紧到 30%。
            # 为什么不能直接用满预算：没超限时全部消息都塞得下，永远切不出东西。
            if self.max_tokens is None:
                return messages  # 没有 token 预算概念，无法收紧，不动
            system_tokens = count_tokens(system_prefix)
            budget = max(int(self.max_tokens * 0.3) - system_tokens - 200, 0)
            cut_start = self._token_budget_cut(rest, budget)
            if cut_start <= 0:
                return messages  # 对话太短，30% 预算也全塞得下，没什么可压
        elif self.max_tokens is not None and over_tokens:
            # token 预算模式：给 system 和摘要留出余地后，剩余预算给近期消息
            system_tokens = count_tokens(system_prefix)
            budget = max(self.max_tokens - system_tokens - 200, 0)  # 200 预留给摘要消息
            cut_start = self._token_budget_cut(rest, budget)
        else:
            # 消息数模式（原有逻辑）
            keep_count = self.max_messages - len(system_prefix)
            if keep_count < 3:
                keep_count = 3  # 至少保留最近 3 条
            if len(rest) <= keep_count:
                return messages  # 去掉 system 后本来就没超
            cut_start = len(rest) - keep_count

        # ---- 第 3 步：找安全切分点 ----
        # 从 cut_start 往后找第一个安全边界（不切断 tool 配对）
        safe_cut = self._find_safe_boundary(rest, cut_start)

        # safe_cut 之前的是要丢弃的旧消息，之后的是要保留的近期消息
        old_messages = rest[:safe_cut]
        recent = rest[safe_cut:]

        # ---- 第 4 步：摘要（可选，带缓存与失败冷却）----
        # 如果传了 summarizer_fn 且旧消息 >= 4 条，尝试 LLM 摘要。
        # 三层兜底：LLM 摘要 → 冷却期内复用摘录 → 摘要失败降级为摘录。
        # 摘录是确定性生成的（直接从旧消息里挑重点），不依赖 LLM，
        # 保证摘要功能坏了任务也不会中断。
        summary_msg = None
        if self.summarizer_fn is not None and len(old_messages) >= 4:
            fingerprint = self._fingerprint(old_messages)
            summary_text = ""

            if not force and self._in_failure_cooldown(fingerprint):
                # 冷却期内：不重复请求模型，直接复用确定性摘录。
                # force=True（手动 /compact）会绕过冷却，允许立即重试。
                summary_text = self._build_excerpt(old_messages)
                self.last_compaction_mode = "excerpt"
            else:
                summary_text, summary_error = self._summarize_with_cache(old_messages)
                if summary_text:
                    self.last_compaction_mode = "summary"
                else:
                    # 摘要失败：记入冷却表，降级为确定性摘录，任务继续
                    self._failure_cache[fingerprint] = time.time()
                    self.last_compaction_mode = "excerpt"
                    self.last_compaction_error = summary_error
                    summary_text = self._build_excerpt(old_messages)
                    logger.info("摘要失败，已降级为确定性摘录: %s", summary_error)

            if summary_text:
                summary_msg = {
                    "role": "user",
                    "content": "[历史摘要] " + summary_text,
                }
        else:
            # 没传 summarizer 或旧消息太少：纯截断
            self.last_compaction_mode = "truncated"

        self.last_dropped_count = len(old_messages)

        # ---- 第 5 步：重新组装 ----
        # 顺序：system 前缀 + 摘要消息（如有）+ 保留的近期消息
        if summary_msg is not None:
            return system_prefix + [summary_msg] + recent
        return system_prefix + recent

    def _fingerprint(self, old_messages: list) -> str:
        """给旧消息算指纹（内容 md5），用作缓存和冷却的 key。

        指纹只看 role + content + tool_calls：这三样不变，
        就认为是同一批旧消息，摘要/失败状态可以直接复用。
        """
        raw = ""
        for m in old_messages:
            raw += str(m.get("role", "")) + str(m.get("content", "")) + str(m.get("tool_calls", ""))
        return hashlib.md5(raw.encode("utf-8")).hexdigest()

    def _in_failure_cooldown(self, fingerprint: str) -> bool:
        """查询某批旧消息的摘要是否还在失败冷却期内。

        冷却期内返回 True（调用方直接用摘录，不再调 LLM）。
        冷却过期后顺手清理记录，下次失败会重新计时。
        """
        failed_at = self._failure_cache.get(fingerprint)
        if failed_at is None:
            return False
        if time.time() - failed_at > SUMMARY_FAILURE_COOLDOWN_SEC:
            del self._failure_cache[fingerprint]  # 冷却过了，清掉重来
            return False
        return True

    def _summarize_with_cache(self, old_messages: list) -> tuple:
        """带缓存地调用摘要函数。

        干什么：
          1. 给旧消息算指纹（内容 hash）
          2. 缓存里有 → 直接返回，不再调 LLM
          3. 没有 → 调摘要函数，结果存进缓存

        返回：
          (摘要字符串, 错误描述)。
          成功：("摘要文本", None)
          失败：("", "错误原因") —— 调用方降级为确定性摘录
        """
        key = self._fingerprint(old_messages)

        if key in self._summary_cache:
            return self._summary_cache[key], None

        try:
            summary_text = self.summarizer_fn(old_messages) or ""
        except Exception as exc:
            # 摘要函数出错：把错误带给调用方（会记入冷却 + 诊断状态）
            return "", str(exc)

        if summary_text:
            # 缓存上限 32 条，满了就清空重来（简单粗暴但够用，
            # 正常会话里同一段旧消息的指纹只有几个）
            if len(self._summary_cache) >= 32:
                self._summary_cache.clear()
            self._summary_cache[key] = summary_text
            return summary_text, None

        # 摘要函数正常返回但内容为空：也算失败
        return "", "摘要返回为空"

    def _build_excerpt(self, old_messages: list) -> str:
        """从旧消息里确定性挑出重点，生成摘录（不依赖 LLM）。

        摘什么（按价值排序，总长不超过 EXCERPT_MAX_CHARS）：
          1. 原始目标   — 第一条非空用户消息（最多 800 字符）
          2. 近期决定   — 最后三条有内容的 user/assistant（各最多 600）
          3. 报错现场   — 最后两条含错误关键词的工具结果（各最多 500）
          4. 涉及文件   — 最多 8 个去重后的文件路径

        被摘掉详细内容仍完整躺在 JSONL 里，模型可用 recall_history 找回。
        """
        parts = ["（仅作历史参考，以后续最新用户消息为准）"]

        # 1. 原始目标：第一条非空用户消息
        for m in old_messages:
            content = str(m.get("content") or "").strip()
            if m.get("role") == "user" and content:
                parts.append("【原始目标】" + content[:800])
                break

        # 2. 近期决定：最后三条有内容的 user/assistant 消息
        dialogs = []
        for message in old_messages:
            role = message.get("role")
            content = str(message.get("content") or "").strip()
            if role in ("user", "assistant") and content:
                dialogs.append(message)
        if dialogs:
            parts.append("【近期决定】")
            for m in dialogs[-3:]:
                parts.append("- [" + m.get("role", "?") + "] "
                             + str(m.get("content"))[:600])

        # 3. 报错现场：最后两条含错误关键词的工具结果
        keywords = ("error", "exception", "failed", "失败", "报错")
        errors = []
        for m in old_messages:
            if m.get("role") != "tool":
                continue
            lowered = str(m.get("content") or "").lower()
            if any(k in lowered for k in keywords):
                errors.append(m)
        if errors:
            parts.append("【报错现场】")
            for m in errors[-2:]:
                parts.append("- " + str(m.get("content"))[:500])

        # 4. 涉及文件：从消息内容里抓路径样式，去重取前 8 个
        paths = []
        for m in old_messages:
            text = str(m.get("content") or "") + str(m.get("tool_calls") or "")
            for p in re.findall(r"[A-Za-z0-9_\-./\\]+\.[A-Za-z0-9]{1,5}", text):
                if p not in paths:
                    paths.append(p)
        if paths:
            parts.append("【涉及文件】" + " ".join(paths[:8]))

        # 总长封顶：超了硬截断（摘录本来就该短，截断是最后防线）
        excerpt = "\n".join(parts)
        if len(excerpt) > EXCERPT_MAX_CHARS:
            excerpt = excerpt[:EXCERPT_MAX_CHARS]
        return excerpt

    def _token_budget_cut(self, messages: list, budget: int) -> int:
        """按 token 预算从后往前累加，返回保留区起始下标。

        干什么：
          从最后一条消息往前加 token，加到再放一条就超 budget 为止。
          至少保留 1 条（哪怕单条就超预算）。

        参数：
          messages — 消息列表（不含 system）
          budget   — 给近期消息的 token 预算

        返回：
          保留区起始下标（该下标及之后的消息保留）
        """
        total = 0
        keep_start = len(messages)
        for i in range(len(messages) - 1, -1, -1):
            msg_tokens = count_tokens([messages[i]])
            # 再加就超了，且已经至少保留了一条 → 停
            if total + msg_tokens > budget and keep_start < len(messages):
                break
            total += msg_tokens
            keep_start = i
        return keep_start

    def _find_safe_boundary(self, messages: list, start: int) -> int:
        """从 start 位置往后找第一个安全切分点。

        什么是安全切分点：
          从这个位置切开，不会留下孤立的 tool result。

        规则：
          - role="tool" 的消息不能做切分点（它是别人的结果）
          - 带 tool_calls 的 assistant 不能做切分点（它的结果在后面）
          - role="user" 或不带 tool_calls 的 assistant 可以做切分点

        参数：
          messages — 消息列表（不含 system）
          start    — 初始切分位置（从这里往后找）

        返回：
          安全切分位置（>= start）
        """
        i = start
        while i < len(messages):
            msg = messages[i]
            role = msg.get("role", "")

            # tool 消息不能做切分点：它是前面 assistant 的结果
            if role == "tool":
                i += 1
                continue

            # 带 tool_calls 的 assistant 不能做切分点：它的结果在后面
            if role == "assistant" and msg.get("tool_calls"):
                i += 1
                continue

            # 找到安全边界：user 或不带 tool_calls 的 assistant
            return i

        # 如果找到末尾都没找到安全点，就保留全部
        return start
