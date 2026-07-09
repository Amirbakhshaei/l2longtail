import asyncio
import time

import pytest

from infra.rate_limiter import TokenBucketRateLimiter


@pytest.mark.asyncio
async def test_rate_limiter_allows_burst() -> None:
    limiter = TokenBucketRateLimiter(rate=10.0, capacity=5)
    for _ in range(5):
        await limiter.acquire()


@pytest.mark.asyncio
async def test_rate_limiter_throttles_excess() -> None:
    limiter = TokenBucketRateLimiter(rate=100.0, capacity=2)
    await limiter.acquire()
    await limiter.acquire()

    start = time.monotonic()
    await limiter.acquire()
    elapsed = time.monotonic() - start
    assert elapsed > 0.001


@pytest.mark.asyncio
async def test_rate_limiter_refills() -> None:
    limiter = TokenBucketRateLimiter(rate=1000.0, capacity=1)
    await limiter.acquire()
    await asyncio.sleep(0.01)
    await limiter.acquire()
