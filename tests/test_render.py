from __future__ import annotations

from telemsg.models import Draft, InlineButton, Keyboard, Media, WebAppInfo
from telemsg.render import render_bubble_html, render_standalone_html, sanitize_rich_text


def test_sanitizer_strips_scripts_but_keeps_allowed_tags() -> None:
    html = sanitize_rich_text("<b>ok</b><script>bad()</script>", "HTML")
    assert "<b>ok</b>" in html
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_sanitizer_blocks_javascript_href() -> None:
    html = sanitize_rich_text('<a href="javascript:alert(1)">x</a>', "HTML")
    assert "javascript:alert" not in html or "&quot;" in html
    assert "<a href" not in html


def test_sanitizer_allows_telegram_links() -> None:
    html = sanitize_rich_text('<a href="https://t.me/x">点</a>', "HTML")
    assert 'href="https://t.me/x"' in html
    assert "noopener" in html


def test_sanitizer_supports_spoiler_span() -> None:
    html = sanitize_rich_text('<span class="tg-spoiler">秘密</span>', "HTML")
    assert 'class="tg-spoiler"' in html
    # 非法属性不会被保留，整段标签被当作纯文本转义
    html2 = sanitize_rich_text('<span onclick="x()">y</span>', "HTML")
    assert "<span onclick" not in html2
    assert "&lt;span" in html2


def test_bubble_contains_media_caption_and_buttons() -> None:
    draft = Draft(
        chat_id="@c",
        media=Media(kind="photo", source="https://example.com/a.jpg", caption="标题", parse_mode="HTML"),
        keyboard=Keyboard(rows=[[InlineButton(text="点我", url="https://a.com")]]),
    )
    html = render_bubble_html(draft)
    assert "https://example.com/a.jpg" in html
    assert "标题" in html
    assert "tg-button" in html
    assert "点我" in html


def test_media_group_preview_uses_album() -> None:
    draft = Draft(
        chat_id="@c",
        media_group=[Media(kind="photo", source=f"https://a/{i}.jpg") for i in range(3)],
    )
    assert 'class="tg-album"' in render_bubble_html(draft)


def test_standalone_html_is_self_contained() -> None:
    draft = Draft(chat_id="@c", text="hello", keyboard="A|https://a.com")
    html = render_standalone_html(draft, title="T")
    assert html.startswith("<!doctype html>")
    assert "<style>" in html
    assert "hello" in html


def test_button_style_colors_rendered() -> None:
    draft = Draft(
        chat_id="@c",
        text="x",
        keyboard=Keyboard(rows=[[InlineButton(text="危险", url="https://a.com", style="danger")]]),
    )
    assert "--btn-accent:#e0533d" in render_bubble_html(draft)


def test_html_mode_converts_markdown_emphasis() -> None:
    """回归：HTML 模式下 **粗体** 之前既不生效、也不会去掉星号。"""
    html = sanitize_rich_text("**Discover Smart Money**", "HTML")
    assert html == "<b>Discover Smart Money</b>"
    assert "**" not in html


def test_html_mode_keeps_original_tags_alongside_syntax() -> None:
    html = sanitize_rich_text("<i>原生斜体</i> 与 **语法加粗**", "HTML")
    assert "<i>原生斜体</i>" in html
    assert "<b>语法加粗</b>" in html


def test_plain_mode_leaves_asterisks_alone() -> None:
    """纯文本模式就是纯文本，不做语法转换（这是用户主动选的）。"""
    assert sanitize_rich_text("**x**", None) == "**x**"


def test_web_app_button_icon() -> None:
    draft = Draft(
        chat_id="@c",
        text="x",
        keyboard=Keyboard(rows=[[InlineButton(text="打开", web_app=WebAppInfo(url="https://app.example.com"))]]),
    )
    assert "🌐" in render_bubble_html(draft)
