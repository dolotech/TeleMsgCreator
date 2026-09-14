"""TeleMsgCreator — 生产级 Telegram 图文 + 按钮消息工具。

Public API::

    from telemsg import Draft, TelegramClient, PostService

**这里的导出是惰性的**（PEP 562）。原因：``telemsg.release``（打包器）与
``telemsg.netutil`` 只用标准库，却因为 ``import telemsg`` 会执行本文件而被迫
拉起 httpx / pydantic / fastapi 整条依赖链——在没装依赖的机器上跑打包脚本
会直接 ``ModuleNotFoundError: No module named 'httpx'``。

改成惰性后：

* ``import telemsg.release`` 不需要任何第三方依赖；
* ``from telemsg import Draft`` 的写法完全不受影响，首次访问时才真正导入。
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

__version__ = "1.0.0"

#: 名称 → 所在子模块
_EXPORTS: dict[str, str] = {
    "ConfigError": "errors",
    "MediaSourceError": "errors",
    "TelegramAPIError": "errors",
    "TelemsgError": "errors",
    "TransportError": "errors",
    "ValidationError": "errors",
    "Draft": "models",
    "InlineButton": "models",
    "Keyboard": "models",
    "Media": "models",
    "SendResult": "models",
    "TelegramClient": "client",
    "PostService": "service",
}

# 显式列出（而不是展开 _EXPORTS）：静态检查工具需要看到字面量才能确认
# 下面 TYPE_CHECKING 块里的导入不是「未使用」
__all__ = [
    "ConfigError",
    "Draft",
    "InlineButton",
    "Keyboard",
    "Media",
    "MediaSourceError",
    "PostService",
    "SendResult",
    "TelegramAPIError",
    "TelegramClient",
    "TelemsgError",
    "TransportError",
    "ValidationError",
    "__version__",
]

if TYPE_CHECKING:  # 只给类型检查器看，运行时不执行
    from .client import TelegramClient
    from .errors import (
        ConfigError,
        MediaSourceError,
        TelegramAPIError,
        TelemsgError,
        TransportError,
        ValidationError,
    )
    from .models import Draft, InlineButton, Keyboard, Media, SendResult
    from .service import PostService


def __getattr__(name: str) -> Any:
    """按需导入子模块里的名字（PEP 562）。"""
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = importlib.import_module(f".{module_name}", __name__)
    value = getattr(module, name)
    globals()[name] = value  # 缓存下来，后续访问不再走 __getattr__
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_EXPORTS))
