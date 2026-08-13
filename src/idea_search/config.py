"""Configuration loading for idea-search.

Loads a ``.env`` file (searched in CWD, then the user's home directory) and
exposes the result through a typed :class:`Settings` dataclass.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


class ConfigError(Exception):
    """Raised when required configuration is missing or invalid."""


def _load_dotenv() -> None:
    """Load .env from CWD, then HOME. Never overrides already-exported vars."""
    for candidate in (Path.cwd() / ".env", Path.home() / ".env"):
        if candidate.is_file():
            load_dotenv(candidate)
    load_dotenv()  # no-op fallback so already-exported vars are picked up


@dataclass(frozen=True)
class Settings:
    deepseek_api_key: str = ""
    openai_api_key: str = ""
    llm_base_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-chat"
    github_token: str = ""

    @classmethod
    def from_env(cls) -> "Settings":
        _load_dotenv()
        return cls(
            deepseek_api_key=os.getenv("DEEPSEEK_API_KEY", ""),
            openai_api_key=os.getenv("OPENAI_API_KEY", ""),
            llm_base_url=os.getenv("LLM_BASE_URL", "https://api.deepseek.com"),
            llm_model=os.getenv("LLM_MODEL", "deepseek-chat"),
            github_token=os.getenv("GITHUB_TOKEN", ""),
        )

    @property
    def llm_api_key(self) -> str:
        """Primary/fallback LLM API key, or raise ConfigError with setup help."""
        if self.deepseek_api_key:
            return self.deepseek_api_key
        if self.openai_api_key:
            return self.openai_api_key
        raise ConfigError(
            "未检测到 LLM API Key。请在 .env 文件中配置 DEEPSEEK_API_KEY（推荐）"
            "或 OPENAI_API_KEY（备用），例如：\n"
            "  DEEPSEEK_API_KEY=sk-xxxx\n"
            "创建方式：在项目根目录（或用户主目录）新建 .env 文件并填入上述内容，"
            "然后重新运行。"
        )

    @property
    def has_git_token(self) -> bool:
        return bool(self.github_token)