"""TeleMsgCreator — 生产级 Telegram 图文 + 按钮消息工具.

Public API::

    from telemsg import Draft, TelegramClient, PostService
"""

from __future__ import annotations

from .client import TelegramClient
from .errors import ConfigError, TelegramAPIError, TelemsgError, TransportError, ValidationError
from .models import Draft, InlineButton, Keyboard, Media, SendResult
from .service import PostService

__version__ = "1.0.0"

__all__ = [
    "ConfigError",
    "Draft",
    "InlineButton",
    "Keyboard",
    "Media",
    "PostService",
    "SendResult",
    "TelegramAPIError",
    "TelegramClient",
    "TelemsgError",
    "TransportError",
    "ValidationError",
    "__version__",
]
