"""运行配置。全部可通过环境变量或 ``.env`` 覆盖（前缀 ``TELEMSG_``）。"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_API_BASE = "https://api.telegram.org"
DEFAULT_DB_PATH = Path("data/telemsg.sqlite3")
DEFAULT_UPLOAD_DIR = Path("data/uploads")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="TELEMSG_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    bot_token: str | None = Field(default=None, description="BotFather 颁发的 token，或走 TELEGRAM_BOT_TOKEN")
    api_base: str = Field(default=DEFAULT_API_BASE, description="Bot API 地址；自建服务可改成 http://localhost:8081")

    default_chat_id: str | None = Field(default=None, description="默认发送目标，例如 @my_channel 或 -1001234567890")
    default_parse_mode: str = Field(default="HTML")

    db_path: Path = Field(default=DEFAULT_DB_PATH)
    upload_dir: Path = Field(default=DEFAULT_UPLOAD_DIR)

    timeout_seconds: float = Field(default=30.0, gt=0)
    max_retries: int = Field(default=3, ge=0, le=10)
    proxy: str | None = Field(default=None, description="如 http://127.0.0.1:7890")
    verify_tls: bool = Field(default=True)

    dry_run: bool = Field(default=False, description="为真时只构建请求，不真正调用 API")

    ui_password: str | None = Field(default=None, description="Web UI 基础认证密码（用户名为 telemsg）")
    ui_host: str = Field(default="127.0.0.1")
    ui_port: int = Field(default=8765, ge=1, le=65535)

    log_level: str = Field(default="INFO")

    @field_validator("bot_token", "default_chat_id", "proxy", "ui_password", mode="before")
    @classmethod
    def _empty_to_none(cls, v: object) -> object:
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @field_validator("api_base")
    @classmethod
    def _strip_slash(cls, v: str) -> str:
        return v.rstrip("/")

    @property
    def resolved_token(self) -> str | None:
        """优先使用 TELEMSG_BOT_TOKEN，其次兼容 TELEGRAM_BOT_TOKEN。"""
        return self.bot_token or os.environ.get("TELEGRAM_BOT_TOKEN") or None

    def ensure_dirs(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.upload_dir.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_dirs()
    return settings


def reset_settings_cache() -> None:
    get_settings.cache_clear()
