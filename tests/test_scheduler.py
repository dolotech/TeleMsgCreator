from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from telemsg.client import TelegramClient
from telemsg.models import Draft
from telemsg.ratelimit import RateLimiter
from telemsg.scheduler import SchedulerRunner, next_cron_time, normalize_cron
from telemsg.service import PostService
from telemsg.store import Store


def build_runner(settings, handler, **kwargs) -> SchedulerRunner:
    client = TelegramClient(
        settings=settings,
        transport=httpx.MockTransport(handler),
        limiter=RateLimiter(global_per_second=1000, chat_min_interval=0.0),
        max_retries=0,
    )
    service = PostService(client, settings=settings, store=Store(settings.db_path))
    return SchedulerRunner(service, **kwargs)


def test_next_cron_time_is_in_the_future() -> None:
    base = datetime(2026, 1, 1, 8, 30, tzinfo=timezone.utc)
    nxt = next_cron_time("0 9 * * *", after=base)
    assert nxt == datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc)
    # 标准 cron：1 = 周一
    weekly = next_cron_time("0 9 * * 1", after=base)
    assert weekly.weekday() == 0 and weekly > base
    assert weekly == datetime(2026, 1, 5, 9, 0, tzinfo=timezone.utc)


def test_normalize_cron_converts_weekday_numbers() -> None:
    assert normalize_cron("0 9 * * 1") == "0 9 * * mon"
    assert normalize_cron("0 9 * * 0") == "0 9 * * sun"
    assert normalize_cron("0 9 * * 7") == "0 9 * * sun"
    assert normalize_cron("0 9 * * 1-5") == "0 9 * * mon-fri"
    assert normalize_cron("0 9 * * mon") == "0 9 * * mon"
    assert normalize_cron("0 9 * * *") == "0 9 * * *"
    assert normalize_cron("0 9 * * 1,3,5") == "0 9 * * mon,wed,fri"


def test_next_cron_time_rejects_bad_expression() -> None:
    with pytest.raises((ValueError, KeyError, TypeError)):
        next_cron_time("not a cron")


@pytest.mark.asyncio
async def test_due_one_off_job_is_sent_and_marked_done(settings) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 321}})

    runner = build_runner(settings, handler)
    store = runner.store
    store.add_schedule(
        Draft(chat_id="@c", text="定时"),
        chat_id="@c",
        run_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    processed = await runner.run_once()
    assert processed == 1
    assert calls["n"] == 1
    rows = store.list_schedules()
    assert rows[0]["status"] == "done"
    assert rows[0]["attempts"] == 1
    await runner.service.client.aclose()


@pytest.mark.asyncio
async def test_future_job_is_not_executed(settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("未到期不应发送")

    runner = build_runner(settings, handler)
    runner.store.add_schedule(
        Draft(chat_id="@c", text="以后"),
        chat_id="@c",
        run_at=datetime.now(timezone.utc) + timedelta(days=3),
    )
    assert await runner.run_once() == 0
    await runner.service.client.aclose()


@pytest.mark.asyncio
async def test_failed_job_is_retried_then_marked_failed(settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400, json={"ok": False, "error_code": 400, "description": "Bad Request: chat not found"}
        )

    runner = build_runner(settings, handler, max_attempts=2)
    runner.store.add_schedule(
        Draft(chat_id="@bad", text="会失败"),
        chat_id="@bad",
        run_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    await runner.run_once()  # 第 1 次失败 -> 待重试
    row = runner.store.list_schedules()[0]
    assert row["status"] == "pending"
    assert row["attempts"] == 1
    assert "chat not found" in (row["last_error"] or "")

    # 把 next_run_at 拨到过去，再跑一轮 -> 达到最大次数
    retry_at = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
    runner.store.mark_schedule(row["id"], status="pending", next_run_at=retry_at, error=None)
    await runner.run_once()
    row = runner.store.list_schedules()[0]
    assert row["status"] == "failed"
    await runner.service.client.aclose()


@pytest.mark.asyncio
async def test_recurring_job_reschedules_itself(settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    runner = build_runner(settings, handler)
    store = runner.store
    sid = store.add_schedule(
        Draft(chat_id="@c", text="每天"),
        chat_id="@c",
        cron="0 9 * * *",
        next_run_at=datetime.now(timezone.utc) - timedelta(seconds=10),
    )
    await runner.run_once()
    row = [r for r in store.list_schedules() if r["id"] == sid][0]
    assert row["status"] == "pending"
    assert row["next_run_at"] > datetime.now(timezone.utc).isoformat()
    await runner.service.client.aclose()


@pytest.mark.asyncio
async def test_runner_stops(settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    runner = build_runner(settings, handler, poll_interval=0.01)
    runner.stop()
    await runner.run_forever()  # 立即返回
    await runner.service.client.aclose()
