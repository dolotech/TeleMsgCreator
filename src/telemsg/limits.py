"""Telegram Bot API 硬性约束。

数值来源：https://core.telegram.org/bots/api （sendMessage / sendPhoto /
InlineKeyboardButton / sendMediaGroup 等章节），于 2026-09 复核。
"""

from __future__ import annotations

# --- 文本 -------------------------------------------------------------
MESSAGE_TEXT_MAX = 4096
CAPTION_MAX = 1024
POLL_QUESTION_MAX = 300
POLL_OPTION_MAX = 100

# --- 按钮 -------------------------------------------------------------
CALLBACK_DATA_MAX_BYTES = 64
BUTTON_TEXT_RECOMMENDED_MAX = 64  # 官方未硬性限制，超过会被客户端截断
KEYBOARD_ROWS_RECOMMENDED_MAX = 100
BUTTONS_PER_ROW_RECOMMENDED_MAX = 8

# --- 媒体 -------------------------------------------------------------
PHOTO_MAX_BYTES = 10 * 1024 * 1024
PHOTO_DIMENSION_SUM_MAX = 10_000  # width + height
PHOTO_ASPECT_RATIO_MAX = 20  # 长边 / 短边
MEDIA_GROUP_MIN = 2
MEDIA_GROUP_MAX = 10
MEDIA_GROUP_CAPTION_MAX = 1024  # 仅第一个元素可带 caption
UPLOAD_MAX_BYTES = 50 * 1024 * 1024  # 本工具默认上传上限（可配置）
STANDARD_UPLOAD_LIMIT = 50 * 1024 * 1024  # Bot API 标准上限
LOCAL_SERVER_UPLOAD_LIMIT = 2000 * 1024 * 1024  # 自建 Bot API Server

# --- 速率 -------------------------------------------------------------
GLOBAL_MESSAGES_PER_SECOND = 30
GROUP_MESSAGES_PER_MINUTE = 20
CHAT_MIN_INTERVAL_SECONDS = 1.0


def photo_dimension_ok(width: int, height: int) -> tuple[bool, str | None]:
    """校验照片宽高是否符合 Bot API 要求。"""
    if width <= 0 or height <= 0:
        return False, "照片宽高必须为正整数"
    if width + height > PHOTO_DIMENSION_SUM_MAX:
        return False, f"宽高之和 {width + height} 超过 {PHOTO_DIMENSION_SUM_MAX}"
    ratio = max(width, height) / min(width, height)
    if ratio > PHOTO_ASPECT_RATIO_MAX:
        return False, f"宽高比 {ratio:.2f} 超过 {PHOTO_ASPECT_RATIO_MAX}"
    return True, None
