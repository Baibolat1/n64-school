"""In-memory rate limiting for user actions.

Scope decision (see ``docs/PLAN.md``, «Rate limit»): the counters live in the
memory of the single process that serves the site. That is enough for a school
and needs no migration, but it does *not* survive a restart and does not add up
across workers -- if the site ever runs behind several workers or needs the
limits to be authoritative, the counters have to move to a shared store (a
table).

The limiter is a sliding-window log: per key it keeps the timestamps of the
attempts still inside the window and drops the ones that fell out. A lock
guards the dict because FastAPI runs the synchronous handlers in a thread pool,
so two requests can hit the limiter at the same time.

The clock is injectable so tests can advance time without sleeping.
"""

import threading
import time
from collections import deque
from dataclasses import dataclass
from math import ceil

__all__ = ["RateLimitResult", "RateLimiter", "limiter"]

# How often (in seconds of limiter time) stale keys are swept out of the dict.
# Without a sweep the dict would keep one entry per student forever: a key only
# empties when it is checked again, so a student who acts once and never comes
# back would leak their window for the life of the process.
_PRUNE_INTERVAL_SECONDS = 300


@dataclass(frozen=True)
class RateLimitResult:
    """Outcome of one :meth:`RateLimiter.check` call.

    ``remaining`` is how many further attempts fit in the window *after* this
    one, and ``retry_after`` (whole seconds, at least 1) is set only on refusal:
    it is how long the caller should wait before the oldest attempt leaves the
    window.
    """

    allowed: bool
    remaining: int = 0
    retry_after: int = 0


class RateLimiter:
    """Sliding-window attempt counter keyed by an arbitrary string."""

    def __init__(self, clock=time.monotonic) -> None:
        self._clock = clock
        # key -> (window the key was last used with, timestamps of its attempts)
        self._hits: dict[str, tuple[int, deque[float]]] = {}
        self._lock = threading.Lock()
        self._last_prune = clock()

    def check(self, key: str, *, limit: int, window_seconds: int) -> RateLimitResult:
        """Record an attempt for ``key`` and say whether it is allowed.

        Allowed attempts are recorded; refused ones are not, so a client that
        keeps hammering the endpoint cannot push its own window forward.
        """
        if limit <= 0:
            # A non-positive limit means the action is switched off entirely.
            return RateLimitResult(allowed=False, retry_after=window_seconds or 1)

        now = self._clock()
        with self._lock:
            self._prune(now)

            stored_window, hits = self._hits.get(key) or (window_seconds, deque())
            # Remember the wider of the two windows: should a caller ever shrink
            # the window, attempts recorded under the old one must not be
            # forgotten early. The stored window is only used for the sweep.
            self._hits[key] = (max(stored_window, window_seconds), hits)

            cutoff = now - window_seconds
            while hits and hits[0] <= cutoff:
                hits.popleft()

            if len(hits) >= limit:
                # The oldest recorded attempt has to age out before there is room.
                wait = hits[0] + window_seconds - now
                return RateLimitResult(allowed=False, retry_after=max(1, ceil(wait)))

            hits.append(now)
            return RateLimitResult(allowed=True, remaining=limit - len(hits))

    def reset(self, key: str | None = None) -> None:
        """Forget all counters, or just one key (used by tests and admin tools)."""
        with self._lock:
            if key is None:
                self._hits.clear()
            else:
                self._hits.pop(key, None)
            self._last_prune = self._clock()

    def _prune(self, now: float) -> None:
        """Drop keys that can no longer affect any decision.

        The caller already holds the lock. A key is dropped when its newest
        attempt is older than that key's own window -- at that point every
        attempt it holds has aged out, so forgetting it changes no verdict.
        """
        if now - self._last_prune < _PRUNE_INTERVAL_SECONDS:
            return
        self._hits = {
            key: (window, hits)
            for key, (window, hits) in self._hits.items()
            if hits and hits[-1] + window > now
        }
        self._last_prune = now


# Process-wide limiter shared by every request handler.
limiter = RateLimiter()
