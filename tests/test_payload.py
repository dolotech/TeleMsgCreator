from __future__ import annotations

from pathlib import Path

from telemsg.models import Draft, Media
from telemsg.payload import build_requests


def test_text_payload() -> None:
    draft = Draft(chat_id="@c", text="hello", parse_mode="HTML", keyboard="A|https://a.com|B|https://b.com")
    spec = build_requests(draft)[0]
    assert spec.method == "sendMessage"
    assert spec.params["chat_id"] == "@c"
    assert spec.params["parse_mode"] == "HTML"
    assert len(spec.params["reply_markup"]["inline_keyboard"]) == 1
    assert spec.params["reply_markup"]["inline_keyboard"][0][1]["text"] == "B"
    assert not spec.has_uploads


def test_photo_url_payload_has_no_upload() -> None:
    draft = Draft(
        chat_id="@c",
        media=Media(kind="photo", source="https://example.com/a.jpg", caption="cap", parse_mode="HTML"),
        keyboard="A|https://a.com",
    )
    spec = build_requests(draft)[0]
    assert spec.method == "sendPhoto"
    assert spec.params["photo"] == "https://example.com/a.jpg"
    assert spec.params["caption"] == "cap"
    assert spec.params["reply_markup"]["inline_keyboard"][0][0]["url"] == "https://a.com"


def test_local_photo_becomes_attachment(tmp_path: Path, png_bytes: bytes) -> None:
    image = tmp_path / "pic.png"
    image.write_bytes(png_bytes)
    draft = Draft(chat_id="@c", media=Media(kind="photo", source=str(image)))
    spec = build_requests(draft)[0]
    assert spec.params["photo"] == "attach://file0"
    assert spec.uploads[0].name == "file0"
    assert spec.uploads[0].mime_type == "image/png"
    assert spec.multipart_data()["photo"] == "attach://file0"
    assert "reply_markup" not in spec.multipart_data()


def test_file_id_source_is_passed_through() -> None:
    file_id = "AgACAgQAAxkBAAIC4mXkZ1234567890abcdefghijklmnopqrstuv"
    draft = Draft(chat_id="@c", media=Media(kind="photo", source=file_id))
    spec = build_requests(draft)[0]
    assert spec.params["photo"] == file_id
    assert not spec.has_uploads


def test_media_group_payload(tmp_path: Path, png_bytes: bytes) -> None:
    first = tmp_path / "1.png"
    first.write_bytes(png_bytes)
    draft = Draft(
        chat_id="@c",
        media_group=[
            Media(kind="photo", source=str(first), caption="第一张", parse_mode="HTML"),
            Media(kind="photo", source="https://example.com/2.jpg"),
            Media(kind="photo", source="https://example.com/3.jpg"),
        ],
    )
    spec = build_requests(draft)[0]
    assert spec.method == "sendMediaGroup"
    items = spec.params["media"]
    assert len(items) == 3
    assert items[0]["media"] == "attach://file0"
    assert items[0]["caption"] == "第一张"
    assert items[1]["media"] == "https://example.com/2.jpg"
    assert "reply_markup" not in spec.params


def test_dry_run_does_not_read_files() -> None:
    draft = Draft(chat_id="@c", media=Media(kind="photo", source="/definitely/missing.png"))
    spec = build_requests(draft, collect_uploads=False)[0]
    assert spec.params["photo"] == "attach://file0"
    assert not spec.uploads


def test_send_options_and_thread() -> None:
    draft = Draft(
        chat_id="@c",
        text="hi",
        disable_notification=True,
        protect_content=True,
        message_thread_id=42,
    )
    spec = build_requests(draft)[0]
    assert spec.params["disable_notification"] is True
    assert spec.params["protect_content"] is True
    assert spec.params["message_thread_id"] == 42


def test_html_parse_mode_applies_emphasis_syntax() -> None:
    """回归：界面承诺 HTML 支持 ** 语法，发出去必须真的是 <b>，不能是字面星号。"""
    draft = Draft(chat_id="@c", text="**Discover Smart Money**", parse_mode="HTML")
    spec = build_requests(draft)[0]
    assert spec.params["text"] == "<b>Discover Smart Money</b>"
    assert spec.params["parse_mode"] == "HTML"


def test_html_parse_mode_applies_syntax_to_caption() -> None:
    draft = Draft(
        chat_id="@c",
        media=Media(kind="photo", source="https://a/1.jpg", caption="**加粗**", parse_mode="HTML"),
    )
    spec = build_requests(draft)[0]
    assert spec.params["caption"] == "<b>加粗</b>"


def test_html_parse_mode_applies_syntax_to_media_group_caption() -> None:
    draft = Draft(
        chat_id="@c",
        media_group=[
            Media(kind="photo", source="https://a/1.jpg", caption="**第一张**", parse_mode="HTML"),
            Media(kind="photo", source="https://a/2.jpg"),
        ],
    )
    spec = build_requests(draft)[0]
    assert spec.params["media"][0]["caption"] == "<b>第一张</b>"


def test_html_parse_mode_keeps_real_tags() -> None:
    draft = Draft(chat_id="@c", text="<i>原生斜体</i>", parse_mode="HTML")
    assert build_requests(draft)[0].params["text"] == "<i>原生斜体</i>"


def test_non_html_parse_modes_are_left_untouched() -> None:
    """MarkdownV2 与纯文本由 Telegram 自己解析，我们不能替它改字。"""
    plain = Draft(chat_id="@c", text="**x**", parse_mode="MarkdownV2")
    assert build_requests(plain)[0].params["text"] == "**x**"
    bare = Draft(chat_id="@c", text="**x**", parse_mode=None)
    assert build_requests(bare)[0].params["text"] == "**x**"
