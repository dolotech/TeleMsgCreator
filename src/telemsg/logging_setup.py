"""日志配置。

**硬性要求：Bot Token 绝不能出现在任何日志里。**

httpx 在 INFO 级别会把完整请求 URL 打出来，而 Bot API 的 URL 长这样::

    https://api.telegram.org/bot123456789:AAExampleTokenxxxxxxxxxxxxxxxxx/getMe

token 就在路径里。所以这里做两层防护：

1. 默认把 httpx 的日志级别压到 WARNING（正常运行时根本不打印请求 URL）；
2. 给所有 handler 挂上 :class:`RedactTokens` 过滤器，兜底把任何漏出来的
   token 替换成 ``bot<TOKEN>``（调试模式下开了 httpx INFO 也不会泄漏）。
"""

from __future__ import annotations

import logging
import re

#: ``bot123456789:AAF…`` 形式的 token
TOKEN_PATTERN = re.compile(r"(bot)(\d{5,}:[A-Za-z0-9_\-]{15,})")
PLACEHOLDER = r"\1<TOKEN>"

#: 裸 token（没有 bot 前缀时也不能放过）
BARE_TOKEN_PATTERN = re.compile(r"(?<![A-Za-z0-9_])(\d{5,}:[A-Za-z0-9_\-]{30,})(?![A-Za-z0-9_\-])")
BARE_PLACEHOLDER = "<TOKEN>"


def redact(text: str) -> str:
    """把文本里的 token 换成占位符。"""
    text = TOKEN_PATTERN.sub(PLACEHOLDER, text)
    return BARE_TOKEN_PATTERN.sub(BARE_PLACEHOLDER, text)


class RedactTokens(logging.Filter):
    """在日志真正落地前改写消息。"""

    @staticmethod
    def _clean(value: object) -> object:
        if isinstance(value, str):
            return redact(value)
        if isinstance(value, dict):
            return {key: RedactTokens._clean(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return type(value)(RedactTokens._clean(item) for item in value)
        return value

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003 - logging 的接口
        if isinstance(record.msg, str) and ":" in record.msg:
            record.msg = redact(record.msg)
        if record.args:
            # logger.info("... %s", token) 与 logger.info("url=%(url)s", {...}) 都要覆盖
            record.args = self._clean(record.args)  # type: ignore[assignment]
        if record.exc_text:
            record.exc_text = redact(record.exc_text)
        return True


_NOISY_LOGGERS = ("httpx", "httpcore", "uvicorn.access", "uvicorn.error", "python_multipart")


def install_token_redaction() -> RedactTokens:
    """给根 logger 上已有的 handler 以及各噪声 logger 挂过滤器。"""
    redactor = RedactTokens()
    for handler in logging.getLogger().handlers:
        if not any(isinstance(f, RedactTokens) for f in handler.filters):
            handler.addFilter(redactor)
    for name in _NOISY_LOGGERS:
        logger = logging.getLogger(name)
        if not any(isinstance(f, RedactTokens) for f in logger.filters):
            logger.addFilter(redactor)
    return redactor


def setup_logging(level: str = "INFO", *, verbose: bool = False, handler: logging.Handler | None = None) -> None:
    """统一入口：设置级别 + 压掉 httpx 噪声 + 安装脱敏过滤器。"""
    resolved = logging.DEBUG if verbose else getattr(logging, str(level).upper(), logging.INFO)
    root = logging.getLogger()
    root.setLevel(resolved)
    if handler is not None:
        handler.setLevel(resolved)
        if not any(isinstance(f, RedactTokens) for f in handler.filters):
            handler.addFilter(RedactTokens())
    # httpx 的请求日志自带 token，非调试模式一律不打印
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.INFO if verbose else logging.WARNING)
    logging.getLogger("python_multipart").setLevel(logging.WARNING)
    install_token_redaction()
