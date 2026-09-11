"""异步 Telegram Bot API 客户端。

特性：

* 自动重试（网络错误 / 5xx / 429 ``retry_after``）；
* 内置速率限制；
* 结构化错误（:class:`~telemsg.errors.TelegramAPIError`）；
* 永不把 token 写进日志；
* 支持自建 Bot API Server（``api_base``）。
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Any

import httpx

from .config import DEFAULT_API_BASE, Settings, get_settings
from .errors import ConfigError, TelegramAPIError, TransportError
from .logging_setup import mask_token
from .models import Draft, SendResult
from .payload import RequestSpec, Upload, build_requests
from .ratelimit import RateLimiter

log = logging.getLogger("telemsg.client")

_MESSAGE_ID_KEYS = ("message_id",)

__all__ = ["TelegramClient", "mask_token"]


class TelegramClient:
    """Bot API 客户端。可用作异步上下文管理器。"""

    def __init__(
        self,
        token: str | None = None,
        *,
        settings: Settings | None = None,
        base_url: str | None = None,
        timeout: float | None = None,
        max_retries: int | None = None,
        limiter: RateLimiter | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.token = token or self.settings.resolved_token
        if not self.token:
            raise ConfigError(
                "缺少 Bot Token：请设置环境变量 TELEMSG_BOT_TOKEN（或 TELEGRAM_BOT_TOKEN），"
                "或用 --token 传入。"
            )
        self.base_url = (base_url or self.settings.api_base or DEFAULT_API_BASE).rstrip("/")
        self.timeout = timeout or self.settings.timeout_seconds
        self.max_retries = self.settings.max_retries if max_retries is None else max_retries
        self.limiter = limiter or RateLimiter()
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout, connect=min(10.0, self.timeout)),
            follow_redirects=True,
            headers={"User-Agent": "TeleMsgCreator/1.0 (+https://core.telegram.org/bots/api)"},
            proxy=self.settings.proxy or None,
            verify=self.settings.verify_tls,
            transport=transport,
        )

    # -- 生命周期 ------------------------------------------------------
    async def __aenter__(self) -> TelegramClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    # -- 底层调用 ------------------------------------------------------
    @property
    def _endpoint(self) -> str:
        return f"{self.base_url}/bot{self.token}"

    async def call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        uploads: list[Upload] | None = None,
        *,
        chat_id: str | int | None = None,
        rate_limit: bool = True,
        retries: int | None = None,
    ) -> dict[str, Any]:
        """调用任意 Bot API 方法并返回 ``result``。"""
        spec = RequestSpec(method=method, params=params or {}, uploads=uploads or [])
        payload = await self._execute(spec, chat_id=chat_id, rate_limit=rate_limit, retries=retries)
        return payload.get("result", payload)

    async def _execute(
        self,
        spec: RequestSpec,
        *,
        chat_id: str | int | None,
        rate_limit: bool,
        retries: int | None,
    ) -> dict[str, Any]:
        attempts_allowed = self.max_retries if retries is None else retries
        last_error: BaseException | None = None

        for attempt in range(attempts_allowed + 1):
            if rate_limit:
                await self.limiter.acquire(chat_id)
            try:
                response = await self._send_once(spec)
                return self._unwrap(spec.method, response)
            except TelegramAPIError as exc:
                last_error = exc
                if exc.is_rate_limited and attempt < attempts_allowed:
                    retry_after = exc.retry_after
                    delay = retry_after if retry_after is not None else min(2**attempt, 30)
                    log.warning("被限流，%.1fs 后重试 %s", delay, spec.method)
                    await asyncio.sleep(delay + random.uniform(0, 0.25))
                    continue
                if exc.is_retryable and attempt < attempts_allowed:
                    await asyncio.sleep(self._backoff(attempt))
                    continue
                raise
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = exc
                if attempt < attempts_allowed:
                    log.warning(
                        "%s 网络异常（%s），第 %d 次重试", spec.method, type(exc).__name__, attempt + 1
                    )
                    await asyncio.sleep(self._backoff(attempt))
                    continue
                raise TransportError(
                    f"{spec.method} 网络请求失败: {exc}", attempts=attempt + 1, last_exception=exc
                ) from exc

        raise TransportError(
            f"{spec.method} 重试 {attempts_allowed + 1} 次仍失败", last_exception=last_error
        )

    @staticmethod
    def _backoff(attempt: int) -> float:
        return min(2**attempt, 20) + random.uniform(0, 0.5)

    async def _send_once(self, spec: RequestSpec) -> httpx.Response:
        url = f"{self._endpoint}/{spec.method}"
        if spec.has_uploads:
            files = [(u.name, (u.filename, u.content, u.mime_type)) for u in spec.uploads]
            return await self._client.post(url, data=spec.multipart_data(), files=files)
        return await self._client.post(url, json=spec.json_body())

    def _unwrap(self, method: str, response: httpx.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise TransportError(
                f"{method} 返回了非 JSON 响应（HTTP {response.status_code}）", last_exception=exc
            ) from exc

        if payload.get("ok"):
            payload.setdefault("result", True)
            return payload

        raise TelegramAPIError(
            method,
            error_code=payload.get("error_code"),
            description=payload.get("description"),
            parameters=payload.get("parameters"),
            status_code=response.status_code,
        )

    # -- 高层封装 ------------------------------------------------------
    async def send_draft(
        self, draft: Draft, *, chat_id: str | int | None = None, collect_uploads: bool = True
    ) -> SendResult:
        """发送一条草稿（文本 / 单媒体 / 媒体组）。"""
        target = chat_id if chat_id is not None else draft.chat_id
        if target is None:
            raise ConfigError("未指定 chat_id：请在草稿里设置，或用 --chat 指定")
        spec = build_requests(draft, collect_uploads=collect_uploads)[0]
        spec.params["chat_id"] = target
        payload = await self._execute(spec, chat_id=target, rate_limit=True, retries=None)
        return _result_from_payload(payload, method=spec.method, chat_id=target)

    async def dry_run(self, draft: Draft, *, chat_id: str | int | None = None) -> dict[str, Any]:
        """只编译请求，不调用网络。"""
        target = chat_id if chat_id is not None else draft.chat_id
        spec = build_requests(draft, collect_uploads=False)[0]
        if target is not None:
            spec.params["chat_id"] = target
        return spec.describe()

    async def get_me(self) -> dict[str, Any]:
        return await self.call("getMe")

    async def get_chat(self, chat_id: str | int) -> dict[str, Any]:
        return await self.call("getChat", {"chat_id": chat_id}, chat_id=chat_id)

    async def get_updates(
        self,
        *,
        offset: int | None = None,
        limit: int = 100,
        allowed_updates: list[str] | None = None,
        timeout: int = 0,
    ) -> list[dict[str, Any]]:
        """拉取最近更新（默认不阻塞）。用于发现机器人见过的会话。"""
        params: dict[str, Any] = {"limit": limit, "timeout": timeout}
        if offset is not None:
            params["offset"] = offset
        if allowed_updates is not None:
            params["allowed_updates"] = allowed_updates
        result = await self.call("getUpdates", params, rate_limit=False)
        return result if isinstance(result, list) else []

    async def discover_chats(self, *, limit: int = 100) -> list[dict[str, Any]]:
        """从最近更新里归纳出机器人见过的会话（方便找 chat_id）。

        ``getUpdates`` 只保留最近 24 小时且未被 webhook 消费的更新，因此结果可能为空；
        这只是一种便利手段，不是权威来源。
        """
        updates = await self.get_updates(
            limit=limit,
            allowed_updates=["message", "edited_message", "channel_post", "my_chat_member", "chat_member"],
        )
        found: dict[int, dict[str, Any]] = {}
        for update in updates:
            for key in ("message", "edited_message", "channel_post", "edited_channel_post"):
                payload = update.get(key)
                if isinstance(payload, dict) and isinstance(payload.get("chat"), dict):
                    _remember_chat(found, payload["chat"])
            for key in ("my_chat_member", "chat_member"):
                payload = update.get(key)
                if isinstance(payload, dict) and isinstance(payload.get("chat"), dict):
                    _remember_chat(found, payload["chat"])
        return list(found.values())

    async def get_chat_member_count(self, chat_id: str | int) -> int:
        return int(await self.call("getChatMemberCount", {"chat_id": chat_id}, chat_id=chat_id))

    async def get_webhook_info(self) -> dict[str, Any]:
        return await self.call("getWebhookInfo", rate_limit=False)

    async def delete_webhook(self, *, drop_pending_updates: bool = False) -> bool:
        """删除 webhook。注意：这会让原来的接收端收不到更新，务必确认。"""
        return bool(
            await self.call(
                "deleteWebhook",
                {"drop_pending_updates": drop_pending_updates},
                rate_limit=False,
            )
        )

    async def send_chat_action(self, chat_id: str | int, action: str = "typing") -> bool:
        return bool(
            await self.call("sendChatAction", {"chat_id": chat_id, "action": action}, chat_id=chat_id)
        )

    async def delete_message(self, chat_id: str | int, message_id: int) -> bool:
        return bool(
            await self.call(
                "deleteMessage", {"chat_id": chat_id, "message_id": message_id}, chat_id=chat_id
            )
        )

    async def edit_message_reply_markup(
        self, chat_id: str | int, message_id: int, draft: Draft | None
    ) -> bool:
        params: dict[str, Any] = {"chat_id": chat_id, "message_id": message_id}
        params["reply_markup"] = (
            draft.keyboard.to_api() if draft and draft.keyboard.rows else {"inline_keyboard": []}
        )
        return bool(await self.call("editMessageReplyMarkup", params, chat_id=chat_id))

    async def pin_chat_message(
        self, chat_id: str | int, message_id: int, *, notify: bool = False
    ) -> bool:
        return bool(
            await self.call(
                "pinChatMessage",
                {"chat_id": chat_id, "message_id": message_id, "disable_notification": not notify},
                chat_id=chat_id,
            )
        )

    async def resolve_chat_id(self, target: str | int) -> int | None:
        """把 @username 解析成数字 id（用于审计与去重）。"""
        try:
            chat = await self.get_chat(target)
        except TelegramAPIError:
            return None
        return chat.get("id")


def _remember_chat(store: dict[int, dict[str, Any]], chat: dict[str, Any]) -> None:
    chat_id = chat.get("id")
    if chat_id is None:
        return
    store[int(chat_id)] = {
        "id": int(chat_id),
        "type": chat.get("type"),
        "title": chat.get("title"),
        "username": chat.get("username"),
        "first_name": chat.get("first_name"),
    }


def _result_from_payload(payload: dict[str, Any], *, method: str, chat_id: str | int) -> SendResult:
    result = payload.get("result")
    ids: list[int] = []
    if isinstance(result, list):
        ids = [
            int(item["message_id"])
            for item in result
            if isinstance(item, dict) and "message_id" in item
        ]
    elif isinstance(result, dict):
        for key in _MESSAGE_ID_KEYS:
            if key in result:
                ids = [int(result[key])]
                break
    return SendResult(
        ok=True,
        chat_id=chat_id,
        message_id=ids[0] if ids else None,
        message_ids=ids,
        method=method,
        raw=result if isinstance(result, dict) else {"result": result},
    )
