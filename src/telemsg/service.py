"""高层服务：校验 → 预览 → 发送 → 审计，以及广播与计划任务。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .client import TelegramClient
from .config import Settings, get_settings
from .diagnostics import explain
from .errors import TelemsgError
from .models import Draft, SendResult
from .render import render_standalone_html
from .store import Store
from .validation import Report, validate_draft

log = logging.getLogger("telemsg.service")


class PostService:
    """把校验、发送、持久化串起来的门面。"""

    def __init__(
        self,
        client: TelegramClient,
        *,
        settings: Settings | None = None,
        store: Store | None = None,
    ) -> None:
        self.client = client
        self.settings = settings or getattr(client, "settings", None) or get_settings()
        self.store = store or Store(self.settings.db_path)

    # -- 校验与预览 ----------------------------------------------------
    def validate(self, draft: Draft, *, check_files: bool = True) -> Report:
        return validate_draft(draft, check_files=check_files)

    async def prepare(self, draft: Draft) -> dict[str, Any]:
        report = self.validate(draft)
        request = await self.client.dry_run(draft)
        return {"report": report.as_dict(), "request": request}

    def preview_html(self, draft: Draft, *, title: str | None = None) -> str:
        return render_standalone_html(draft, title=title)

    def save_preview(self, draft: Draft, path: str | Path) -> Path:
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(self.preview_html(draft), encoding="utf-8")
        return out

    # -- 发送 ----------------------------------------------------------
    async def send(
        self,
        draft: Draft,
        *,
        chat_id: str | int | None = None,
        dry_run: bool | None = None,
    ) -> SendResult:
        """校验并发送；校验失败抛 :class:`TelemsgError`。"""
        report = self.validate(draft)
        if not report.ok:
            raise TelemsgError("草稿未通过校验：" + "; ".join(i.message for i in report.errors))
        if report.warnings:
            log.warning("草稿存在 %d 条警告", len(report.warnings))

        target = chat_id if chat_id is not None else draft.chat_id or self.settings.default_chat_id
        if target is None:
            raise TelemsgError("未指定 chat_id：请在草稿中设置，或用 --chat 指定")
        draft.chat_id = target

        is_dry = self.settings.dry_run if dry_run is None else dry_run
        if is_dry:
            spec = await self.client.dry_run(draft, chat_id=target)
            return SendResult(
                ok=True,
                chat_id=target,
                method=spec["method"],
                notes=report.notes,
                raw={"dry_run": spec},
            )

        try:
            result = await self.client.send_draft(draft, chat_id=target)
        except Exception as exc:  # noqa: BLE001 - 任何失败都要留审计记录再抛出
            failure = SendResult.failure(exc, chat_id=target)
            failure.method = getattr(exc, "method", None)
            failure.hint = explain(exc).hint
            self.store.record_send(failure)
            raise
        result.notes = report.notes
        self.store.record_send(result)
        return result

    # -- 广播 ----------------------------------------------------------
    async def broadcast(
        self,
        draft: Draft,
        chat_ids: Sequence[str | int],
        *,
        concurrency: int = 4,
        stop_on_error: bool = False,
    ) -> list[SendResult]:
        """向多个会话发送同一内容，受速率限制保护。"""
        sem = asyncio.Semaphore(max(1, concurrency))

        async def one(chat: str | int) -> SendResult:
            async with sem:
                copy = draft.model_copy(deep=True)
                copy.chat_id = chat
                try:
                    return await self.send(copy)
                except Exception as exc:  # noqa: BLE001 - 广播要逐条记录失败
                    log.error("向 %s 发送失败: %s", chat, exc)
                    if stop_on_error:
                        raise
                    failure = SendResult.failure(exc, chat_id=chat)
                    failure.hint = explain(exc).hint
                    return failure

        # gather 保序，结果顺序与传入的 chat_ids 一致，方便对照阅读
        return list(await asyncio.gather(*(one(chat) for chat in chat_ids)))

    # -- 模板 ----------------------------------------------------------
    def save_template(self, name: str, draft: Draft) -> str:
        return self.store.save_template(name, draft)

    def load_template(self, name: str) -> Draft:
        draft = self.store.get_template(name)
        if draft is None:
            raise TelemsgError(f"模板不存在: {name}")
        return draft

    def list_templates(self) -> list[dict[str, Any]]:
        return self.store.list_templates()

    # -- 计划任务 ------------------------------------------------------
    def schedule(
        self,
        draft: Draft,
        *,
        when: datetime,
        chat_id: str | int | None = None,
        name: str | None = None,
    ) -> str:
        target = chat_id or draft.chat_id or self.settings.default_chat_id
        if target is None:
            raise TelemsgError("计划任务必须指定 chat_id")
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return self.store.add_schedule(draft, chat_id=target, name=name, run_at=when)

    def list_schedules(self) -> list[dict[str, Any]]:
        return self.store.list_schedules()

    def cancel_schedule(self, schedule_id: str) -> bool:
        return self.store.cancel_schedule(schedule_id)

    # -- 杂项 ----------------------------------------------------------
    async def me(self) -> dict[str, Any]:
        return await self.client.get_me()

    @staticmethod
    def iter_targets(raw: Iterable[str]) -> list[str]:
        return [item.strip() for item in raw if item and item.strip()]
