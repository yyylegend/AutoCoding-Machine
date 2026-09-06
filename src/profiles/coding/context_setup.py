"""兼容旧入口；实现已移到共享 runtime 层。"""

from src.runtime.context import build_context_manager, calculate_token_budget, init_coding_tokenizer, make_summarizer, resolve_context_length, resolve_token_budget
