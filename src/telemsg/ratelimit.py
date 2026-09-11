"""速率控制。

Telegram 的限制（官方文档 + 实测）：

* 全局约 30 条/秒；
* 同一群组约 20 条/分钟；
* 同一会话建议不低于 1 条/秒。

被限流时 API 会返回 429 并带上 ``retry_after``，客户端要按它退避。
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque

from . import limits


class RateLimiter:
    """异步滑动窗口限流器，同时约束全局、群组与单会话频率。"""

    def __init__(
        self,
        *,
        global_per_second: int = limits.GLOBAL_MESSAGES_PER_SECOND,
        group_per_minute: int = limits.GROUP_MESSAGES_PER_MINUTE,
        chat_min_interval: float = limits.CHAT_MIN_INTERVAL_SECONDS,
        clock=time.monotonic,
        sleeper=asyncio.sleep,
    ) -> None:
        self._global_limit = global_per_second
        self._group_limit = group_per_minute
        self._chat_interval = chat_min_interval
        self._clock = clock
        self._sleep = sleeper
        self._lock = asyncio.Lock()
        self._global: deque[float] = deque()
        self._groups: dict[str, deque[float]] = defaultdict(deque)
        self._last_chat: dict[str, float] = {}

    @staticmethod
    def classify(chat_id: str | int | None) -> str:
        """粗略判断会话类型：群组/超级群需要更严格的限流。"""
        if chat_id is None:
            return "private"
        text = str(chat_id)
        if text.startswith("-100") or text.startswith("-"):
            return "group"
        return "private"

    async def acquire(self, chat_id: str | int | None = None) -> None:
        key = str(chat_id) if chat_id is not None else "global"
        kind = self.classify(chat_id)
        while True:
            async with self._lock:
                now = self._clock()
                self._trim(now)
                wait = self._wait_time(now, key, kind)
                if wait <= 0:
                    self._record(now, key, kind)
                    return
            await self._sleep(min(wait, 5.0))

    def _trim(self, now: float) -> None:
        while self._global and now - self._global[0] > 1.0:
            self._global.popleft()
        for key, bucket in list(self._groups.items()):
            while bucket and now - bucket[0] > 60.0:
                bucket.popleft()
            if not bucket:
                self._groups.pop(key, None)

    def _wait_time(self, now: float, key: str, kind: str) -> float:
        waits = [0.0]
        if len(self._global) >= self._global_limit:
            waits.append(1.0 - (now - self._global[0]))
        if kind == "group":
            bucket = self._groups.get(key)
            if bucket and len(bucket) >= self._group_limit:
                waits.append(60.0 - (now - bucket[0]))
        last = self._last_chat.get(key)
        if last is not None:
            waits.append(self._chat_interval - (now - last))
        return max(waits)

    def _record(self, now: float, key: str, kind: str) -> None:
        self._global.append(now)
        if kind == "group":
            self._groups[key].append(now)
        self._last_chat[key] = now

    def forget(self, chat_id: str | int | None) -> None:
        key = str(chat_id) if chat_id is not None else "global"
        self._groups.pop(key, None)
        self._last_chat.pop(key, None)
