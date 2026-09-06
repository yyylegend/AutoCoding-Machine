"""诊断记录独立于聊天历史：便于评测，但不会被自动召回。"""

import hashlib
import json
import time

from src.common.logger import get_logger
from src.config.settings import settings


class RunTrace:
    def __init__(self, directory, profile, model_fn, skills, context_budget):
        self.directory = directory
        self.profile = profile
        self.model_fn = model_fn
        self.path = None
        # 调用方可以注入自己的上下文管理器；未提供数值预算时记为未知。
        self.context_budget = context_budget if isinstance(context_budget, int) else None
        # 记录实际加载版本，而不把 API key 等环境配置写进日志。
        self.skills = [
            {"name": skill["name"], "sha256": hashlib.sha256(skill["path"].read_bytes()).hexdigest()}
            for skill in skills
        ]

    def set_session(self, store):
        self.path = self.directory / (store.path.stem + ".jsonl")
        self.write("configuration", profile=self.profile.snapshot(), skills=self.skills,
                   model=self.profile.model or settings.CODING_LLM_MODEL,
                   context_budget=self.context_budget)

    def write(self, event, **data):
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({"event": event, "time": time.time(), **data}, ensure_ascii=False) + "\n")
        except OSError:
            get_logger(__name__).warning("诊断记录写入失败，主任务继续")

    def call(self, messages):
        # 保存模型实际看到的视图，包括临时历史召回和压缩结果。
        self.write("model_request", messages=messages)
        start = time.perf_counter()
        try:
            response = self.model_fn(messages)
        except Exception as exc:
            self.write("model_error", error=type(exc).__name__, duration_ms=(time.perf_counter() - start) * 1000)
            raise
        self.write("model_response", response=response.to_message(), usage=response.usage,
                   duration_ms=(time.perf_counter() - start) * 1000)
        return response

    def after_tool(self, tool_name, error=False, duration_ms=None, **_):
        self.write("tool_result", tool=tool_name, error=error, duration_ms=duration_ms)

    def done(self, **_):
        self.write("done")

    def failed(self, error=None, **_):
        self.write("failed", error=error)
