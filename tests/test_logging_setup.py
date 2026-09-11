from __future__ import annotations

import logging

from telemsg.logging_setup import RedactTokens, redact, setup_logging

#: 仅用于测试的假 token，切勿填入真实凭证
TOKEN = "1234567890:AAHfakeTokenValueForTestsOnly_12345678"


def test_redact_removes_bot_url_token() -> None:
    message = f"HTTP Request: POST https://api.telegram.org/bot{TOKEN}/getMe \"HTTP/1.1 200 OK\""
    cleaned = redact(message)
    assert TOKEN not in cleaned
    assert "bot<TOKEN>/getMe" in cleaned


def test_redact_removes_bare_token() -> None:
    assert TOKEN not in redact(f"token={TOKEN}")
    assert TOKEN not in redact(f'{{"authorization": "{TOKEN}"}}')


def test_redact_keeps_normal_text() -> None:
    text = "发送成功 message_id=42 chat=@my_channel"
    assert redact(text) == text
    assert redact("time 12:30:45") == "time 12:30:45"


def test_filter_rewrites_record_and_args() -> None:
    redactor = RedactTokens()
    record = logging.LogRecord(
        "httpx",
        logging.INFO,
        __file__,
        1,
        "POST https://api.telegram.org/bot%s/getMe",
        (TOKEN,),
        None,
    )
    assert redactor.filter(record) is True
    rendered = record.getMessage()
    assert TOKEN not in rendered
    assert "bot<TOKEN>" in rendered


def test_filter_rewrites_dict_args() -> None:
    redactor = RedactTokens()
    # logging 会把单个 mapping 参数包成 tuple，这里按真实形态构造
    record = logging.LogRecord(
        "httpx", logging.INFO, __file__, 1, "url=%(url)s", ({"url": f"https://x/bot{TOKEN}/m"},), None
    )
    redactor.filter(record)
    assert TOKEN not in record.getMessage()


def test_setup_logging_quiets_httpx_by_default() -> None:
    setup_logging("INFO", verbose=False)
    assert logging.getLogger("httpx").level == logging.WARNING
    setup_logging("INFO", verbose=True)
    assert logging.getLogger("httpx").level == logging.INFO
    setup_logging("INFO", verbose=False)


def test_handler_gets_redaction_filter() -> None:
    handler = logging.StreamHandler()
    setup_logging("INFO", verbose=True, handler=handler)
    assert any(isinstance(f, RedactTokens) for f in handler.filters)
