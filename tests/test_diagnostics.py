from __future__ import annotations

import pytest

from telemsg.diagnostics import Diagnosis, explain
from telemsg.errors import ConfigError, MediaSourceError, TelegramAPIError, TransportError


def api_error(code: int, description: str) -> TelegramAPIError:
    return TelegramAPIError("sendMessage", error_code=code, description=description)


@pytest.mark.parametrize(
    ("error", "expected_keyword"),
    [
        (api_error(400, "Bad Request: chat not found"), "找不到目标会话"),
        (api_error(400, "Bad Request: not enough rights to send text messages"), "没有发帖权限"),
        (api_error(403, "Forbidden: bot is not a member of the channel chat"), "不在这个会话里"),
        (api_error(403, "Forbidden: bot was kicked from the supergroup chat"), "不在这个会话里"),
        (api_error(400, "Bad Request: message caption is too long"), "1024"),
        (api_error(400, "Bad Request: message is too long"), "4096"),
        (api_error(400, "Bad Request: BUTTON_DATA_INVALID"), "callback_data"),
        (api_error(400, "Bad Request: can't parse entities"), "富文本"),
        (api_error(400, "Bad Request: wrong file identifier/HTTP URL specified"), "媒体地址"),
        (api_error(400, "Bad Request: MEDIA_GROUP_INVALID"), "相册"),
        (api_error(400, "Bad Request: PHOTO_INVALID_DIMENSIONS"), "尺寸"),
        (api_error(400, "Bad Request: file is too big"), "上传上限"),
        (api_error(429, "Too Many Requests: retry after 5"), "限流"),
        (api_error(401, "Unauthorized"), "Token"),
        (api_error(500, "Internal Server Error"), "临时故障"),
    ],
)
def test_explain_maps_common_telegram_errors(error, expected_keyword: str) -> None:
    diagnosis = explain(error)
    assert isinstance(diagnosis, Diagnosis)
    assert expected_keyword in diagnosis.message, diagnosis
    assert diagnosis.raw  # 原文保留，便于排查


def test_explain_unknown_api_error_keeps_description() -> None:
    diagnosis = explain(api_error(400, "Bad Request: something totally new"))
    assert "something totally new" in diagnosis.message


def test_explain_config_error_points_to_settings() -> None:
    diagnosis = explain(ConfigError("缺少 Bot Token"))
    assert diagnosis.action == "open_settings"
    assert diagnosis.hint


def test_explain_transport_error_mentions_proxy() -> None:
    diagnosis = explain(TransportError("sendMessage 网络请求失败: timeout"))
    assert "网络" in diagnosis.message
    assert "代理" in (diagnosis.hint or "")


def test_explain_media_error_keeps_hint() -> None:
    diagnosis = explain(MediaSourceError("文件不存在", hint="确认路径"))
    assert "文件不存在" in diagnosis.message


def test_diagnosis_text_combines_message_and_hint() -> None:
    assert Diagnosis("出错了", "这么办").text == "出错了 👉 这么办"
    assert Diagnosis("出错了").text == "出错了"


def test_explain_never_leaks_token() -> None:
    """诊断信息会直接展示给用户，绝不能把 token 带出来。"""
    token = "1234567890:AAHfakeTokenValueForTestsOnly_12345678"
    diagnosis = explain(TransportError(f"https://api.telegram.org/bot{token}/sendMessage 失败"))
    assert token not in diagnosis.text
    assert token not in (diagnosis.raw or "")
