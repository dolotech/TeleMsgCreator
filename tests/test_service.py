from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from telemsg.client import TelegramClient
from telemsg.errors import TelemsgError
from telemsg.models import Draft, Media
from telemsg.ratelimit import RateLimiter
from telemsg.service import PostService
from telemsg.store import Store


def ok_handler(message_id: int = 1):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "result": {"message_id": message_id}})

    return handler


def build_service(settings, handler) -> PostService:
    client = TelegramClient(
        settings=settings,
        transport=httpx.MockTransport(handler),
        limiter=RateLimiter(global_per_second=1000, chat_min_interval=0.0),
        max_retries=0,
    )
    return PostService(client, settings=settings, store=Store(settings.db_path))


@pytest.mark.asyncio
async def test_send_records_audit(settings) -> None:
    service = build_service(settings, ok_handler(42))
    result = await service.send(Draft(chat_id="@c", text="hi"))
    assert result.ok and result.message_id == 42
    rows = service.store.recent_sends()
    assert rows and rows[0]["ok"] == 1 and rows[0]["message_id"] == 42
    await service.client.aclose()


@pytest.mark.asyncio
async def test_send_blocks_invalid_draft(settings) -> None:
    service = build_service(settings, ok_handler())
    with pytest.raises(TelemsgError):
        await service.send(Draft(chat_id="@c", text="a" * 5000))
    await service.client.aclose()


@pytest.mark.asyncio
async def test_dry_run_does_not_send(settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("dry-run 不应发送")

    service = build_service(settings, handler)
    result = await service.send(Draft(chat_id="@c", text="hi"), dry_run=True)
    assert result.ok and result.raw and "dry_run" in result.raw
    await service.client.aclose()


@pytest.mark.asyncio
async def test_template_roundtrip(settings) -> None:
    service = build_service(settings, ok_handler())
    draft = Draft(chat_id="@c", text="模板", keyboard="A|https://a.com")
    service.save_template("tpl", draft)
    loaded = service.load_template("tpl")
    assert loaded.text == "模板"
    assert loaded.keyboard.rows[0][0].url == "https://a.com"
    assert [t["name"] for t in service.list_templates()] == ["tpl"]
    await service.client.aclose()


@pytest.mark.asyncio
async def test_broadcast_collects_failures(settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json

        body = _json.loads(request.content)
        if body["chat_id"] == "@bad":
            return httpx.Response(400, json={"ok": False, "error_code": 400, "description": "chat not found"})
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 9}})

    service = build_service(settings, handler)
    results = await service.broadcast(Draft(text="hi"), ["@good", "@bad"], concurrency=2)
    assert len(results) == 2
    assert sorted(r.ok for r in results) == [False, True]
    await service.client.aclose()


@pytest.mark.asyncio
async def test_schedule_requires_target(settings) -> None:
    service = build_service(settings, ok_handler())
    with pytest.raises(TelemsgError):
        service.schedule(Draft(text="hi"), when=datetime.now(timezone.utc))
    sid = service.schedule(
        Draft(chat_id="@c", text="hi"),
        when=datetime.now(timezone.utc) + timedelta(hours=1),
        name="later",
    )
    assert any(row["id"] == sid for row in service.list_schedules())
    assert service.cancel_schedule(sid)
    await service.client.aclose()


@pytest.mark.asyncio
async def test_preview_html_written(settings, tmp_path) -> None:
    service = build_service(settings, ok_handler())
    out = service.save_preview(Draft(chat_id="@c", text="预览"), tmp_path / "p.html")
    assert out.exists() and "预览" in out.read_text(encoding="utf-8")
    await service.client.aclose()


@pytest.mark.asyncio
async def test_validation_report_exposed(settings) -> None:
    service = build_service(settings, ok_handler())
    report = service.validate(Draft(chat_id="@c", text="hi"), check_files=False)
    assert report.ok
    await service.client.aclose()


def test_draft_with_local_missing_file_is_rejected(settings) -> None:
    service = build_service(settings, ok_handler())
    report = service.validate(Draft(chat_id="@c", media=Media(kind="photo", source="/nope.jpg")))
    assert not report.ok
