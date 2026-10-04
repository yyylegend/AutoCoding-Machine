from importlib.metadata import version
from urllib.parse import urlsplit

from auto_coding_machine.common.llm_client import chat_stream
from auto_coding_machine.common.model_adapter import ModelAdapter


class TuiModelAdapter(ModelAdapter):
    def __init__(self, tools, emit, *, session_id, model=None, completion_gate=None):
        super().__init__(tools, on_token=self._receive_token, model=model)
        self.emit = emit
        self.completion_gate = completion_gate
        self.session_id = session_id
        self.reset_metrics()
        self._publish = False

    def reset_metrics(self):
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.usage_complete = True
        self.last_prompt_tokens = None
        self.last_ttft_ms = None

    def _receive_token(self, text):
        if self._publish:
            self.emit("chunk", text)

    def call(self, messages):
        self._publish = self.completion_gate is None or self.completion_gate.should_publish_stream()
        self.emit("start", self._publish)
        previous_complete = self.usage_complete
        self.usage_complete = False
        response = super().call(messages)
        self.usage_complete = previous_complete
        prompt = response.usage.get("prompt_tokens")
        self.last_prompt_tokens = prompt if type(prompt) is int and prompt >= 0 else None
        self.emit("end", None)
        for key, attr in (("prompt_tokens", "prompt_tokens"),
                          ("completion_tokens", "completion_tokens")):
            value = response.usage.get(key)
            if type(value) is int and value >= 0:
                setattr(self, attr, getattr(self, attr) + value)
            else:
                self.usage_complete = False
        return response

    def _chat_stream(self, *args, **kwargs):
        headers = {"User-Agent": f"AutoCoding-Machine/{version('auto-coding-machine')}"}
        if urlsplit(self.base_url).hostname == "opencode.ai":
            headers["x-opencode-session"] = self.session_id
        result = chat_stream(*args, **kwargs, extra_headers=headers)
        self.last_ttft_ms = result.get("ttft_ms")
        return result
