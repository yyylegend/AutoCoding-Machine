"""上下文管理器的统一构造，只此一处。

【这文件是干什么的】
  本文件把"ContextManager 该怎么配"收编成单一真相源：
    - init_coding_tokenizer()      — 初始化 tokenizer（根据 .env 模型名）
    - make_summarizer()            — 造摘要函数（带开关）
    - resolve_token_budget()       — 算 token 预算（问供应商，失败用默认值）
    - build_context_manager()      — 一步到位，两边都调这个

  以后想改压缩配置，天下只有这一个地方可改。

【谁会用】
  - cli.py       启动时
"""

from src.common.llm_client import chat, fetch_model_context_window
from src.common.token_utils import init_tokenizer
from src.config.settings import settings
from src.engine import ContextManager


# 兜底的上下文 token 预算：供应商 API 查不到窗口大小时用。
# 故意设得保守——哪怕模型有 1M 上下文也不该用满：
#   注意力衰减（lost in the middle）、成本、Prompt Cache 命中率都要求省着用。
DEFAULT_TOKEN_BUDGET = 128000

# 查到窗口后留给输出的比例：预算 = 窗口 * 0.8（留 20% 给模型生成）
BUDGET_RATIO = 0.8

# resolve_token_budget() 的进程内缓存：
# 多次构造 ContextManager 时复用模型上下文窗口查询结果。
# 模型窗口在进程生命周期内不会变，问一次就够。
_budget_cache = None


# ============================================================
# 工具：初始化 tokenizer
# ============================================================

def init_coding_tokenizer():
    """根据 .env 中的模型名初始化 Tokenizer。

    作用：
      Runtime 启动前先初始化 tokenizer（token 计数依赖）。
      以前这两处各自写了同样的代码，这里收编成单一真相源。

    逻辑：
      - 优先 CODING_LLM_MODEL → 回退到 LLM_MODEL
      - 调用 token_utils.init_tokenizer()

    用法例子：
        init_coding_tokenizer()
    """
    model_name = settings.CODING_LLM_MODEL or settings.LLM_MODEL
    init_tokenizer(model_name)


def make_summarizer():
    """构造上下文摘要函数，供 ContextManager 压缩时调用。

    干什么：
      返回一个函数，输入消息列表，调用 LLM 总结成一句话。
      如果 CONTEXT_SUMMARY_ENABLED 为 False，直接返回 None（不启用摘要）。

    谁调用：
      build_context_manager()；测试也会直接调它验证开关行为。

    返回：
      - 开关关闭时：None
      - 开关开启时：一个函数 summarize(messages) -> str
    """
    if not settings.CONTEXT_SUMMARY_ENABLED:
        # 开关未开启，直接返回 None
        return None

    def summarize(messages: list) -> str:
        """对旧消息进行 LLM 摘要。

        参数：
          messages — 被切掉的旧消息列表

        返回：
          摘要字符串（3-5 句，结构化）

        实现细节：
          - 每条消息最多取前 500 字符（工具输出的关键信息常在中段，
            200 太短会把文件路径、报错信息切掉）
          - 输入总长封顶 8000 字符（超长时保留头尾、丢中间，
            防止摘要请求自己就撞上下文上限）
          - 要求 3-5 句结构化摘要（做了什么/改了哪些文件/当前结论），
            一句话装不下几十条消息的信息量
          - 调用 Coding 模型（使用 CODING_LLM_* 配置），max_tokens 限 300
        """
        # ---- 步骤 1：整理输入文本（每条前 500 字符）----
        lines = []
        for m in messages:
            role = m.get("role", "")
            content = m.get("content", "") or ""
            lines.append(role + ": " + content[:500])
        text = "\n".join(lines)

        # ---- 步骤 2：输入总长封顶 8000 字符 ----
        # 超长时保留开头 4000 + 结尾 4000（头尾比中间重要：
        # 开头是任务目标，结尾是最新进展）
        if len(text) > 8000:
            text = text[:4000] + "\n（中间内容已省略）\n" + text[-4000:]

        # ---- 步骤 3：构造 prompt（要结构化摘要，不是一句话）----
        prompt = (
            "用 3-5 句话概括以下对话，必须覆盖："
            "①用户让做什么 ②实际做了什么（读/改了哪些文件、跑了什么命令）"
            "③关键结论或报错。只写事实，不写废话：\n" + text
        )

        # ---- 步骤 4：调用 LLM（Coding 模型配置）----
        # chat() 在 tools=None 时返回 str
        resp = chat(
            [{"role": "user", "content": prompt}],
            max_tokens=300,  # 3-5 句的余地，再长就是啰嗦了
            model=settings.CODING_LLM_MODEL,
            base_url=settings.CODING_LLM_BASE_URL,
            api_key=settings.CODING_LLM_API_KEY,
            auth_type=settings.CODING_LLM_AUTH_TYPE,
            timeout=settings.CODING_LLM_TIMEOUT_SEC,
        )
        # resp 是 str（因为 tools=None），没有内容时可能返回空字符串或 None
        return resp or ""

    return summarize


def resolve_token_budget() -> int:
    """算出上下文 token 预算（进程内只真正查一次，之后走缓存）。

    先问供应商 API 拿模型的上下文窗口（vLLM 会返回 max_model_len），
    拿到就用窗口的 80%；查不到（OpenAI 不返回 / 网络问题）就用兜底值。

    返回：
      int，token 预算
    """
    global _budget_cache
    if _budget_cache is not None:
        return _budget_cache

    ctx_window = fetch_model_context_window(
        base_url=settings.CODING_LLM_BASE_URL,
        api_key=settings.CODING_LLM_API_KEY,
        auth_type=settings.CODING_LLM_AUTH_TYPE,
        model=settings.CODING_LLM_MODEL,
    )
    if ctx_window:
        _budget_cache = int(ctx_window * BUDGET_RATIO)
    else:
        _budget_cache = DEFAULT_TOKEN_BUDGET
    return _budget_cache


def build_context_manager(max_messages: int | None = None, token_budget: int | None = None) -> ContextManager:
    """构造配置齐全的 ContextManager（token 预算 + 历史摘要都开）。

    参数：
      max_messages — 消息条数上限。默认 None = 关闭条数维度，只看 token 预算。
                     （决策背景：Coding 场景一轮工具调用 6-8 条消息，
                     20 条约等于 3 轮就压缩，远早于 token 预算，信息白丢，
                     见 ADR-0003）
      token_budget — token 预算。不传就调 resolve_token_budget() 现算。

    返回：
      ContextManager 实例

    用法例子：
        context_mgr = build_context_manager()
    """
    if token_budget is None:
        token_budget = resolve_token_budget()
    return ContextManager(
        max_messages=max_messages,
        max_tokens=token_budget,
        summarizer_fn=make_summarizer(),
    )
