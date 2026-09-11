"""把 :class:`~telemsg.models.Draft` 编译成 Bot API 请求。"""

from __future__ import annotations

import json
import mimetypes
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import limits
from .errors import MediaSourceError
from .models import Draft, Media


@dataclass(slots=True)
class Upload:
    """一个 multipart 文件部件。``name`` 用于 ``attach://<name>`` 引用。"""

    name: str
    filename: str
    content: bytes
    mime_type: str


@dataclass(slots=True)
class RequestSpec:
    """一次可执行的 API 调用。"""

    method: str
    params: dict[str, Any] = field(default_factory=dict)
    uploads: list[Upload] = field(default_factory=list)

    @property
    def has_uploads(self) -> bool:
        return bool(self.uploads)

    def multipart_data(self) -> dict[str, str]:
        """multipart 场景下所有参数都要转成字符串。"""
        out: dict[str, str] = {}
        for key, value in self.params.items():
            if isinstance(value, (dict, list)):
                out[key] = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            elif isinstance(value, bool):
                out[key] = "true" if value else "false"
            elif value is None:
                continue
            else:
                out[key] = str(value)
        return out

    def json_body(self) -> dict[str, Any]:
        return {k: v for k, v in self.params.items() if v is not None}

    def describe(self) -> dict[str, Any]:
        """可安全落盘/展示的摘要（不含文件内容）。"""
        return {
            "method": self.method,
            "params": self.json_body(),
            "uploads": [
                {"name": u.name, "filename": u.filename, "bytes": len(u.content), "mime": u.mime_type}
                for u in self.uploads
            ],
        }


def build_requests(draft: Draft, *, collect_uploads: bool = True) -> list[RequestSpec]:
    """编译草稿；媒体组会生成 1 条请求（album 本身就是单次调用）。

    :param collect_uploads: 为 False 时不读取本地文件内容（用于干跑与静态校验）。
    """
    if draft.media_group:
        return [_build_media_group(draft, collect_uploads=collect_uploads)]
    if draft.media:
        return [_build_single_media(draft, collect_uploads=collect_uploads)]
    return [_build_text(draft)]


def _base_params(draft: Draft) -> dict[str, Any]:
    params: dict[str, Any] = {"chat_id": draft.chat_id}
    if draft.disable_notification:
        params["disable_notification"] = True
    if draft.protect_content:
        params["protect_content"] = True
    if draft.message_thread_id is not None:
        params["message_thread_id"] = draft.message_thread_id
    return params


def _attach_keyboard(params: dict[str, Any], draft: Draft) -> None:
    if draft.keyboard.rows:
        params["reply_markup"] = draft.keyboard.to_api()


def _build_text(draft: Draft) -> RequestSpec:
    params = _base_params(draft)
    params["text"] = draft.text or ""
    if draft.parse_mode:
        params["parse_mode"] = draft.parse_mode
    if draft.link_preview_options:
        params["link_preview_options"] = draft.link_preview_options.model_dump(exclude_none=True)
    _attach_keyboard(params, draft)
    return RequestSpec(method="sendMessage", params=params)


def _build_single_media(draft: Draft, *, collect_uploads: bool) -> RequestSpec:
    media = draft.media
    assert media is not None  # 由 build_requests 保证
    params = _base_params(draft)
    sources = _resolve_media_source(media, "file0", params, collect_uploads)

    if media.kind == "photo":
        method = "sendPhoto"
        params["photo"] = sources.ref
        if media.has_spoiler:
            params["has_spoiler"] = True
    elif media.kind == "video":
        method = "sendVideo"
        params["video"] = sources.ref
        if media.has_spoiler:
            params["has_spoiler"] = True
    elif media.kind == "animation":
        method = "sendAnimation"
        params["animation"] = sources.ref
        if media.has_spoiler:
            params["has_spoiler"] = True
    elif media.kind == "audio":
        method = "sendAudio"
        params["audio"] = sources.ref
    else:
        method = "sendDocument"
        params["document"] = sources.ref

    if media.caption:
        params["caption"] = media.caption
        if media.parse_mode:
            params["parse_mode"] = media.parse_mode
    _attach_keyboard(params, draft)
    return RequestSpec(method=method, params=params, uploads=sources.uploads)


def _build_media_group(draft: Draft, *, collect_uploads: bool) -> RequestSpec:
    params = _base_params(draft)
    items: list[dict[str, Any]] = []
    uploads: list[Upload] = []
    for idx, media in enumerate(draft.media_group):
        name = f"file{idx}"
        sources = _resolve_media_source(media, name, {}, collect_uploads)
        uploads.extend(sources.uploads)
        entry: dict[str, Any] = {"type": media.kind, "media": sources.ref}
        if media.caption:
            entry["caption"] = media.caption
            if media.parse_mode:
                entry["parse_mode"] = media.parse_mode
        if media.has_spoiler:
            entry["has_spoiler"] = True
        items.append(entry)
    params["media"] = items
    # 注意：Bot API 明确不支持给 album 附带 inline keyboard。
    return RequestSpec(method="sendMediaGroup", params=params, uploads=uploads)


@dataclass(slots=True)
class _Resolved:
    ref: Any
    uploads: list[Upload] = field(default_factory=list)


def _resolve_media_source(media: Media, name: str, params: dict[str, Any], collect_uploads: bool) -> _Resolved:
    if media.is_file_id or media.is_remote_url:
        return _Resolved(ref=media.source)

    path = Path(media.source).expanduser()
    if not collect_uploads:
        return _Resolved(ref=f"attach://{name}")

    if not path.is_file():
        raise MediaSourceError(
            f"媒体文件不存在：{path}",
            source=str(path),
            hint="确认路径；引用网络图片请用完整 https:// 直链，或直接填 file_id",
        )
    size = path.stat().st_size
    if size > limits.UPLOAD_MAX_BYTES:
        raise MediaSourceError(
            f"{path.name} 有 {size / 1048576:.1f} MB，超过本工具上传上限 "
            f"{limits.UPLOAD_MAX_BYTES // 1048576} MB",
            source=str(path),
            hint="标准 Bot API 单文件上限 50MB；更大的文件需要自建 Bot API Server",
        )
    mime = media.mime_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    upload = Upload(name=name, filename=media.filename or path.name, content=path.read_bytes(), mime_type=mime)
    return _Resolved(ref=f"attach://{name}", uploads=[upload])
