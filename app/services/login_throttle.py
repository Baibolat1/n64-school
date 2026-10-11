"""Login-attempt throttling for the admin and student login forms.

A thin policy wrapper over the same in-process sliding-window limiter that caps
posting (:mod:`app.services.rate_limit`). It differs from posting in who the key
is built from: there is no logged-in student yet, so the only handle available is
the client IP.

The address is used *only* as an in-memory counter key. It is never written to
the database and never logged; the counters are forgotten on restart, like every
other rate-limit key (see "Rate limit" in ``docs/PLAN.md``).

Every attempt is counted before the credentials are checked -- that is what
makes the counter a cap on guessing -- and a successful login clears the counter
so an ordinary typo does not accumulate against the next session.
"""

from app.config import settings
from app.services.rate_limit import limiter

_ADMIN_PREFIX = "login:admin:"
_STUDENT_PREFIX = "login:student:"


def _consume(key: str, limit: int, window_seconds: int) -> int | None:
    """Consume one attempt; return the seconds to wait if it was refused."""
    result = limiter.check(key, limit=limit, window_seconds=window_seconds)
    return None if result.allowed else result.retry_after


def check_admin(ip: str) -> int | None:
    """Count an admin login attempt; wait seconds if the IP is over the limit."""
    return _consume(
        _ADMIN_PREFIX + ip,
        settings.ADMIN_LOGIN_MAX_ATTEMPTS,
        settings.ADMIN_LOGIN_WINDOW_SECONDS,
    )


def check_student(ip: str) -> int | None:
    """Count a student login attempt; wait seconds if the IP is over the limit."""
    return _consume(
        _STUDENT_PREFIX + ip,
        settings.STUDENT_LOGIN_MAX_ATTEMPTS,
        settings.STUDENT_LOGIN_WINDOW_SECONDS,
    )


def clear_admin(ip: str) -> None:
    """Forget the attempts of ``ip`` after a successful admin login."""
    limiter.reset(_ADMIN_PREFIX + ip)


def clear_student(ip: str) -> None:
    """Forget the attempts of ``ip`` after a successful student login."""
    limiter.reset(_STUDENT_PREFIX + ip)
