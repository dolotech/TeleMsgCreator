"""草稿校验：把 Telegram 的硬性约束变成可读的 issue 列表。"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from . import limits
from .models import Draft, InlineButton, Media


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(slots=True)
class Issue:
    severity: Severity
    field: str
    message: str
    hint: str | None = None

    def as_dict(self) -> dict[str, str]:
        data = {"severity": self.severity.value, "field": self.field, "message": self.message}
        if self.hint:
            data["hint"] = self.hint
        return data


@dataclass(slots=True)
class Report:
    issues: list[Issue] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity is Severity.ERROR]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity is Severity.WARNING]

    @property
    def ok(self) -> bool:
        return not self.errors

    def add(self, severity: Severity, field_name: str, message: str, hint: str | None = None) -> None:
        self.issues.append(Issue(severity, field_name, message, hint))

    def error(self, field_name: str, message: str, hint: str | None = None) -> None:
        self.add(Severity.ERROR, field_name, message, hint)

    def warn(self, field_name: str, message: str, hint: str | None = None) -> None:
        self.add(Severity.WARNING, field_name, message, hint)

    def note(self, message: str) -> None:
        """中性说明：不是问题，但用户应该知道（例如正文被自动转成了 caption）。"""
        if message not in self.notes:
            self.notes.append(message)

    def as_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "errors": [i.as_dict() for i in self.errors],
            "warnings": [i.as_dict() for i in self.warnings],
            "notes": list(self.notes),
        }

    def raise_if_invalid(self) -> None:
        if not self.ok:
            from .errors import ValidationError

            raise ValidationError(self.errors)


def _byte_len(text: str) -> int:
    return len(text.encode("utf-8"))


def validate_draft(draft: Draft, *, check_files: bool = True) -> Report:
    report = Report()
    _validate_text(draft, report)
    _validate_keyboard(draft, report)
    _validate_media_group(draft, report)
    _explain_implicit_behaviour(draft, report)
    if check_files:
        _validate_local_files(draft, report)
    return report


def _explain_implicit_behaviour(draft: Draft, report: Report) -> None:
    """把模型里自动做的调整讲清楚，避免「我明明填了正文，怎么变成说明了」。"""
    if draft.caption_from_text:
        limit = limits.CAPTION_MAX
        current = len(draft.effective_caption or "")
        report.note(f"正文已作为媒体说明（caption）发送，当前 {current}/{limit} 字符")
    if draft.is_media_group:
        report.note("相册只有第一条能带说明文字，且相册不支持内联按钮")
    for media in _all_media(draft):
        if media.is_file_id:
            report.note("检测到 file_id：会复用 Telegram 上已有的文件，不再重复上传")
            break


def _validate_text(draft: Draft, report: Report) -> None:
    if draft.text is not None:
        if not draft.text.strip():
            report.warn("text", "正文为空或只有空白字符")
        if len(draft.text) > limits.MESSAGE_TEXT_MAX:
            report.error(
                "text",
                f"正文 {len(draft.text)} 字符，超过 {limits.MESSAGE_TEXT_MAX} 字符上限",
                "拆分为多条消息，或改用图片 caption + 后续消息的写法",
            )
        if draft.parse_mode is None:
            report.warn("parse_mode", "未设置 parse_mode，Markdown/HTML 会原样显示")

    for idx, media in enumerate(_all_media(draft)):
        if media.caption is None:
            continue
        if len(media.caption) > limits.CAPTION_MAX:
            report.error(
                f"media[{idx}].caption",
                f"caption {len(media.caption)} 字符，超过 {limits.CAPTION_MAX} 字符上限",
                "把长文放到独立的文本消息里，或使用 Telegraph/频道评论",
            )


def _validate_keyboard(draft: Draft, report: Report) -> None:
    kb = draft.keyboard
    if not kb.rows:
        return
    if len(kb.rows) > limits.KEYBOARD_ROWS_RECOMMENDED_MAX:
        report.warn("keyboard", f"键盘有 {len(kb.rows)} 行，超过建议的 {limits.KEYBOARD_ROWS_RECOMMENDED_MAX} 行")
    for r_idx, row in enumerate(kb.rows):
        if len(row) > limits.BUTTONS_PER_ROW_RECOMMENDED_MAX:
            report.warn(
                f"keyboard[{r_idx}]",
                f"第 {r_idx + 1} 行有 {len(row)} 个按钮，客户端会压缩显示",
                "建议每行不超过 3 个按钮",
            )
        for b_idx, button in enumerate(row):
            _validate_button(button, f"keyboard[{r_idx}][{b_idx}]", report)

    if draft.is_media_group and kb.rows:
        report.error(
            "keyboard",
            "媒体组（album）不支持 inline keyboard",
            "拆成「相册 + 下一条带按钮的消息」，或只发一张图",
        )


def _validate_button(button: InlineButton, field_name: str, report: Report) -> None:
    if len(button.text) > limits.BUTTON_TEXT_RECOMMENDED_MAX:
        report.warn(
            field_name,
            f"按钮文字 {len(button.text)} 字符，客户端可能截断",
            "建议不超过 20 个中文字符",
        )
    if button.callback_data is not None and _byte_len(button.callback_data) > limits.CALLBACK_DATA_MAX_BYTES:
        report.error(field_name, "callback_data 超过 64 字节")
    if button.callback_data is not None and draft_is_channel_context(button):
        report.warn(
            field_name,
            "callback_data 按钮在频道中需要机器人能接收频道回调（频道管理员身份），否则点击无响应",
        )
    if button.pay is True:
        report.warn(field_name, "Pay 按钮必须是第一行第一个按钮，否则 Telegram 会拒绝")


def draft_is_channel_context(button: InlineButton) -> bool:  # noqa: ARG001 - 语义占位
    return True


def _validate_media_group(draft: Draft, report: Report) -> None:
    if not draft.media_group:
        return
    n = len(draft.media_group)
    if n < limits.MEDIA_GROUP_MIN:
        report.error("media_group", f"媒体组至少 {limits.MEDIA_GROUP_MIN} 条，当前 {n} 条")
    if n > limits.MEDIA_GROUP_MAX:
        report.error("media_group", f"媒体组最多 {limits.MEDIA_GROUP_MAX} 条，当前 {n} 条")
    kinds = {m.kind for m in draft.media_group}
    if "document" in kinds and len(kinds) > 1:
        report.error("media_group", "媒体组中 document 不能与 photo/video 混用")
    if "audio" in kinds:
        report.error("media_group", "Bot API 不支持在媒体组中发送 audio")


def _validate_local_files(draft: Draft, report: Report) -> None:
    for idx, media in enumerate(_all_media(draft)):
        if not media.is_local_file:
            continue
        path = Path(media.source).expanduser()
        field_name = f"media[{idx}].source"
        if not path.exists():
            report.error(
                field_name,
                f"本地文件不存在：{path}",
                "确认路径无误；如果要引用网络图片请用完整的 https:// 直链，"
                "也可以直接粘贴上一步上传后返回的 file_id",
            )
            continue
        if not path.is_file():
            report.error(field_name, f"这个路径不是文件：{path}", "请选择具体的文件而不是目录")
            continue
        size = path.stat().st_size
        if size == 0:
            report.error(field_name, "文件大小为 0", "重新导出或换一个文件")
            continue
        if media.kind == "photo":
            if size > limits.PHOTO_MAX_BYTES:
                report.error(
                    field_name,
                    f"照片 {size / 1048576:.2f} MB，超过 {limits.PHOTO_MAX_BYTES // 1048576} MB 上限",
                    "压缩后再发（工具内置 `telemsg prepare-image`）",
                )
            _check_photo_dimensions(path, field_name, report)
        elif size > limits.STANDARD_UPLOAD_LIMIT:
            report.error(
                field_name,
                f"文件 {size / 1048576:.2f} MB，超过标准 Bot API 上传上限 50 MB",
                "改用自建 Bot API Server，或先上传后复用 file_id",
            )


def _check_photo_dimensions(path: Path, field_name: str, report: Report) -> None:
    try:
        from PIL import Image  # type: ignore
    except ModuleNotFoundError:  # pragma: no cover - 可选依赖
        return
    try:
        with Image.open(path) as img:
            ok, reason = limits.photo_dimension_ok(*img.size)
    except Exception as exc:  # noqa: BLE001 - 图片损坏不应中断校验
        report.warn(field_name, f"无法读取图片尺寸: {exc}")
        return
    if not ok:
        report.error(field_name, reason or "图片尺寸不符合要求")


def _all_media(draft: Draft) -> Iterable[Media]:
    if draft.media:
        yield draft.media
    yield from draft.media_group
