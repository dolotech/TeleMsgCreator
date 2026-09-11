"""运行配置。全部可通过环境变量或 ``.env`` 覆盖（前缀 ``TELEMSG_``）。"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .logging_setup import mask_token

DEFAULT_API_BASE = "https://api.telegram.org"
DEFAULT_DB_PATH = Path("data/telemsg.sqlite3")
DEFAULT_UPLOAD_DIR = Path("data/uploads")
ENV_FILE_NAME = ".env"

#: 允许通过 Web 界面写回的配置项（其余项保持只在环境变量里控制）
WRITABLE_ENV_KEYS = (
    "TELEMSG_BOT_TOKEN",
    "TELEMSG_DEFAULT_CHAT_ID",
    "TELEMSG_API_BASE",
)


def env_file_path(base: Path | None = None) -> Path:
    """``.env`` 的位置；默认取当前工作目录（与 pydantic-settings 保持一致）。"""
    return (base or Path.cwd()) / ENV_FILE_NAME


def update_env_file(updates: Mapping[str, str | None], *, path: Path | None = None) -> Path:
    """就地更新 ``.env``：保留注释与无关行，值为 ``None`` 时删除该键。

    写入采用「临时文件 + 原子替换」，并强制 ``0600`` 权限——这里存的是
    等同于账号密码的东西。
    """
    target = path or env_file_path()
    lines: list[str] = []
    if target.exists():
        lines = target.read_text(encoding="utf-8").splitlines()

    pending = {key: value for key, value in updates.items()}
    result: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            result.append(line)
            continue
        key = stripped.split("=", 1)[0].strip()
        if key not in pending:
            result.append(line)
            continue
        value = pending.pop(key)
        if value is None:
            continue
        result.append(f"{key}={value}")

    for key, value in pending.items():
        if value is None:
            continue
        if result and result[-1].strip():
            result.append("")
        result.append(f"{key}={value}")

    target.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join(result).rstrip("\n") + "\n"
    fd, tmp_name = tempfile.mkstemp(dir=str(target.parent), prefix=".env.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, target)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    return target


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
    env_file: Path = Field(default=Path(ENV_FILE_NAME), description="界面保存配置时写入的 .env 路径")

    timeout_seconds: float = Field(default=30.0, gt=0)
    max_retries: int = Field(default=3, ge=0, le=10)
    proxy: str | None = Field(default=None, description="如 http://127.0.0.1:7890")
    verify_tls: bool = Field(default=True)

    dry_run: bool = Field(default=False, description="为真时只构建请求，不真正调用 API")

    ui_password: str | None = Field(default=None, description="Web UI 基础认证密码（用户名为 telemsg）")
    ui_host: str = Field(default="127.0.0.1")
    ui_port: int = Field(default=8765, ge=1, le=65535)
    allow_env_write: bool = Field(default=True, description="是否允许从 Web 界面把配置写回 .env")

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

    def public_view(self) -> dict[str, object]:
        """给界面用的配置快照——**只含脱敏信息**。"""
        from_env = bool(
            os.environ.get("TELEMSG_BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN")
        )
        return {
            "has_token": bool(self.resolved_token),
            "token_hint": mask_token(self.resolved_token),
            # 环境变量优先级高于 .env，界面要如实告诉用户，否则会以为改 .env 没生效
            "token_from_env": from_env,
            "default_chat_id": self.default_chat_id or "",
            "api_base": self.api_base,
            "allow_env_write": self.allow_env_write,
            "env_file": str(self.env_file),
            "env_file_exists": Path(self.env_file).exists(),
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_dirs()
    return settings


def reset_settings_cache() -> None:
    get_settings.cache_clear()
