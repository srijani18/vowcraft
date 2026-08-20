"""Bounded retry with full jitter — SPEC-002 §6.

Full jitter rather than plain exponential backoff: when a provider has an outage, every
pending execution retries on the *same* schedule, and a deterministic backoff means they all
retry simultaneously — a thundering herd that keeps the provider down. Randomising over
``[ceiling/2, ceiling]`` spreads them out.

``sleep`` and ``random`` are injectable so the tests are deterministic and instant. A retry
test that actually waits eight seconds is a test nobody runs.
"""

from __future__ import annotations

import asyncio
import random as _random
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

ATTEMPTS = 3
BASE_MS = 400
MAX_DELAY_MS = 8_000
#: A provider asking us to wait an hour is refused: the request is already held open, and
#: honouring it would tie up a connection far past any sensible timeout.
MAX_HONOURED_RETRY_AFTER_MS = 30_000


def backoff_delay(attempt: int, *, base_ms: int = BASE_MS, max_delay_ms: int = MAX_DELAY_MS,
                  random_fn: Callable[[], float] = _random.random) -> int:
    ceiling = min(max_delay_ms, base_ms * 2 ** (attempt - 1))
    # Full jitter: uniform over [ceiling/2, ceiling].
    return round(ceiling * (0.5 + random_fn() * 0.5))


@dataclass
class AttemptRecord:
    n: int
    outcome: str  # "SUCCESS" | "FAILED"
    duration_ms: int
    error_code: Optional[str] = None
    error_message: Optional[str] = None


@dataclass
class RetryOutcome:
    ok: bool
    value: Any = None
    error: Any = None
    attempts: list[AttemptRecord] = field(default_factory=list)


async def with_retry(
    operation: Callable[[int], Awaitable[Any]],
    *,
    attempts: int = ATTEMPTS,
    base_ms: int = BASE_MS,
    max_delay_ms: int = MAX_DELAY_MS,
    is_retryable: Callable[[Any], bool],
    retry_after_ms: Optional[Callable[[Any], Optional[int]]] = None,
    on_attempt: Optional[Callable[[int, int, Any], None]] = None,
    sleep: Optional[Callable[[float], Awaitable[None]]] = None,
    random_fn: Callable[[], float] = _random.random,
) -> RetryOutcome:
    """Runs ``operation`` until it succeeds or the budget is spent.

    Never raises. The caller decides what a failure means, because "three attempts failed"
    is a business outcome here — it becomes a FAILED action item with a reason — not an
    exception to propagate.
    """
    sleeper = sleep or asyncio.sleep
    records: list[AttemptRecord] = []
    last_error: Any = None

    for attempt in range(1, attempts + 1):
        started = time.perf_counter()
        try:
            value = await operation(attempt)
            records.append(
                AttemptRecord(n=attempt, outcome="SUCCESS",
                              duration_ms=round((time.perf_counter() - started) * 1000))
            )
            return RetryOutcome(ok=True, value=value, attempts=records)
        except Exception as exc:  # noqa: BLE001 — classified below, not swallowed
            duration = round((time.perf_counter() - started) * 1000)
            last_error = exc
            records.append(
                AttemptRecord(
                    n=attempt, outcome="FAILED", duration_ms=duration,
                    error_code=getattr(exc, "code", None) or type(exc).__name__,
                    error_message=str(exc)[:500],
                )
            )
            if attempt >= attempts or not is_retryable(exc):
                break

            delay = backoff_delay(attempt, base_ms=base_ms, max_delay_ms=max_delay_ms,
                                  random_fn=random_fn)
            requested = retry_after_ms(exc) if retry_after_ms else None
            if requested is not None and 0 <= requested <= MAX_HONOURED_RETRY_AFTER_MS:
                # Honour a *sane* Retry-After: the provider knows its own reset window
                # better than our backoff curve does.
                delay = requested
            if on_attempt:
                on_attempt(attempt, delay, exc)
            await sleeper(delay / 1000)

    return RetryOutcome(ok=False, error=last_error, attempts=records)
