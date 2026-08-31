"""Coding Agent 的集中配置。"""

import os
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")


class Settings:
    """只保留 Engine、Runtime 与 CLI 实际使用的配置。"""

    LLM_BASE_URL: str = os.getenv("LLM_BASE_URL", "")
    LLM_MODEL: str = os.getenv("LLM_MODEL", "")
    LLM_API_KEY: str = os.getenv("LLM_API_KEY", "")
    LLM_AUTH_TYPE: str = os.getenv("LLM_AUTH_TYPE", "bearer")
    LLM_TIMEOUT_SEC: int = int(os.getenv("LLM_TIMEOUT_SEC", "120"))
    LLM_MAX_TOKENS: int = int(os.getenv("LLM_MAX_TOKENS", "4096"))

    CODING_LLM_BASE_URL: str = os.getenv("CODING_LLM_BASE_URL", LLM_BASE_URL)
    CODING_LLM_MODEL: str = os.getenv("CODING_LLM_MODEL", LLM_MODEL)
    CODING_LLM_API_KEY: str = os.getenv("CODING_LLM_API_KEY", LLM_API_KEY)
    CODING_LLM_AUTH_TYPE: str = os.getenv("CODING_LLM_AUTH_TYPE", LLM_AUTH_TYPE)
    CODING_LLM_TIMEOUT_SEC: int = int(os.getenv("CODING_LLM_TIMEOUT_SEC", str(LLM_TIMEOUT_SEC)))
    CODING_LLM_MAX_TOKENS: int = int(os.getenv("CODING_LLM_MAX_TOKENS", str(LLM_MAX_TOKENS)))

    CODING_MAX_TURNS: int = int(os.getenv("CODING_MAX_TURNS", "30"))
    CONTEXT_SUMMARY_ENABLED: bool = os.getenv("CONTEXT_SUMMARY_ENABLED", "false").lower() == "true"

    MEMORY_ENABLED: bool = os.getenv("MEMORY_ENABLED", "true").lower() == "true"
    MEMORY_CHAR_LIMIT: int = int(os.getenv("MEMORY_CHAR_LIMIT", "2200"))
    USER_CHAR_LIMIT: int = int(os.getenv("USER_CHAR_LIMIT", "1375"))


settings = Settings()
