from __future__ import annotations

import json

import httpx
import pytest

from telemsg.client import TelegramClient, mask_token
from telemsg.errors import ConfigError, TelegramAPIError, TransportError
from telemsg.models import Draft, Media
from telemsg.ratelimit import RateLimiter


def fast_limiter() -> RateLimiter:
    return RateLimiter(global_per_second=1000, group_per_minute=1000, chat_min_interval=0.0)


def make_client(handler, settings) -> TelegramClient:
    return TelegramClient(
        settings=settings,
        transport=httpx.MockTransport(handler),
        limiter=fast_limiter(),
        max_retries=1,
    )


def test_missing_token_raises(settings) -> None:
    settings.bot_token = None
    with pytest.raises(ConfigError):
        TelegramClient(settings=settings, token="", transport=httpx.MockTransport(lambda r: httpx.Response(200)))


def test_mask_token_never_leaks() -> None:
    token = "123456789:AAFsuperSecretTokenValue"
    masked = mask_token(token)
    assert token not in masked
    assert masked.startswith("123456")


@pytest.mark.asyncio
async def test_send_text_success(settings) -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 77, "chat": {"id": -100}}})

    async with make_client(handler, settings) as client:
        result = await client.send_draft(Draft(chat_id="@c", text="hi", parse_mode="HTML"))

    assert result.ok and result.message_id == 77
    assert result.method == "sendMessage"
    assert seen["path"].endswith("/sendMessage")
    assert seen["body"]["chat_id"] == "@c"


@pytest.mark.asyncio
async def test_retry_on_429_then_success(settings) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(
                429,
                json={
                    "ok": False,
                    "error_code": 429,
                    "description": "Too Many Requests",
                    "parameters": {"retry_after": 0},
                },
            )
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    async with make_client(handler, settings) as client:
        result = await client.send_draft(Draft(chat_id="@c", text="hi"))
    assert calls["n"] == 2
    assert result.message_id == 1


@pytest.mark.asyncio
async def test_api_error_is_structured(settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "ok": False,
                "error_code": 400,
                "description": "Bad Request: chat not found",
            },
        )

    async with make_client(handler, settings) as client:
        with pytest.raises(TelegramAPIError) as excinfo:
            await client.send_draft(Draft(chat_id="@nope", text="hi"))

    err = excinfo.value
    assert err.error_code == 400
    assert "chat not found" in err.description
    assert err.is_fatal_for_chat
    assert not err.is_retryable


@pytest.mark.asyncio
async def test_5xx_is_retried_then_raises(settings) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(500, json={"ok": False, "error_code": 500, "description": "Internal"})

    async with make_client(handler, settings) as client:
        with pytest.raises(TelegramAPIError):
            await client.send_draft(Draft(chat_id="@c", text="hi"))
    assert calls["n"] == 2  # 首次 + 1 次重试


@pytest.mark.asyncio
async def test_transport_error_wrapped(settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    async with make_client(handler, settings) as client:
        with pytest.raises(TransportError):
            await client.send_draft(Draft(chat_id="@c", text="hi"))


@pytest.mark.asyncio
async def test_media_group_returns_all_ids(settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "ok": True,
                "result": [{"message_id": 11}, {"message_id": 12}, {"message_id": 13}],
            },
        )

    draft = Draft(
        chat_id="@c",
        media_group=[Media(kind="photo", source=f"https://a/{i}.jpg") for i in range(3)],
    )
    async with make_client(handler, settings) as client:
        result = await client.send_draft(draft)
    assert result.method == "sendMediaGroup"
    assert result.message_ids == [11, 12, 13]
    assert result.message_id == 11


@pytest.mark.asyncio
async def test_upload_uses_multipart(settings, tmp_path, png_bytes) -> None:
    image = tmp_path / "pic.png"
    image.write_bytes(png_bytes)
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["content_type"] = request.headers.get("content-type", "")
        captured["body"] = request.content
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 5}})

    draft = Draft(chat_id="@c", media=Media(kind="photo", source=str(image), caption="c"))
    async with make_client(handler, settings) as client:
        await client.send_draft(draft)
    assert captured["content_type"].startswith("multipart/form-data")
    assert b"attach://file0" in captured["body"]
    assert png_bytes[:8] in captured["body"]


@pytest.mark.asyncio
async def test_dry_run_makes_no_request(settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("dry_run 不应发起网络请求")

    async with make_client(handler, settings) as client:
        spec = await client.dry_run(Draft(chat_id="@c", text="hi"))
    assert spec["method"] == "sendMessage"


@pytest.mark.asyncio
async def test_discover_chats_dedupes_and_reads_types(settings) -> None:
    channel = {"id": -100123, "type": "channel", "title": "我的频道", "username": "mychannel"}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "ok": True,
                "result": [
                    {"update_id": 1, "message": {"chat": channel}},
                    {"update_id": 2, "channel_post": {"chat": channel}},
                    {"update_id": 3, "my_chat_member": {"chat": {"id": 555, "type": "private", "first_name": "我"}}},
                    {"update_id": 4},
                ],
            },
        )

    async with make_client(handler, settings) as client:
        found = await client.discover_chats()
    assert len(found) == 2
    row = [c for c in found if c["id"] == -100123][0]
    assert row["title"] == "我的频道"
    assert row["username"] == "mychannel"


@pytest.mark.asyncio
async def test_get_updates_passes_paging_params(settings) -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "result": []})

    async with make_client(handler, settings) as client:
        assert await client.get_updates(offset=7, limit=50, timeout=0) == []
    assert seen["offset"] == 7
    assert seen["limit"] == 50
    assert seen["timeout"] == 0
