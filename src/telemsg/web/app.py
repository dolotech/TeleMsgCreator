"""FastAPI 应用：图形化的图文 + 按钮编辑器。"""

from __future__ import annotations

import logging
import mimetypes
import os
import secrets
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from pydantic import ValidationError as PydanticValidationError

from .. import __version__, limits
from ..client import TelegramClient, mask_token
from ..config import DEFAULT_API_BASE, Settings, get_settings, update_env_file
from ..diagnostics import explain
from ..errors import ConfigError, TelegramAPIError, TelemsgError
from ..logging_setup import install_token_redaction
from ..models import Draft
from ..render import render_bubble_html
from ..service import PostService
from ..store import Store
from ..validation import validate_draft

log = logging.getLogger("telemsg.web")

BASE_DIR = Path(__file__).parent
security = HTTPBasic(auto_error=False)
ALLOWED_UPLOAD_SUFFIXES = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".gif",
    ".mp4",
    ".mov",
    ".webm",
    ".m4v",
    ".pdf",
    ".zip",
    ".mp3",
    ".m4a",
    ".ogg",
    ".txt",
}


class PreviewRequest(BaseModel):
    draft: dict[str, Any]


class SendRequest(BaseModel):
    draft: dict[str, Any]
    chat_id: str | int | None = None
    dry_run: bool = False


class ScheduleRequest(BaseModel):
    draft: dict[str, Any]
    chat_id: str | int | None = None
    when: datetime
    name: str | None = None


class TemplateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    draft: dict[str, Any]


class SettingsRequest(BaseModel):
    """界面上的「设置」表单。只提交需要改动的项。"""

    bot_token: str | None = None
    default_chat_id: str | None = None
    api_base: str | None = None
    persist: bool = True
    clear_token: bool = False


EMPTY_BUBBLE = (
    '<div class="tg-bubble tg-empty">'
    "<span>在这里开始写正文，或拖入一张图片…</span>"
    "</div>"
)


def _readable_pydantic_errors(exc: PydanticValidationError) -> str:
    """把 pydantic 的错误数组压成一句人话（否则前端会显示 [object Object]）。"""
    parts: list[str] = []
    for err in exc.errors()[:3]:
        loc = " → ".join(str(x) for x in err.get("loc", ()) if x not in ("draft", "keyboard"))
        message = str(err.get("msg", "")).removeprefix("Value error, ")
        parts.append(f"{loc}：{message}" if loc else message)
    return "；".join(parts) or "草稿不合法"


def _parse_draft(raw: dict[str, Any]) -> Draft:
    """把前端传来的裸 JSON 转成 Draft，失败时给出可读原因。"""
    try:
        return Draft.model_validate(raw)
    except PydanticValidationError as exc:
        raise HTTPException(status_code=400, detail=_readable_pydantic_errors(exc)) from exc


def _is_empty_draft(raw: dict[str, Any]) -> bool:
    has_text = bool(str(raw.get("text") or "").strip())
    media = raw.get("media")
    group = raw.get("media_group") or []
    return not has_text and not media and not group


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    settings.ensure_dirs()
    # 出站请求的 URL 里带 token，绝不能原样落到 uvicorn 的日志里
    install_token_redaction()
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    store = Store(settings.db_path)

    application = FastAPI(
        title="TeleMsgCreator",
        version=__version__,
        description="图文 + 内联按钮的 Telegram 消息编辑器",
    )
    application.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
    if settings.upload_dir.exists():
        application.mount("/uploads", StaticFiles(directory=settings.upload_dir), name="uploads")
    templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

    def require_auth(
        credentials: HTTPBasicCredentials | None = Depends(security),
    ) -> None:
        if not settings.ui_password:
            return
        if credentials is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="需要登录",
                headers={"WWW-Authenticate": "Basic"},
            )
        ok_user = secrets.compare_digest(credentials.username, "telemsg")
        ok_pass = secrets.compare_digest(credentials.password, settings.ui_password)
        if not (ok_user and ok_pass):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="用户名或密码错误",
                headers={"WWW-Authenticate": "Basic"},
            )

    auth = Depends(require_auth)

    async def client_ctx():
        client = TelegramClient(settings=settings)
        try:
            yield client
        finally:
            await client.aclose()

    # ------------------------------------------------------------- pages
    @application.get("/", response_class=HTMLResponse, dependencies=[auth])
    async def index(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={
                "version": __version__,
                "default_chat": settings.default_chat_id or "",
                "has_token": bool(settings.resolved_token),
                "token_hint": mask_token(settings.resolved_token),
                "env_file": str(settings.env_file),
                "allow_env_write": settings.allow_env_write,
                "limits": {
                    "text": limits.MESSAGE_TEXT_MAX,
                    "caption": limits.CAPTION_MAX,
                    "group_max": limits.MEDIA_GROUP_MAX,
                    "photo_mb": limits.PHOTO_MAX_BYTES // 1048576,
                },
            },
        )

    @application.get("/api/health")
    async def health() -> dict[str, Any]:
        return {"ok": True, "version": __version__, "has_token": bool(settings.resolved_token)}

    # ------------------------------------------------------ settings/引导
    @application.get("/api/settings", dependencies=[auth])
    async def read_settings() -> dict[str, Any]:
        return {"ok": True, "settings": settings.public_view()}

    @application.post("/api/settings", dependencies=[auth])
    async def write_settings(payload: SettingsRequest) -> JSONResponse:
        """保存 Bot Token 等配置。

        流程刻意做成「先验证、后写入」：token 只有真的能通过 getMe 才落盘，
        否则用户会在几小时后才发现自己填错了。
        """
        nonlocal settings
        token = payload.bot_token.strip() if payload.bot_token else None
        warnings: list[str] = []
        bot: dict[str, Any] | None = None
        chat_check: dict[str, Any] | None = None

        if token:
            probe = settings.model_copy(update={"bot_token": token})
            try:
                async with TelegramClient(settings=probe, max_retries=0) as client:
                    bot = await client.get_me()
            except Exception as exc:  # noqa: BLE001 - 一切失败都翻译成可读原因
                diagnosis = explain(exc)
                return JSONResponse(
                    status_code=400,
                    content={
                        "ok": False,
                        "error": diagnosis.message,
                        "hint": diagnosis.hint,
                        "raw": diagnosis.raw,
                    },
                )

        target = (payload.default_chat_id or "").strip() or None
        if target and not token and settings.resolved_token:
            # 用现有 token 顺手确认一下这个频道机器人可否访问
            try:
                async with TelegramClient(settings=settings, max_retries=0) as client:
                    chat = await client.get_chat(target)
                chat_check = {
                    "ok": True,
                    "title": chat.get("title") or chat.get("username") or str(chat.get("id")),
                    "type": chat.get("type"),
                }
            except Exception as exc:  # noqa: BLE001 - 校验失败不影响保存
                diagnosis = explain(exc)
                chat_check = {"ok": False, "error": diagnosis.message, "hint": diagnosis.hint}
                warnings.append(
                    f"默认目标「{target}」暂时访问不到：{diagnosis.message}。"
                    "已保存，但发送前请确认机器人已被拉进该频道。"
                )

        updates: dict[str, Any] = {}
        if payload.clear_token:
            updates["bot_token"] = None
        elif token:
            updates["bot_token"] = token
        if payload.default_chat_id is not None:
            updates["default_chat_id"] = target or None
        if payload.api_base is not None:
            updates["api_base"] = (payload.api_base.strip() or DEFAULT_API_BASE)

        if updates:
            settings = settings.model_copy(update=updates)

        saved_to: str | None = None
        if payload.persist and updates and settings.allow_env_write:
            env_updates: dict[str, str | None] = {}
            if "bot_token" in updates:
                env_updates["TELEMSG_BOT_TOKEN"] = updates["bot_token"]
                env_updates["TELEGRAM_BOT_TOKEN"] = None
            if "default_chat_id" in updates:
                env_updates["TELEMSG_DEFAULT_CHAT_ID"] = updates["default_chat_id"]
            if "api_base" in updates:
                env_updates["TELEMSG_API_BASE"] = updates["api_base"]
            try:
                saved_to = str(update_env_file(env_updates, path=Path(settings.env_file)))
            except OSError as exc:
                warnings.append(f"写 .env 失败（{exc}），配置只在本次运行内生效")
        elif payload.persist and not settings.allow_env_write:
            warnings.append("服务端已禁用写 .env（TELEMSG_ALLOW_ENV_WRITE=0），配置只在本次运行内生效")

        if token and os.environ.get("TELEMSG_BOT_TOKEN"):
            warnings.append(
                "检测到环境变量 TELEMSG_BOT_TOKEN，它的优先级高于 .env；"
                "重启后仍会使用环境变量里的值"
            )

        view = settings.public_view()
        view["saved_to"] = saved_to
        return JSONResponse(
            content={
                "ok": True,
                "settings": view,
                "bot": bot,
                "chat_check": chat_check,
                "warnings": warnings,
            }
        )

    @application.get("/api/me", dependencies=[auth])
    async def me() -> JSONResponse:
        try:
            async with TelegramClient(settings=settings) as client:
                info = await client.get_me()
        except (ConfigError, TelegramAPIError, TelemsgError) as exc:
            diagnosis = explain(exc)
            return JSONResponse(status_code=400, content={"ok": False, **diagnosis.as_dict()})
        return JSONResponse(content={"ok": True, "bot": info})

    # ------------------------------------------------------------- draft
    @application.post("/api/preview", dependencies=[auth])
    async def preview(payload: PreviewRequest) -> dict[str, Any]:
        # 空草稿是编辑器的正常初始状态，不该报错刷屏
        if _is_empty_draft(payload.draft):
            return {
                "ok": True,
                "html": EMPTY_BUBBLE,
                "report": {"ok": True, "errors": [], "warnings": [], "notes": []},
            }
        try:
            draft = _parse_draft(payload.draft)
        except HTTPException as exc:
            return {
                "ok": True,
                "html": EMPTY_BUBBLE,
                "report": {
                    "ok": False,
                    "errors": [{"severity": "error", "field": "draft", "message": str(exc.detail)}],
                    "warnings": [],
                    "notes": [],
                },
            }
        report = validate_draft(draft)
        return {
            "ok": True,
            "html": render_bubble_html(draft),
            "report": report.as_dict(),
        }

    @application.post("/api/compile", dependencies=[auth])
    async def compile_request(payload: SendRequest) -> dict[str, Any]:
        draft = _parse_draft(payload.draft)
        try:
            async with TelegramClient(settings=settings) as client:
                spec = await client.dry_run(draft, chat_id=payload.chat_id)
        except ConfigError:
            from ..payload import build_requests

            spec = build_requests(draft, collect_uploads=False)[0].describe()
        return {"ok": True, "request": spec}

    @application.post("/api/send", dependencies=[auth])
    async def send(payload: SendRequest) -> JSONResponse:
        draft = _parse_draft(payload.draft)
        try:
            async with TelegramClient(settings=settings) as client:
                service = PostService(client, settings=settings, store=store)
                result = await service.send(draft, chat_id=payload.chat_id, dry_run=payload.dry_run)
        except Exception as exc:  # noqa: BLE001 - 统一翻译成可读诊断再回给界面
            diagnosis = explain(exc)
            return JSONResponse(
                status_code=400,
                content={
                    "ok": False,
                    "error": diagnosis.message,
                    "hint": diagnosis.hint,
                    "action": diagnosis.action,
                    "raw": diagnosis.raw,
                    "error_code": getattr(exc, "error_code", None),
                },
            )
        return JSONResponse(content={"ok": result.ok, "result": result.model_dump(mode="json")})

    # ------------------------------------------------------------ upload
    @application.post("/api/upload", dependencies=[auth])
    async def upload(file: UploadFile = File(...)) -> dict[str, Any]:
        original = Path(file.filename or "upload.bin").name
        suffix = Path(original).suffix.lower()
        if suffix not in ALLOWED_UPLOAD_SUFFIXES:
            raise HTTPException(status_code=400, detail=f"不支持的文件类型：{suffix or '未知'}")
        data = await file.read()
        if not data:
            raise HTTPException(status_code=400, detail="文件为空")
        if len(data) > limits.UPLOAD_MAX_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"文件超过 {limits.UPLOAD_MAX_BYTES // 1048576} MB 上限",
            )
        asset_id = uuid.uuid4().hex[:12]
        stored = settings.upload_dir / f"{asset_id}{suffix}"
        stored.write_bytes(data)
        mime = file.content_type or mimetypes.guess_type(original)[0] or "application/octet-stream"
        store.add_asset(
            filename=original, path=str(stored), size_bytes=len(data), mime_type=mime
        )
        kind = _kind_for_suffix(suffix)
        return {
            "ok": True,
            "id": asset_id,
            "path": str(stored),
            "url": f"/uploads/{stored.name}",
            "filename": original,
            "size": len(data),
            "mime": mime,
            "kind": kind,
        }

    # -------------------------------------------------------- scheduling
    @application.post("/api/schedule", dependencies=[auth])
    async def schedule(payload: ScheduleRequest) -> dict[str, Any]:
        draft = _parse_draft(payload.draft)
        report = validate_draft(draft)
        if not report.ok:
            return JSONResponse(status_code=400, content={"ok": False, "report": report.as_dict()})
        target = payload.chat_id or draft.chat_id or settings.default_chat_id
        if target is None:
            raise HTTPException(status_code=400, detail="未指定 chat_id")
        when = payload.when if payload.when.tzinfo else payload.when.replace(tzinfo=timezone.utc)
        sid = store.add_schedule(draft, chat_id=target, name=payload.name, run_at=when)
        return {"ok": True, "id": sid, "when": when.isoformat()}

    @application.get("/api/schedules", dependencies=[auth])
    async def list_schedules() -> dict[str, Any]:
        return {"ok": True, "items": store.list_schedules()}

    @application.delete("/api/schedules/{schedule_id}", dependencies=[auth])
    async def cancel_schedule(schedule_id: str) -> dict[str, Any]:
        if not store.cancel_schedule(schedule_id):
            raise HTTPException(status_code=404, detail="计划任务不存在或已执行")
        return {"ok": True}

    # ---------------------------------------------------------- templates
    @application.get("/api/templates", dependencies=[auth])
    async def list_templates() -> dict[str, Any]:
        return {"ok": True, "items": store.list_templates()}

    @application.post("/api/templates", dependencies=[auth])
    async def save_template(payload: TemplateRequest) -> dict[str, Any]:
        sid = store.save_template(payload.name, _parse_draft(payload.draft))
        return {"ok": True, "id": sid}

    @application.get("/api/templates/{name}", dependencies=[auth])
    async def get_template(name: str) -> dict[str, Any]:
        draft = store.get_template(name)
        if draft is None:
            raise HTTPException(status_code=404, detail="模板不存在")
        return {"ok": True, "draft": draft.model_dump(mode="json", exclude_none=True)}

    @application.delete("/api/templates/{name}", dependencies=[auth])
    async def delete_template(name: str) -> dict[str, Any]:
        if not store.delete_template(name):
            raise HTTPException(status_code=404, detail="模板不存在")
        return {"ok": True}

    @application.get("/api/history", dependencies=[auth])
    async def history(limit: int = 30) -> dict[str, Any]:
        return {"ok": True, "items": store.recent_sends(limit)}

    @application.exception_handler(PydanticValidationError)
    async def pydantic_handler(_: Request, exc: PydanticValidationError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"ok": False, "error": exc.errors()})

    return application


def _kind_for_suffix(suffix: str) -> str:
    if suffix in {".jpg", ".jpeg", ".png", ".webp"}:
        return "photo"
    if suffix == ".gif":
        return "animation"
    if suffix in {".mp4", ".mov", ".webm", ".m4v"}:
        return "video"
    if suffix in {".mp3", ".m4a", ".ogg"}:
        return "audio"
    return "document"
