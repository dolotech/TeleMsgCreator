"""领域模型：按钮、键盘、媒体、草稿、发送结果。"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ParseMode = Literal["HTML", "MarkdownV2", "Markdown"]
ButtonStyle = Literal["danger", "success", "primary"]
MediaKind = Literal["photo", "video", "animation", "document", "audio"]

_URL_SCHEME_RE = re.compile(r"^(https?|tg)://", re.IGNORECASE)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class CopyTextButton(BaseModel):
    """点击后把指定文本复制到剪贴板（Bot API 7.11+）。"""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=256)


class WebAppInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str

    @field_validator("url")
    @classmethod
    def _https_only(cls, v: str) -> str:
        if not v.lower().startswith("https://"):
            raise ValueError("Web App URL 必须以 https:// 开头")
        return v


class LoginUrl(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str
    forward_text: str | None = None
    bot_username: str | None = None
    request_write_access: bool | None = None


class SwitchInlineQueryChosenChat(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str | None = Field(default=None, max_length=256)
    allow_user_chats: bool | None = None
    allow_bot_chats: bool | None = None
    allow_group_chats: bool | None = None
    allow_channel_chats: bool | None = None


class InlineButton(BaseModel):
    """一个内联按钮。

    ``text`` / ``style`` / ``icon_custom_emoji_id`` 是修饰字段，其余动作字段
    必须**有且只有一个**（Telegram 官方约束）。
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    text: str = Field(min_length=1)
    style: ButtonStyle | None = None
    icon_custom_emoji_id: str | None = None

    url: str | None = None
    callback_data: str | None = None
    web_app: WebAppInfo | None = None
    login_url: LoginUrl | None = None
    switch_inline_query: str | None = None
    switch_inline_query_current_chat: str | None = None
    switch_inline_query_chosen_chat: SwitchInlineQueryChosenChat | None = None
    copy_text: CopyTextButton | None = None
    pay: bool | None = None

    @field_validator("url")
    @classmethod
    def _check_url(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if not _URL_SCHEME_RE.match(v):
            raise ValueError("按钮 URL 必须是 http(s):// 或 tg:// 链接")
        return v

    @model_validator(mode="after")
    def _exactly_one_action(self) -> InlineButton:
        action_fields = (
            "url",
            "callback_data",
            "web_app",
            "login_url",
            "switch_inline_query",
            "switch_inline_query_current_chat",
            "switch_inline_query_chosen_chat",
            "copy_text",
        )
        used = [name for name in action_fields if getattr(self, name) is not None]
        if self.pay is True:
            used.append("pay")
        if len(used) != 1:
            raise ValueError(f"按钮「{self.text}」必须且只能设置一个动作字段，当前为 {used or '无'}")
        if self.callback_data is not None:
            size = len(self.callback_data.encode("utf-8"))
            if size > 64:
                raise ValueError(f"callback_data 为 {size} 字节，超过 64 字节上限")
        if self.pay is False:
            raise ValueError("pay 只支持 True")
        return self

    @property
    def action(self) -> str:
        for name in ("url", "callback_data", "web_app", "login_url", "copy_text"):
            if getattr(self, name) is not None:
                return name
        if self.pay:
            return "pay"
        if self.switch_inline_query is not None:
            return "switch_inline_query"
        if self.switch_inline_query_current_chat is not None:
            return "switch_inline_query_current_chat"
        return "switch_inline_query_chosen_chat"

    def to_api(self) -> dict[str, Any]:
        """序列化为 Bot API 的 ``InlineKeyboardButton`` 对象。"""
        data = self.model_dump(exclude_none=True, by_alias=True)
        return data

    @classmethod
    def url_button(cls, text: str, url: str, **kw: Any) -> InlineButton:
        return cls(text=text, url=url, **kw)

    @classmethod
    def callback_button(cls, text: str, data: str, **kw: Any) -> InlineButton:
        return cls(text=text, callback_data=data, **kw)

    @classmethod
    def copy_button(cls, text: str, copy: str, **kw: Any) -> InlineButton:
        return cls(text=text, copy_text=CopyTextButton(text=copy), **kw)


class Keyboard(BaseModel):
    """内联键盘：二维数组，外层为行。"""

    model_config = ConfigDict(extra="forbid")

    rows: list[list[InlineButton]] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_shape(self) -> Keyboard:
        for idx, row in enumerate(self.rows):
            if not row:
                raise ValueError(f"第 {idx + 1} 行没有任何按钮")
        return self

    def __bool__(self) -> bool:  # pragma: no cover - 直观性
        return bool(self.rows)

    @property
    def total_buttons(self) -> int:
        return sum(len(r) for r in self.rows)

    def to_api(self) -> dict[str, Any]:
        return {"inline_keyboard": [[b.to_api() for b in row] for row in self.rows]}

    @classmethod
    def from_spec(cls, spec: Any) -> Keyboard:
        """从多种简写构造键盘。

        支持：

        * ``Keyboard`` 实例 / ``[[{...}]]`` / ``[{"text":..,"url":..}]``；
        * 简写字符串：``\\n`` 分行，按钮写成 ``文案|目标``，同一行的多个按钮
          连续写即可，例如 ``"A|https://a|B|https://b"`` 表示一行两个按钮。
          目标支持 ``https://`` / ``cb:回调数据`` / ``copy:复制文本`` / ``query:内联查询``。
        """
        if isinstance(spec, Keyboard):
            return spec
        if spec is None or spec == "":
            return cls()
        if isinstance(spec, str):
            rows: list[list[InlineButton]] = []
            for line in spec.splitlines():
                line = line.strip()
                if not line:
                    continue
                rows.append(_parse_button_row(line))
            return cls(rows=rows)
        if isinstance(spec, dict) and "rows" in spec:
            return cls.model_validate(spec)
        if isinstance(spec, list):
            # 扁平列表 => 每行一个按钮；嵌套列表 => 视为行
            if spec and all(isinstance(x, list) for x in spec):
                return cls(rows=[[InlineButton.model_validate(b) for b in row] for row in spec])
            return cls(rows=[[InlineButton.model_validate(b)] for b in spec])
        raise ValueError(f"无法解析键盘定义: {spec!r}")


def _parse_button_row(line: str) -> list[InlineButton]:
    """把 ``"A|url|B|cb:x"`` 解析成同一行里的两个按钮。"""
    tokens = [t.strip() for t in line.split("|")]
    if len(tokens) == 1:
        token = tokens[0]
        tokens = [token, token] if _URL_SCHEME_RE.match(token) else [token, ""]
    if len(tokens) % 2 != 0:
        raise ValueError(f"按钮简写「{line}」不合法：文案与目标必须成对出现（用 | 分隔）")
    return [_make_button(tokens[i], tokens[i + 1]) for i in range(0, len(tokens), 2)]


def _make_button(label: str, target: str) -> InlineButton:
    """根据 ``文案|目标`` 生成按钮；目标支持 URL 与 cb:/copy:/query: 前缀。"""
    if not label:
        raise ValueError("按钮文案不能为空")
    if target.startswith("cb:"):
        return InlineButton(text=label, callback_data=target[3:])
    if target.startswith("copy:"):
        return InlineButton(text=label, copy_text=CopyTextButton(text=target[5:]))
    if target.startswith("query:"):
        return InlineButton(text=label, switch_inline_query=target[6:])
    if not target:
        raise ValueError(f"按钮「{label}」缺少 url / cb: / copy: / query: 目标")
    return InlineButton(text=label, url=target)


class Media(BaseModel):
    """一条媒体。``source`` 可以是 file_id、公网 URL 或本地路径。"""

    model_config = ConfigDict(extra="forbid")

    kind: MediaKind = "photo"
    source: str = Field(min_length=1)
    caption: str | None = None
    parse_mode: ParseMode | None = None
    has_spoiler: bool = False
    filename: str | None = None
    mime_type: str | None = None

    @model_validator(mode="after")
    def _caption_requires_parse_mode(self) -> Media:
        if self.caption is not None and self.parse_mode is None:
            self.parse_mode = "HTML"
        return self

    @property
    def field_name(self) -> str:
        """在 Bot API 里对应的参数名（photo/video/document/...）。"""
        return self.kind

    @property
    def is_remote_url(self) -> bool:
        return bool(re.match(r"^https?://", self.source, re.IGNORECASE))

    @property
    def is_local_file(self) -> bool:
        if self.is_remote_url:
            return False
        if self.source.startswith("tg://") or re.match(r"^[A-Za-z0-9_-]{20,}$", self.source) and "/" not in self.source:
            # 形如 file_id 的字符串（无路径分隔符且长度较大）
            return False
        return True

    @property
    def is_file_id(self) -> bool:
        return not self.is_remote_url and not self.is_local_file


class LinkPreviewOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_disabled: bool | None = None
    url: str | None = None
    prefer_large_media: bool | None = None
    prefer_small_media: bool | None = None
    show_above_text: bool | None = None


class Draft(BaseModel):
    """一条待发送的消息。

    既可只发文本，也可发单媒体（图/视频/文件）+ caption，或 2-10 条媒体组。
    可附内联键盘。
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    chat_id: str | int | None = None
    text: str | None = None
    parse_mode: ParseMode | None = None
    media: Media | None = None
    media_group: list[Media] = Field(default_factory=list)
    keyboard: Keyboard = Field(default_factory=Keyboard)

    # 发送选项
    disable_notification: bool = False
    protect_content: bool = False
    message_thread_id: int | None = None
    link_preview_options: LinkPreviewOptions | None = None

    # 调度
    schedule_at: datetime | None = None
    cron: str | None = None

    @field_validator("keyboard", mode="before")
    @classmethod
    def _coerce_keyboard(cls, v: Any) -> Any:
        """允许用字符串/列表简写来定义按钮行。"""
        if isinstance(v, (Keyboard, dict)) or v is None:
            return v
        if isinstance(v, (str, list)):
            return Keyboard.from_spec(v)
        return v

    @model_validator(mode="after")
    def _check_content(self) -> Draft:
        if self.media and self.media_group:
            raise ValueError("media 与 media_group 不能同时使用")
        if self.media_group:
            if self.media is not None:
                raise ValueError("media 与 media_group 不能同时使用")
            captions = [m.caption for m in self.media_group if m.caption]
            if len(captions) > 1:
                raise ValueError("媒体组中只有第一条可以带 caption")
        has_media = bool(self.media or self.media_group)
        if not self.text and not has_media:
            raise ValueError("草稿必须包含 text 或 media / media_group")
        if has_media and self.text:
            # 单媒体：text 作为 caption；媒体组：text 作为首条 caption
            if self.media and not self.media.caption:
                self.media.caption = self.text
                if self.media.parse_mode is None:
                    self.media.parse_mode = self.parse_mode or "HTML"
                self.text = None
            elif self.media_group and not self.media_group[0].caption:
                self.media_group[0].caption = self.text
                if self.media_group[0].parse_mode is None:
                    self.media_group[0].parse_mode = self.parse_mode or "HTML"
                self.text = None
        return self

    @property
    def is_media_group(self) -> bool:
        return len(self.media_group) > 0

    @property
    def effective_caption(self) -> str | None:
        if self.media:
            return self.media.caption
        if self.media_group:
            return self.media_group[0].caption
        return None

    @property
    def effective_parse_mode(self) -> ParseMode | None:
        if self.media:
            return self.media.parse_mode
        if self.media_group:
            return self.media_group[0].parse_mode
        return self.parse_mode

    @property
    def scheduled(self) -> bool:
        return self.schedule_at is not None or bool(self.cron)

    def to_json(self, *, indent: int = 2) -> str:
        return self.model_dump_json(indent=indent, exclude_none=True)

    @classmethod
    def from_json(cls, raw: str | bytes) -> Draft:
        data = json.loads(raw)
        if isinstance(data.get("keyboard"), (list, str)):
            data["keyboard"] = Keyboard.from_spec(data["keyboard"])
        return cls.model_validate(data)


class SendResult(BaseModel):
    """一次发送的结果。"""

    ok: bool
    chat_id: str | int | None = None
    message_id: int | None = None
    message_ids: list[int] = Field(default_factory=list)
    method: str | None = None
    sent_at: datetime = Field(default_factory=_now)
    error: str | None = None
    error_code: int | None = None
    raw: dict[str, Any] | None = None

    @classmethod
    def failure(
        cls,
        error: BaseException,
        *,
        chat_id: str | int | None = None,
        method: str | None = None,
    ) -> SendResult:
        return cls(
            ok=False,
            chat_id=chat_id,
            method=method,
            error=str(error),
            error_code=getattr(error, "error_code", None),
        )
