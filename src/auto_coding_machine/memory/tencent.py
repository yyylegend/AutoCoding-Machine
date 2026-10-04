"""Tencent MemoryCore Gateway v3 的只读 L1 召回 Adapter。"""

import os
import re
from urllib.parse import urlsplit

import requests

from auto_coding_machine.memory.external import (
    MemoryCapture,
    MemoryHit,
    MemoryQuery,
    MemoryScope,
    MemoryWriteError,
    MemoryWriteUncertainError,
)


_SAFE_ID = re.compile(r"[A-Za-z0-9._:-]{1,128}\Z")


class TencentMemoryCoreProvider:
    """把 MemoryProvider.recall 翻译为 Gateway /v3/atomic/search。"""

    def __init__(self, endpoint: str, *, service_id: str):
        parsed = urlsplit(endpoint)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError("MemoryCore endpoint 必须是 HTTP(S) URL")
        if parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
            raise ValueError("远程 MemoryCore endpoint 必须使用 HTTPS")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("MemoryCore endpoint 不能包含凭据、查询参数或片段")
        if not service_id or not service_id.strip():
            raise ValueError("MemoryCore service_id 不能为空")
        api_key = os.environ.get("TDAI_MEMORY_API_KEY")
        if not api_key:
            raise ValueError("请在本地环境设置 TDAI_MEMORY_API_KEY")
        self.endpoint = endpoint.rstrip("/")
        self.service_id = service_id
        self._api_key = api_key

    def recall(self, query: MemoryQuery) -> list[MemoryHit]:
        scope = query.scope
        if not scope.team_id or not scope.agent_id:
            raise ValueError("MemoryCore v3 召回需要 team_id、agent_id 和 user_id")

        # 不传 session_id：L1 需要在同一身份范围内跨会话召回。
        response = requests.post(
            self.endpoint + "/v3/atomic/search",
            json={
                "team_id": scope.team_id,
                "agent_id": scope.agent_id,
                "user_id": scope.user_id,
                "query": query.text,
                "limit": query.max_items,
            },
            headers={
                "Authorization": "Bearer " + self._api_key,
                "x-tdai-service-id": self.service_id,
            },
            timeout=query.timeout_seconds,
            allow_redirects=False,
        )
        response.raise_for_status()
        if response.status_code >= 300:
            raise ValueError("MemoryCore 召回收到重定向")
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("code") != 0:
            raise ValueError("MemoryCore 召回失败")
        data = payload.get("data")
        items = data.get("items") if isinstance(data, dict) else None
        if not isinstance(items, list):
            raise ValueError("MemoryCore 召回结果格式错误")

        hits = []
        for item in items[:query.max_items]:
            if not isinstance(item, dict):
                raise ValueError("MemoryCore 记忆条目格式错误")
            if not all(isinstance(item.get(name), str) and item[name].strip()
                       for name in ("team_id", "agent_id", "user_id")):
                raise ValueError("MemoryCore 记忆缺少身份范围")
            item_scope = MemoryScope(
                user_id=item.get("user_id"),
                team_id=item.get("team_id"),
                agent_id=item.get("agent_id"),
            )
            if item_scope != scope:
                raise ValueError("MemoryCore 返回了其他身份范围的记忆")
            item_id = item.get("id")
            content = item.get("content")
            if not isinstance(item_id, str) or not _SAFE_ID.fullmatch(item_id):
                raise ValueError("MemoryCore 记忆缺少有效来源 ID")
            if not isinstance(content, str) or not content.strip():
                raise ValueError("MemoryCore 记忆内容格式错误")
            hits.append(MemoryHit(content, "tencent:atomic:" + item_id, item_scope))
        return hits

    def capture(self, turn: MemoryCapture) -> None:
        """写入显式选择的一轮对话，Gateway 后台负责 L1 提炼。"""
        scope = turn.scope
        if not scope.team_id or not scope.agent_id:
            raise MemoryWriteError("MemoryCore v3 写入需要 team_id、agent_id 和 user_id")

        try:
            response = requests.post(
                self.endpoint + "/v3/conversation/add",
                json={
                    "team_id": scope.team_id,
                    "agent_id": scope.agent_id,
                    "user_id": scope.user_id,
                    "session_id": turn.session_id,
                    "messages": [
                        {"role": "user", "content": turn.user_message},
                        {"role": "assistant", "content": turn.assistant_message},
                    ],
                },
                headers={
                    "Authorization": "Bearer " + self._api_key,
                    "x-tdai-service-id": self.service_id,
                },
                timeout=turn.timeout_seconds,
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            raise MemoryWriteUncertainError(type(exc).__name__) from exc

        if 300 <= response.status_code < 400:
            raise MemoryWriteError("MemoryCore 写入收到重定向")
        if response.status_code >= 500:
            raise MemoryWriteUncertainError("GatewayServerError")
        if response.status_code >= 400:
            raise MemoryWriteError("MemoryCore 写入被拒绝")
        try:
            payload = response.json()
        except ValueError as exc:
            raise MemoryWriteUncertainError("InvalidGatewayResponse") from exc
        if not isinstance(payload, dict) or payload.get("code") != 0:
            raise MemoryWriteError("MemoryCore 写入失败")
        data = payload.get("data")
        accepted_ids = data.get("accepted_ids") if isinstance(data, dict) else None
        if not isinstance(accepted_ids, list) or len(accepted_ids) != 2:
            raise MemoryWriteUncertainError("IncompleteGatewayAcknowledgement")
