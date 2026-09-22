"""一次模型请求的临时消息视图。"""


class RequestView:
    """组合历史召回和临时状态，不修改原始 messages。"""

    def __init__(self, context_selector=None, status_bar=None):
        self.context_selector = context_selector
        self.status_bar = status_bar

    def select(self, messages):
        selected = messages
        if self.context_selector is not None:
            selected = self.context_selector.select(messages)
        return selected

    def build(self, messages, status_state=None):
        selected = self.select(messages)
        if self.status_bar is None:
            return selected
        return list(selected) + [self.status_bar(status_state or {})]
