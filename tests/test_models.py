from __future__ import annotations

import pytest
from pydantic import ValidationError as PydanticValidationError

from telemsg.models import Draft, InlineButton, Keyboard, Media


def test_button_requires_exactly_one_action() -> None:
    with pytest.raises(PydanticValidationError):
        InlineButton(text="没动作")
    with pytest.raises(PydanticValidationError):
        InlineButton(text="两个动作", url="https://a.com", callback_data="x")
    assert InlineButton(text="ok", url="https://a.com").action == "url"


def test_button_url_scheme_is_enforced() -> None:
    with pytest.raises(PydanticValidationError):
        InlineButton(text="坏链接", url="javascript:alert(1)")
    assert InlineButton(text="tg", url="tg://resolve?domain=x").url.startswith("tg://")


def test_callback_data_byte_limit() -> None:
    with pytest.raises(PydanticValidationError):
        InlineButton(text="长", callback_data="a" * 65)
    # 中文按 UTF-8 计算：22 个字符 = 66 字节，超限
    with pytest.raises(PydanticValidationError):
        InlineButton(text="中文", callback_data="中" * 22)
    assert InlineButton(text="边界", callback_data="a" * 64).callback_data


def test_style_and_pay_variants() -> None:
    button = InlineButton(text="付款", pay=True, style="success")
    assert button.action == "pay"
    assert button.to_api() == {"text": "付款", "style": "success", "pay": True}


def test_keyboard_from_string_shorthand() -> None:
    keyboard = Keyboard.from_spec(
        "🛒 购买|https://shop.example.com|📄 文档|https://doc.example.com\n"
        "📋 复制|copy:SAVE20\n"
        "⚡ 回调|cb:payload"
    )
    assert len(keyboard.rows) == 3
    assert len(keyboard.rows[0]) == 2
    assert keyboard.rows[1][0].copy_text.text == "SAVE20"
    assert keyboard.rows[2][0].callback_data == "payload"
    assert keyboard.total_buttons == 4
    assert "inline_keyboard" in keyboard.to_api()


def test_keyboard_from_string_rejects_odd_tokens() -> None:
    with pytest.raises(ValueError):
        Keyboard.from_spec("只有一个文案|https://a.com|多余")


def test_keyboard_from_structured_input() -> None:
    keyboard = Keyboard.from_spec([{"text": "A", "url": "https://a.com"}, {"text": "B", "callback_data": "b"}])
    assert len(keyboard.rows) == 2
    nested = Keyboard.from_spec([[[{"text": "A", "url": "https://a.com"}]][0]])
    assert nested.rows[0][0].text == "A"


def test_draft_moves_text_into_caption() -> None:
    draft = Draft(
        chat_id="@c",
        text="标题",
        parse_mode="HTML",
        media=Media(kind="photo", source="https://example.com/a.jpg"),
    )
    assert draft.text is None
    assert draft.media is not None and draft.media.caption == "标题"
    assert draft.effective_caption == "标题"
    assert draft.effective_parse_mode == "HTML"


def test_draft_rejects_conflicting_media() -> None:
    with pytest.raises(PydanticValidationError):
        Draft(
            chat_id="@c",
            text="x",
            media=Media(kind="photo", source="https://a/1.jpg"),
            media_group=[Media(kind="photo", source="https://a/2.jpg"), Media(kind="photo", source="https://a/3.jpg")],
        )


def test_draft_requires_some_content() -> None:
    with pytest.raises(PydanticValidationError):
        Draft(chat_id="@c")


def test_draft_json_roundtrip() -> None:
    draft = Draft(chat_id="@c", text="hello", keyboard="A|https://a.com")
    restored = Draft.from_json(draft.to_json())
    assert restored.chat_id == "@c"
    assert restored.keyboard.rows[0][0].url == "https://a.com"


def test_media_source_kind_detection(tmp_path) -> None:
    local = tmp_path / "cover.jpg"
    local.write_bytes(b"x")
    assert Media(kind="photo", source=str(local)).source_kind == "local"
    assert Media(kind="photo", source="cover.jpg").source_kind == "local"
    assert Media(kind="photo", source="./pic.png").source_kind == "local"
    assert Media(kind="photo", source="~/pic.png").source_kind == "local"
    assert Media(kind="photo", source="https://a/b.jpg").source_kind == "url"
    assert Media(kind="photo", source="tg://resolve?domain=x").source_kind == "url"
    realistic_file_id = "AgACAgQAAxkBAAIC4mXkZ1234567890abcdefghijklmnopqrstuv"
    assert len(realistic_file_id) >= 40
    assert Media(kind="photo", source=realistic_file_id).source_kind == "file_id"


def test_long_filename_without_extension_is_not_a_file_id() -> None:
    """回归：以前会把 20+ 字符的无扩展名文件名误判成 file_id。"""
    media = Media(kind="photo", source="mysecretfilewithoutanyextension")
    assert media.is_file_id is False
    assert media.is_local_file is True


def test_draft_marks_when_text_becomes_caption() -> None:
    draft = Draft(
        chat_id="@c",
        text="标题",
        media=Media(kind="photo", source="https://a/1.jpg"),
    )
    assert draft.caption_from_text is True
    # 该标记是运行期元数据，不该被序列化出去
    assert "caption_from_text" not in draft.to_json()


def test_draft_without_conversion_has_no_flag() -> None:
    draft = Draft(chat_id="@c", text="纯文本")
    assert draft.caption_from_text is False
