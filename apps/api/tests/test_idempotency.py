"""Idempotency and retry — SPEC-002 §5 step 3, §6.

The fixtures are verbatim output from the TypeScript implementation. They matter more than
most: the idempotency key is what stops an action executing twice, and a key that differs
between the two implementations means the same email sends twice with nothing in the audit
trail to distinguish a duplicate from two decisions.

That exact bug was present in the first draft of this port — Python filtered ``None`` where
the TypeScript filters only ``undefined``, so any payload containing a ``null`` produced a
different key. These cases are why it was caught.
"""

from __future__ import annotations

import asyncio

import pytest

from app.core.crypto import default_idempotency_key, sha256_hex, stable_stringify
from app.core.retry import (
    MAX_HONOURED_RETRY_AFTER_MS,
    AttemptRecord,
    backoff_delay,
    with_retry,
)


class TestStableStringify:
    @pytest.mark.parametrize(
        "item_id,payload,expected_stable,expected_key",
        [
        ('item-1', {'to': ['a@b.co'], 'subject': 'S'},
         '{"subject":"S","to":["a@b.co"]}',
         'ik_8068d695e25fd219be092dc75eb567d94da2beef'),
        ('item-1', {'subject': 'S', 'to': ['a@b.co']},
         '{"subject":"S","to":["a@b.co"]}',
         'ik_8068d695e25fd219be092dc75eb567d94da2beef'),
        ('item-2', {'nested': {'z': 1, 'a': [2, 3]}, 'top': 'x'},
         '{"nested":{"a":[2,3],"z":1},"top":"x"}',
         'ik_78d173fc60f2739c22d6e008c9cee7ebef4c57b1'),
        ('item-3', {},
         '{}',
         'ik_a932f4e5f907faa8acf7fd53814fa1d44d044edb'),
        ('item-4', {'n': 42, 'f': 1.5, 't': True, 'nil': None},
         '{"f":1.5,"n":42,"nil":null,"t":true}',
         'ik_3608d16e7cba29a5264b541633a00e408d92b6ee'),
        ('item-5', {'unicode': 'café 日本語', 'quote': 'he said "hi"'},
         '{"quote":"he said \\"hi\\"","unicode":"café 日本語"}',
         'ik_229335e5c237fb44642d048d6d0545b8d79c3bab'),
        ('item-6', {'arr': [{'b': 1, 'a': 2}, {'d': 3, 'c': 4}]},
         '{"arr":[{"a":2,"b":1},{"c":4,"d":3}]}',
         'ik_dbced80626a63610b53aee84527092311e020215'),
        ('item-7', {'deep': {'a': {'b': {'c': {'d': 'e'}}}}},
         '{"deep":{"a":{"b":{"c":{"d":"e"}}}}}',
         'ik_06921c6ca06b56589a719c5bc6cc3cec6f0a8da1'),
        ],
    )
    def test_matches_the_typescript_byte_for_byte(
        self, item_id, payload, expected_stable, expected_key
    ):
        assert stable_stringify(payload) == expected_stable
        assert default_idempotency_key(item_id, payload) == expected_key

    def test_key_order_does_not_change_the_hash(self):
        """`{a,b}` and `{b,a}` are the same payload and must not execute twice."""
        assert default_idempotency_key("i", {"a": 1, "b": 2}) == default_idempotency_key(
            "i", {"b": 2, "a": 1}
        )

    def test_a_null_value_is_kept_not_dropped(self):
        """The bug this file exists for. Python's None maps to JSON null, which the
        TypeScript keeps; filtering it produced a different key for the same payload."""
        assert stable_stringify({"a": 1, "b": None}) == '{"a":1,"b":null}'

    def test_editing_the_payload_produces_a_new_key(self):
        """An edited action is a different action and should be allowed to execute."""
        before = default_idempotency_key("i", {"to": ["a@b.co"]})
        after = default_idempotency_key("i", {"to": ["c@d.co"]})
        assert before != after

    def test_the_same_payload_on_a_different_item_differs(self):
        assert default_idempotency_key("item-1", {"a": 1}) != default_idempotency_key(
            "item-2", {"a": 1}
        )

    def test_nested_objects_inside_arrays_are_also_sorted(self):
        assert stable_stringify({"arr": [{"b": 1, "a": 2}]}) == '{"arr":[{"a":2,"b":1}]}'

    def test_the_key_is_prefixed_and_bounded(self):
        key = default_idempotency_key("i", {"a": 1})
        assert key.startswith("ik_")
        # 40 hex characters is enough to make collision irrelevant and short enough to read
        # in a log line.
        assert len(key) == 43

    def test_sha256_is_hex(self):
        assert sha256_hex("abc") == (
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        )


class TestBackoff:
    def test_grows_exponentially_and_is_capped(self):
        fixed = lambda: 0.5  # noqa: E731 — midpoint of the jitter window
        assert [backoff_delay(a, random_fn=fixed) for a in (1, 2, 3, 4, 5, 6)] == [
            300, 600, 1200, 2400, 4800, 6000
        ]

    def test_full_jitter_spans_half_the_ceiling_to_the_ceiling(self):
        """Deterministic backoff means every pending execution retries simultaneously when a
        provider recovers — a thundering herd that puts it back down."""
        low = backoff_delay(3, random_fn=lambda: 0.0)
        high = backoff_delay(3, random_fn=lambda: 1.0)
        assert low == 800 and high == 1600

    def test_never_exceeds_the_ceiling(self):
        assert backoff_delay(20, random_fn=lambda: 1.0) == 8000


class TestRetry:
    async def _instant(self, _seconds: float) -> None:
        return None

    async def test_a_transient_failure_is_retried_to_success(self):
        seen = []

        async def op(attempt: int) -> str:
            seen.append(attempt)
            if attempt < 3:
                raise RuntimeError("transient")
            return "ok"

        outcome = await with_retry(op, is_retryable=lambda e: True, sleep=self._instant)
        assert outcome.ok and outcome.value == "ok"
        assert seen == [1, 2, 3]
        assert [r.outcome for r in outcome.attempts] == ["FAILED", "FAILED", "SUCCESS"]

    async def test_a_non_retryable_failure_stops_after_one_attempt(self):
        calls = []

        async def op(attempt: int):
            calls.append(attempt)
            raise RuntimeError("permanent")

        outcome = await with_retry(op, is_retryable=lambda e: False, sleep=self._instant)
        assert not outcome.ok
        assert calls == [1]

    async def test_the_attempt_budget_is_respected(self):
        calls = []

        async def op(attempt: int):
            calls.append(attempt)
            raise RuntimeError("always")

        outcome = await with_retry(op, is_retryable=lambda e: True, sleep=self._instant)
        assert calls == [1, 2, 3]
        assert len(outcome.attempts) == 3

    async def test_every_attempt_is_recorded_including_the_failures(self):
        """The earlier failures are the audit trail: "succeeded on the third try" is a
        different operational fact from "succeeded"."""
        async def op(attempt: int) -> str:
            if attempt < 2:
                raise RuntimeError("transient")
            return "ok"

        outcome = await with_retry(op, is_retryable=lambda e: True, sleep=self._instant)
        assert len(outcome.attempts) == 2
        assert outcome.attempts[0].error_message == "transient"

    async def test_it_never_raises_so_the_caller_decides_what_failure_means(self):
        async def op(attempt: int):
            raise RuntimeError("boom")

        outcome = await with_retry(op, is_retryable=lambda e: True, sleep=self._instant)
        assert outcome.ok is False
        assert isinstance(outcome.error, RuntimeError)

    async def test_a_sane_retry_after_overrides_the_computed_delay(self):
        delays: list[int] = []

        async def op(attempt: int):
            raise RuntimeError("rate limited")

        await with_retry(
            op, is_retryable=lambda e: True, retry_after_ms=lambda e: 1500,
            on_attempt=lambda attempt, delay, err: delays.append(delay),
            sleep=self._instant, random_fn=lambda: 0.5,
        )
        assert delays and all(d == 1500 for d in delays)

    async def test_an_absurd_retry_after_is_ignored(self):
        """A provider asking us to wait an hour is refused: the request is already held
        open, and honouring it would tie up a connection far past any sane timeout."""
        delays: list[int] = []

        async def op(attempt: int):
            raise RuntimeError("rate limited")

        await with_retry(
            op, is_retryable=lambda e: True,
            retry_after_ms=lambda e: MAX_HONOURED_RETRY_AFTER_MS + 1,
            on_attempt=lambda attempt, delay, err: delays.append(delay),
            sleep=self._instant, random_fn=lambda: 0.5,
        )
        assert delays == [300, 600]
