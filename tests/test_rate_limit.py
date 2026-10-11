"""Tests for :mod:`app.services.rate_limit`.

Pure service, no web layer: a fake clock stands in for ``time.monotonic`` so
the window can be advanced without sleeping.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.rate_limit import _PRUNE_INTERVAL_SECONDS, RateLimiter  # noqa: E402


class FakeClock:
    """A monotonic clock a test can push forward by hand."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture()
def clock():
    return FakeClock()


@pytest.fixture()
def limiter(clock):
    return RateLimiter(clock=clock)


def test_allows_exactly_the_limit_then_refuses(limiter):
    for _ in range(3):
        assert limiter.check("k", limit=3, window_seconds=60).allowed
    refused = limiter.check("k", limit=3, window_seconds=60)
    assert not refused.allowed
    assert refused.retry_after == 60


def test_remaining_counts_down(limiter):
    assert limiter.check("k", limit=3, window_seconds=60).remaining == 2
    assert limiter.check("k", limit=3, window_seconds=60).remaining == 1
    assert limiter.check("k", limit=3, window_seconds=60).remaining == 0


def test_retry_after_shrinks_as_the_oldest_attempt_ages(limiter, clock):
    assert limiter.check("k", limit=1, window_seconds=60).allowed
    clock.advance(20)
    refused = limiter.check("k", limit=1, window_seconds=60)
    assert not refused.allowed
    assert refused.retry_after == 40


def test_attempt_is_allowed_again_after_the_window(limiter, clock):
    assert limiter.check("k", limit=1, window_seconds=60).allowed
    clock.advance(61)
    assert limiter.check("k", limit=1, window_seconds=60).allowed


def test_refused_attempts_are_not_recorded(limiter, clock):
    # A client hammering the endpoint must not push the window forward: the
    # retry_after has to keep shrinking, not reset to a full window each time.
    assert limiter.check("k", limit=1, window_seconds=60).allowed
    clock.advance(30)
    assert limiter.check("k", limit=1, window_seconds=60).retry_after == 30
    clock.advance(10)
    assert limiter.check("k", limit=1, window_seconds=60).retry_after == 20


def test_keys_have_separate_budgets(limiter):
    assert limiter.check("post:1", limit=1, window_seconds=60).allowed
    assert not limiter.check("post:1", limit=1, window_seconds=60).allowed
    # A different student, and a different action for the same student.
    assert limiter.check("post:2", limit=1, window_seconds=60).allowed
    assert limiter.check("comment:1", limit=1, window_seconds=60).allowed


def test_zero_limit_switches_the_action_off(limiter):
    refused = limiter.check("k", limit=0, window_seconds=60)
    assert not refused.allowed
    assert refused.retry_after == 60


def test_reset_clears_one_key(limiter):
    assert limiter.check("a", limit=1, window_seconds=60).allowed
    assert limiter.check("b", limit=1, window_seconds=60).allowed
    limiter.reset("a")
    assert limiter.check("a", limit=1, window_seconds=60).allowed
    assert not limiter.check("b", limit=1, window_seconds=60).allowed


def test_reset_clears_everything(limiter):
    limiter.check("a", limit=1, window_seconds=60)
    limiter.reset()
    assert limiter.check("a", limit=1, window_seconds=60).allowed


def test_prune_forgets_keys_whose_window_has_passed(limiter, clock):
    limiter.check("idle", limit=5, window_seconds=60)
    # Jump past the sweep interval and past the key's own window, then trigger a
    # sweep with an unrelated check.
    clock.advance(_PRUNE_INTERVAL_SECONDS + 61)
    limiter.check("busy", limit=5, window_seconds=60)
    assert "idle" not in limiter._hits
    assert "busy" in limiter._hits


def test_prune_keeps_keys_whose_window_is_still_open(limiter, clock):
    # A long window is still live after the sweep interval, so the key must
    # survive: forgetting it would reset the budget early.
    limiter.check("recent", limit=5, window_seconds=100_000)
    clock.advance(_PRUNE_INTERVAL_SECONDS + 1)
    limiter.check("busy", limit=5, window_seconds=60)
    assert "recent" in limiter._hits
