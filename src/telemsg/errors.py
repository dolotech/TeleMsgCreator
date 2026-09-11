"""异常层级。所有对外抛出的异常都继承 :class:`TelemsgError`。"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any


class TelemsgError(Exception):
    """本项目的基类异常。"""


class ConfigError(TelemsgError):
    """配置缺失或非法（例如没有提供 BOT TOKEN）。"""


class MediaSourceError(TelemsgError):
    """媒体来源不可用：路径不存在、文件过大、格式不支持等。"""

    def __init__(self, message: str, *, source: str | None = None, hint: str | None = None) -> None:
        super().__init__(message)
        self.source = source
        self.hint = hint


class ValidationError(TelemsgError):
    """草稿未通过 Telegram 约束校验。"""

    def __init__(self, issues: Iterable[Issue]) -> None:  # noqa: F821 - forward ref
        self.issues = list(issues)
        detail = "; ".join(f"[{i.field}] {i.message}" for i in self.issues) or "unknown validation error"
        super().__init__(detail)

    def errors(self) -> list[Issue]:  # noqa: F821
        from .validation import Severity

        return [i for i in self.issues if i.severity is Severity.ERROR]


class TransportError(TelemsgError):
    """网络层错误（连接失败、超时、5xx 重试耗尽等）。"""

    def __init__(self, message: str, *, attempts: int = 1, last_exception: BaseException | None = None):
        super().__init__(message)
        self.attempts = attempts
        self.last_exception = last_exception


class TelegramAPIError(TelemsgError):
    """Bot API 返回 ``ok: false``。"""

    def __init__(
        self,
        method: str,
        *,
        error_code: int | None = None,
        description: str | None = None,
        parameters: dict[str, Any] | None = None,
        status_code: int | None = None,
    ) -> None:
        self.method = method
        self.error_code = error_code
        self.description = description or "unknown Telegram API error"
        self.parameters = parameters or {}
        self.status_code = status_code
        super().__init__(f"{method}: [{error_code}] {self.description}")

    @property
    def retry_after(self) -> float | None:
        value = self.parameters.get("retry_after")
        return float(value) if value is not None else None

    @property
    def migrate_to_chat_id(self) -> int | None:
        value = self.parameters.get("migrate_to_chat_id")
        return int(value) if value is not None else None

    @property
    def is_rate_limited(self) -> bool:
        return self.error_code == 429

    @property
    def is_retryable(self) -> bool:
        if self.error_code is None:
            return True
        return self.error_code == 429 or self.error_code >= 500

    @property
    def is_fatal_for_chat(self) -> bool:
        """目标会话永久不可用（被踢、被封、chat_id 不存在等）。"""
        return self.error_code in {400, 403} and any(
            token in self.description.lower()
            for token in (
                "chat not found",
                "bot was kicked",
                "bot is not a member",
                "user is deactivated",
                "bot can't initiate conversation",
                "not enough rights",
                "chat_write_forbidden",
            )
        )


def summarize_errors(errors: Sequence[BaseException]) -> str:
    if not errors:
        return ""
    head = "; ".join(f"{type(e).__name__}: {e}" for e in errors[:3])
    return head + (f" (+{len(errors) - 3} more)" if len(errors) > 3 else "")
