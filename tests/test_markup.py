from __future__ import annotations

from telemsg.markup import (
    escape_html,
    escape_markdown_v2,
    markdown_to_telegram_html,
    plain_text,
    visible_length,
)


def test_escape_html() -> None:
    assert escape_html('<b>&"x"</b>') == '&lt;b&gt;&amp;"x"&lt;/b&gt;'


def test_escape_markdown_v2() -> None:
    assert escape_markdown_v2("1.2!") == r"1\.2\!"
    assert escape_markdown_v2("a-b(c)") == r"a\-b\(c\)"


def test_markdown_to_html_basics() -> None:
    assert markdown_to_telegram_html("**粗**") == "<b>粗</b>"
    assert markdown_to_telegram_html("__斜__") == "<i>斜</i>"
    assert markdown_to_telegram_html("~~删~~") == "<s>删</s>"
    assert markdown_to_telegram_html("||剧透||") == "<tg-spoiler>剧透</tg-spoiler>"
    assert markdown_to_telegram_html("`code`") == "<code>code</code>"


def test_markdown_code_block_keeps_content_escaped() -> None:
    html = markdown_to_telegram_html("```python\nprint('<x>')\n```")
    assert html.startswith('<pre><code class="language-python">')
    assert "&lt;x&gt;" in html
    assert "<x>" not in html


def test_markdown_links_are_sanitized() -> None:
    assert '<a href="https://t.me/x">点我</a>' in markdown_to_telegram_html("[点我](https://t.me/x)")
    # javascript: 协议不会被转换
    assert "[点我](javascript:alert(1))" in markdown_to_telegram_html("[点我](javascript:alert(1))")


def test_markdown_does_not_break_on_html_injection() -> None:
    html = markdown_to_telegram_html("<script>alert(1)</script> **b**")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "<b>b</b>" in html


def test_plain_text_and_visible_length() -> None:
    assert plain_text("<b>hello</b>") == "hello"
    assert visible_length("<b>hello</b>") == 5
