from __future__ import annotations

import pytest

from telemsg.ratelimit import RateLimiter


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.mark.asyncio
async def test_min_interval_between_messages_in_same_chat() -> None:
    clock = FakeClock()
    limiter = RateLimiter(chat_min_interval=1.0, clock=clock, sleeper=clock.sleep)
    await limiter.acquire("@c")
    await limiter.acquire("@c")
    assert clock.sleeps and clock.sleeps[0] == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_global_limit_enforced() -> None:
    clock = FakeClock()
    limiter = RateLimiter(global_per_second=2, chat_min_interval=0.0, clock=clock, sleeper=clock.sleep)
    await limiter.acquire("a")
    await limiter.acquire("b")
    await limiter.acquire("c")
    assert clock.sleeps and clock.sleeps[0] == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_group_limit_enforced() -> None:
    clock = FakeClock()
    limiter = RateLimiter(
        global_per_second=1000, group_per_minute=2, chat_min_interval=0.0, clock=clock, sleeper=clock.sleep
    )
    await limiter.acquire(-100123)
    await limiter.acquire(-100123)
    await limiter.acquire(-100123)
    # 等待按 5 秒切片轮询，总时长约等于 60 秒窗口
    assert clock.sleeps[0] == pytest.approx(5.0)
    assert sum(clock.sleeps) == pytest.approx(60.0)


def test_classify_chat_types() -> None:
    assert RateLimiter.classify(-100123) == "group"
    assert RateLimiter.classify("@channel") == "private"
    assert RateLimiter.classify(None) == "private"
