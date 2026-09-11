"""计划任务执行器。

支持一次性（``run_at``）与周期性（cron）任务；周期性任务的下次运行时间交给
APScheduler 的 ``CronTrigger`` 计算，保证与业界 cron 语义一致。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from . import markup
from .client import TelegramClient
from .models import Draft
from .service import PostService
from .store import Store

log = logging.getLogger("telemsg.scheduler")

#: 标准 cron 的星期编号（0/7 = 周日）与 APScheduler（0 = 周一）不一致，需要转换。
_CRON_DOW_NAMES = {
    "0": "sun",
    "1": "mon",
    "2": "tue",
    "3": "wed",
    "4": "thu",
    "5": "fri",
    "6": "sat",
    "7": "sun",
}


def normalize_cron(expr: str) -> str:
    """把标准 5 段式 cron 转成 APScheduler 能正确理解的写法。

    这里只处理星期字段的数字 -> 名称转换，因为 ``1`` 在标准 cron 里是周一、
    在 APScheduler 里却是周二。名称（mon/tue/...）原样保留。
    """
    fields = expr.split()
    if len(fields) not in {5, 6}:
        return expr
    dow_index = 4 if len(fields) == 5 else 5
    fields[dow_index] = _normalize_dow(fields[dow_index])
    return " ".join(fields)


def _normalize_dow(field: str) -> str:
    if field.strip() == "*":
        return field
    out: list[str] = []
    for part in field.split(","):
        base, _, step = part.partition("/")
        suffix = f"/{step}" if step else ""
        if base.strip() == "*":
            out.append(part)
        elif "-" in base:
            low, high = base.split("-", 1)
            out.append(f"{_dow_name(low)}-{_dow_name(high)}{suffix}")
        else:
            out.append(f"{_dow_name(base)}{suffix}")
    return ",".join(out)


def _dow_name(value: str) -> str:
    return _CRON_DOW_NAMES.get(value.strip(), value.strip())


def next_cron_time(cron: str, *, after: datetime | None = None, timezone_name: str = "UTC") -> datetime:
    """根据 5 段式 cron 表达式计算下一次触发时间（UTC）。

    采用标准 cron 语义：``分 时 日 月 周``，星期 ``0``/``7`` 为周日，``1`` 为周一。
    """
    from apscheduler.triggers.cron import CronTrigger

    base = after or datetime.now(timezone.utc)
    trigger = CronTrigger.from_crontab(normalize_cron(cron), timezone=timezone_name)
    nxt = trigger.get_next_fire_time(None, base)
    if nxt is None:  # pragma: no cover - 表达式无法触发
        raise ValueError(f"无法从 cron 表达式 {cron!r} 推导下次运行时间")
    return nxt.astimezone(timezone.utc)


class SchedulerRunner:
    """轮询数据库中的到期任务并发送。"""

    def __init__(
        self,
        service: PostService,
        *,
        store: Store | None = None,
        poll_interval: float = 5.0,
        max_attempts: int = 3,
    ) -> None:
        self.service = service
        self.store = store or service.store
        self.poll_interval = poll_interval
        self.max_attempts = max_attempts
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def run_forever(self) -> None:
        log.info("计划任务执行器已启动，轮询间隔 %.1fs", self.poll_interval)
        while not self._stop.is_set():
            try:
                await self.run_once()
            except Exception:  # noqa: BLE001 - 单轮异常不应终止守护进程
                log.exception("调度轮询出错")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.poll_interval)
            except asyncio.TimeoutError:
                continue
        log.info("计划任务执行器已停止")

    async def run_once(self) -> int:
        due = self.store.due_schedules(limit=20)
        for row in due:
            await self._execute(row)
        return len(due)

    async def _execute(self, row: dict) -> None:
        schedule_id = row["id"]
        attempts = int(row.get("attempts") or 0)
        draft = Draft.from_json(row["payload"])
        draft.chat_id = row["chat_id"]
        preview = markup.plain_text(draft.text or draft.effective_caption or "")[:60]
        log.info("执行计划任务 %s → %s（%s）", schedule_id, row["chat_id"], preview)
        try:
            result = await self.service.send(draft, chat_id=row["chat_id"])
        except Exception as exc:  # noqa: BLE001 - 失败要落库并重试
            if attempts + 1 >= self.max_attempts:
                self.store.mark_schedule(
                    schedule_id, status="failed", next_run_at=None, error=f"{type(exc).__name__}: {exc}"
                )
                log.error("计划任务 %s 连续失败 %d 次，已标记 failed", schedule_id, attempts + 1)
            else:
                delay = 60 * (attempts + 1)
                retry_at = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat()
                self.store.mark_schedule(schedule_id, status="pending", next_run_at=retry_at, error=str(exc))
                log.warning("计划任务 %s 失败，%s 后重试", schedule_id, retry_at)
            return

        if row.get("cron"):
            try:
                nxt = next_cron_time(row["cron"], after=datetime.now(timezone.utc))
                self.store.mark_schedule(schedule_id, status="pending", next_run_at=nxt.isoformat(), error=None)
            except ValueError as exc:
                self.store.mark_schedule(schedule_id, status="failed", next_run_at=None, error=str(exc))
        else:
            self.store.mark_schedule(schedule_id, status="done", next_run_at=None, error=None)
        log.info("计划任务 %s 发送成功 message_id=%s", schedule_id, result.message_id)


async def run_scheduler(client: TelegramClient, *, poll_interval: float = 5.0) -> None:
    """便捷入口：``await run_scheduler(client)``。"""
    runner = SchedulerRunner(PostService(client), poll_interval=poll_interval)
    await runner.run_forever()
