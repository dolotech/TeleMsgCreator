from __future__ import annotations

from telemsg import limits
from telemsg.models import Draft, Media
from telemsg.validation import Severity, validate_draft


def test_text_over_limit_is_error() -> None:
    draft = Draft(chat_id="@c", text="a" * (limits.MESSAGE_TEXT_MAX + 1), parse_mode="HTML")
    report = validate_draft(draft, check_files=False)
    assert not report.ok
    assert any(i.field == "text" for i in report.errors)


def test_caption_over_limit_is_error() -> None:
    draft = Draft(
        chat_id="@c",
        media=Media(kind="photo", source="https://a/1.jpg", caption="b" * (limits.CAPTION_MAX + 1)),
    )
    report = validate_draft(draft, check_files=False)
    assert not report.ok
    assert any("caption" in i.field for i in report.errors)


def test_media_group_with_keyboard_is_error() -> None:
    draft = Draft(
        chat_id="@c",
        media_group=[Media(kind="photo", source=f"https://a/{i}.jpg") for i in range(3)],
        keyboard="A|https://a.com",
    )
    report = validate_draft(draft, check_files=False)
    assert not report.ok
    assert any("媒体组" in i.message for i in report.errors)


def test_media_group_size_limits() -> None:
    too_many = Draft(
        chat_id="@c",
        media_group=[Media(kind="photo", source=f"https://a/{i}.jpg") for i in range(11)],
    )
    assert not validate_draft(too_many, check_files=False).ok
    mixed = Draft(
        chat_id="@c",
        media_group=[
            Media(kind="photo", source="https://a/1.jpg"),
            Media(kind="document", source="https://a/2.pdf"),
        ],
    )
    assert not validate_draft(mixed, check_files=False).ok


def test_missing_local_file_is_error() -> None:
    draft = Draft(chat_id="@c", media=Media(kind="photo", source="/nonexistent/nope.jpg"))
    report = validate_draft(draft)
    assert not report.ok
    assert any(i.severity is Severity.ERROR for i in report.issues)


def test_warnings_do_not_block() -> None:
    draft = Draft(chat_id="@c", text="hi", keyboard="A|https://a.com|B|https://b.com|C|https://c.com|D|https://d.com")
    report = validate_draft(draft, check_files=False)
    assert report.ok
    assert report.warnings


def test_photo_dimension_helper() -> None:
    assert limits.photo_dimension_ok(1200, 800)[0]
    assert not limits.photo_dimension_ok(20000, 800)[0]
    assert not limits.photo_dimension_ok(1200, 10)[0]


def test_report_serialization() -> None:
    draft = Draft(chat_id="@c", text="a" * 5000)
    payload = validate_draft(draft, check_files=False).as_dict()
    assert payload["ok"] is False
    assert payload["errors"][0]["severity"] == "error"
