"""预览渲染。

把草稿渲染成贴近 Telegram 观感的 HTML，用于 Web UI 实时预览与
``telemsg preview`` 生成离线预览文件。

安全策略：只允许 Telegram 支持的 HTML 子集通过，其余标签全部转义，
因此预览片段可以安全地嵌进网页，不存在 XSS 风险。
"""

from __future__ import annotations

import base64
import html
import mimetypes
from html.parser import HTMLParser
from pathlib import Path

from .models import Draft, InlineButton, Media

ALLOWED_TAGS = {
    "b",
    "strong",
    "i",
    "em",
    "u",
    "ins",
    "s",
    "strike",
    "del",
    "code",
    "pre",
    "blockquote",
    "tg-spoiler",
    "span",
    "a",
}
VOID_TAGS = {"br"}
INLINE_MAX_BYTES = 8 * 1024 * 1024


class _TelegramHTMLSanitizer(HTMLParser):
    """保留 Telegram 支持的标签，其余一律转义为纯文本。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._open: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag not in ALLOWED_TAGS:
            self.parts.append(html.escape(self.get_starttag_text() or ""))
            return
        if tag in VOID_TAGS:
            self.parts.append(f"<{tag}>")
            return
        safe_attrs = ""
        if tag == "a":
            href = dict(attrs).get("href") or ""
            if href.lower().startswith(("http://", "https://", "tg://", "mailto:")):
                safe_attrs = (
                    f' href="{html.escape(href, quote=True)}" target="_blank" rel="noopener noreferrer"'
                )
            else:
                self.parts.append(html.escape(self.get_starttag_text() or ""))
                return
        elif tag == "span":
            cls = (dict(attrs).get("class") or "").strip()
            if cls != "tg-spoiler":
                self.parts.append(html.escape(self.get_starttag_text() or ""))
                return
            safe_attrs = ' class="tg-spoiler"'
        self.parts.append(f"<{tag}{safe_attrs}>")
        self._open.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in ALLOWED_TAGS and tag not in VOID_TAGS and tag in self._open:
            self.parts.append(f"</{tag}>")
            self._open.remove(tag)
        elif tag not in ALLOWED_TAGS:
            self.parts.append(html.escape(f"</{tag}>"))

    def handle_data(self, data: str) -> None:
        self.parts.append(html.escape(data))

    def handle_entityref(self, name: str) -> None:
        self.parts.append(html.escape(f"&{name};"))

    def handle_charref(self, name: str) -> None:
        self.parts.append(html.escape(f"&#{name};"))

    def result(self) -> str:
        out = "".join(self.parts)
        for tag in reversed(self._open):
            out += f"</{tag}>"
        return out


def sanitize_rich_text(text: str | None, parse_mode: str | None) -> str:
    """把用户文本渲染成安全的 HTML 片段（保留换行）。"""
    if not text:
        return ""
    from .markup import escape_html, markdown_to_telegram_html

    mode = (parse_mode or "").upper()
    if mode == "HTML":
        # parse_mode=HTML 也承诺支持 **粗体** 这类轻量语法（见界面的格式下拉框），
        # 所以先做一次强调转换；escape=False 是为了不破坏用户写的 <b> 等真实标签
        source = markdown_to_telegram_html(text, escape=False)
    elif mode in {"MARKDOWN", "MARKDOWNV2"}:
        source = markdown_to_telegram_html(text)
    else:
        return escape_html(text).replace("\n", "<br>")
    parser = _TelegramHTMLSanitizer()
    parser.feed(source)
    parser.close()
    return parser.result()


def _media_src(media: Media, *, inline: bool) -> str | None:
    if media.is_remote_url:
        return media.source
    if not media.is_local_file:
        return None
    path = Path(media.source).expanduser()
    if not path.is_file():
        return None
    if not inline or path.stat().st_size > INLINE_MAX_BYTES:
        return path.as_uri()
    mime = media.mime_type or mimetypes.guess_type(path.name)[0] or "image/png"
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{data}"


def media_preview_html(media: Media, *, inline: bool = True) -> str:
    src = _media_src(media, inline=inline) if media.kind == "photo" else None
    if media.kind == "photo" and src:
        spoiler = ' class="blurred"' if media.has_spoiler else ""
        alt = html.escape(media.source[:64], quote=True)
        return f'<img{spoiler} src="{html.escape(src, quote=True)}" alt="{alt}">'
    icon = {"photo": "🖼", "video": "🎬", "animation": "🎞", "document": "📄", "audio": "🎵"}.get(
        media.kind, "📎"
    )
    name = media.source if media.is_remote_url else Path(media.source).name
    return (
        '<div class="placeholder-media">'
        f'<span class="ph-icon">{icon}</span>'
        f'<span class="ph-name">{html.escape(name[:80])}</span>'
        f'<span class="ph-kind">{media.kind}</span>'
        "</div>"
    )


def _style_color(style: str | None) -> str:
    return {"danger": "#e0533d", "success": "#3fae5a", "primary": "#3b82f6"}.get(style or "", "#8ab4d8")


def _button_html(button: InlineButton) -> str:
    style = f' style="--btn-accent:{_style_color(button.style)}"' if button.style else ""
    icon = {
        "url": "🔗",
        "callback_data": "⚡",
        "copy_text": "📋",
        "web_app": "🌐",
        "pay": "⭐",
        "login_url": "🔐",
    }.get(button.action, "▸")
    return (
        f'<button class="tg-button" data-action="{html.escape(button.action)}"{style}>'
        f'<span class="btn-icon">{icon}</span>{html.escape(button.text)}</button>'
    )


def render_bubble_html(draft: Draft, *, inline_media: bool = True) -> str:
    """渲染消息气泡（不含外层页面结构）。"""
    parts: list[str] = ['<div class="tg-bubble">']

    if draft.media_group:
        parts.append('<div class="tg-album">')
        for media in draft.media_group:
            parts.append(media_preview_html(media, inline=inline_media))
        parts.append("</div>")
        if draft.effective_caption:
            parts.append(
                '<div class="tg-caption">'
                f"{sanitize_rich_text(draft.effective_caption, draft.effective_parse_mode)}</div>"
            )
    elif draft.media:
        parts.append(media_preview_html(draft.media, inline=inline_media))
        if draft.media.caption:
            parts.append(
                '<div class="tg-caption">'
                f"{sanitize_rich_text(draft.media.caption, draft.media.parse_mode)}</div>"
            )
    elif draft.text:
        parts.append(f'<div class="tg-text">{sanitize_rich_text(draft.text, draft.parse_mode)}</div>')

    if draft.keyboard.rows:
        parts.append('<div class="tg-keyboard">')
        for row in draft.keyboard.rows:
            parts.append('<div class="tg-row">')
            parts.extend(_button_html(b) for b in row)
            parts.append("</div>")
        parts.append("</div>")

    parts.append('<div class="tg-meta"><span class="tg-time">刚刚</span><span class="tg-ticks">✓✓</span></div>')
    parts.append("</div>")
    return "".join(parts)


_STANDALONE_CSS = """
* { box-sizing: border-box; }
body { margin:0; padding:32px; background:#0e1621; color:#e9edf1;
       font:15px/1.5 -apple-system,"PingFang SC","Microsoft YaHei",Segoe UI,sans-serif; }
.wrap { max-width:520px; margin:0 auto; }
h1 { font-size:18px; font-weight:600; margin:0 0 6px; }
.sub { color:#7d8b99; font-size:13px; margin-bottom:20px; }
.tg-bubble { background:#182533; border-radius:14px; padding:10px 12px 8px;
             box-shadow:0 2px 10px rgba(0,0,0,.35); }
.tg-text, .tg-caption { white-space:pre-wrap; word-break:break-word; }
.tg-caption { margin-top:8px; }
.tg-bubble img { max-width:100%; border-radius:10px; display:block; margin:2px 0 6px; }
.tg-bubble img.blurred { filter:blur(18px); }
.tg-album { display:grid; grid-template-columns:1fr 1fr; gap:4px; }
.placeholder-media { background:#22323f; border-radius:10px; padding:14px; display:flex;
                     gap:10px; align-items:center; margin:2px 0 6px; }
.ph-name { flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.ph-kind { color:#7d8b99; font-size:12px; }
.tg-keyboard { margin-top:8px; display:flex; flex-direction:column; gap:4px; }
.tg-row { display:flex; gap:4px; }
.tg-button { flex:1; padding:8px 6px; border:0; border-radius:8px; cursor:pointer;
             background:#2b3a4a; color:var(--btn-accent,#8ab4d8); font-size:14px;
             font-weight:500; display:flex; align-items:center; justify-content:center; gap:5px; }
.tg-button:hover { background:#35455a; }
.btn-icon { opacity:.85; font-size:12px; }
.tg-meta { display:flex; justify-content:flex-end; gap:6px; color:#7d8b99;
           font-size:11px; margin-top:6px; }
.tg-ticks { color:#5aa7e0; }
"""


def render_standalone_html(draft: Draft, *, title: str | None = None) -> str:
    """生成可直接用浏览器打开的离线预览 HTML。"""
    heading = html.escape(title or draft.name or "Telegram 消息预览")
    target = html.escape(str(draft.chat_id or "（未设置目标会话）"))
    return (
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{heading}</title><style>{_STANDALONE_CSS}</style></head><body><div class=\"wrap\">"
        f'<h1>{heading}</h1><div class="sub">目标会话：{target}</div>'
        f"{render_bubble_html(draft, inline_media=False)}"
        "</div></body></html>"
    )
