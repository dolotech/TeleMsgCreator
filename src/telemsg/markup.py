"""文本格式化工具。

Telegram 的两套 parse_mode 都有各自的转义地狱：

* HTML  —— 只需转义 ``& < >``，但用户粘贴的 HTML 会直接生效（XSS 风险）。
* MarkdownV2 —— 需要转义 18 个字符，少一个就整条消息发送失败。

本模块提供：

* :func:`escape_html` / :func:`escape_markdown_v2`：安全转义。
* :func:`markdown_to_telegram_html`：把轻量 Markdown 转成 Telegram HTML。
* :func:`plain_text`：把富文本降级为纯文本（用于字数统计与预览）。
"""

from __future__ import annotations

import html
import re

_MDV2_SPECIAL = r"_*[]()~`>#+\-=|{}.!"
_MDV2_RE = re.compile("([" + re.escape(_MDV2_SPECIAL) + r"])")

_FENCE_RE = re.compile(r"```([A-Za-z0-9_+-]*)\n?(.*?)```", re.DOTALL)
_CODE_RE = re.compile(r"`([^`\n]+)`")
_LINK_RE = re.compile(r"\[([^\]\n]+)\]\(([^)\s]+)\)")
_BOLD_RE = re.compile(r"\*\*(?!\s)(.+?)(?<!\s)\*\*", re.DOTALL)
_ITALIC_RE = re.compile(r"__(?!\s)(.+?)(?<!\s)__", re.DOTALL)
_STRIKE_RE = re.compile(r"~~(?!\s)(.+?)(?<!\s)~~", re.DOTALL)
_SPOILER_RE = re.compile(r"\|\|(?!\s)(.+?)(?<!\s)\|\|", re.DOTALL)

_SAFE_URL_RE = re.compile(r"^(https?://|tg://)", re.IGNORECASE)
_PLACEHOLDER = "\x00PH{}\x00"


def escape_html(text: str) -> str:
    """转义 HTML 特殊字符，使文本可以被安全地放进 parse_mode=HTML 的消息。"""
    return html.escape(text, quote=False)


def escape_markdown_v2(text: str) -> str:
    """转义 MarkdownV2 的所有保留字符。"""
    return _MDV2_RE.sub(r"\\\1", text)


def _protect(store: list[str], value: str) -> str:
    store.append(value)
    return _PLACEHOLDER.format(len(store) - 1)


def markdown_to_telegram_html(
    text: str, *, allow_links: bool = True, escape: bool = True
) -> str:
    """把轻量 Markdown 转成 Telegram 支持的 HTML 子集。

    支持语法：``**粗体**``、``__斜体__``、``~~删除线~~``、``||剧透||``、
    ``` `行内代码` ```、```` ```代码块``` ````、``[文本](https://链接)``。
    未支持的语法原样保留（已转义），不会导致发送失败。

    :param escape: 为 True（默认）时先整体转义 HTML 特殊字符，适合纯 Markdown 场景；
        为 False 时保留原始 HTML 标签，用于 parse_mode=HTML 的输入——
        此时只做强调语法转换，`<b>` 这类标签原样留给调用方处理。
    """
    placeholders: list[str] = []
    work = text

    work = _FENCE_RE.sub(
        lambda m: _protect(
            placeholders,
            "<pre><code{}>{}</code></pre>".format(
                f' class="language-{html.escape(m.group(1), quote=True)}"' if m.group(1) else "",
                html.escape(m.group(2), quote=False),
            ),
        ),
        work,
    )
    work = _CODE_RE.sub(
        lambda m: _protect(placeholders, f"<code>{html.escape(m.group(1), quote=False)}</code>"), work
    )

    if escape:
        work = escape_html(work)

    if allow_links:
        work = _LINK_RE.sub(_link_repl, work)

    work = _BOLD_RE.sub(r"<b>\1</b>", work)
    work = _ITALIC_RE.sub(r"<i>\1</i>", work)
    work = _STRIKE_RE.sub(r"<s>\1</s>", work)
    work = _SPOILER_RE.sub(r"<tg-spoiler>\1</tg-spoiler>", work)

    for idx, value in enumerate(placeholders):
        work = work.replace(_PLACEHOLDER.format(idx), value)
    return work


def _link_repl(match: re.Match[str]) -> str:
    label, url = match.group(1), html.unescape(match.group(2))
    if not _SAFE_URL_RE.match(url):
        return match.group(0)
    return f'<a href="{html.escape(url, quote=True)}">{label}</a>'


_TAG_RE = re.compile(r"<[^>]+>")


def plain_text(rich: str) -> str:
    """粗略地把富文本降级为纯文本（用于字数统计/预览统计）。"""
    return html.unescape(_TAG_RE.sub("", rich))


def visible_length(rich: str) -> int:
    """按 Telegram 的字数口径计算长度（HTML 标签不计入）。"""
    return len(plain_text(rich))
