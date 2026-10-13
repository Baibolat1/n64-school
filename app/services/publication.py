"""Shared publication gates for everything a student writes.

Posts, comments and teacher reviews all go through the same three gates, in the
same order:

1. **Shape** -- non-empty and within the length limit.
2. **Rate limit** -- the author's own sliding window (in-process, see
   :mod:`app.services.rate_limit`). The attempt is recorded *before* the filter
   runs, so a message later refused for PII has already spent budget; a message
   refused by the limiter itself does not extend the window, so hammering the
   endpoint cannot be retried into success.
3. **PII filter** -- :mod:`app.services.pii_filter` classifies the text. A HARD
   verdict (phone, e-mail, link, messenger) refuses the message outright and
   leaves a row in ``pii_blocked_attempts`` recording the fact of the refusal
   (author, target, codes) -- never the text; a SOFT one (full name, address,
   class) is not refused at all -- the fragment is replaced by a placeholder and
   a row is queued for a moderator (decision recorded in ``docs/PLAN.md``: such
   content is visible immediately, it is not held as ``pending``).

The rules live here, in one module, rather than in each feature: a new kind of
content must not be able to drift to a different order of gates or to a variant
of the PII handling. A feature only says *what* is being published
(``target_type``), how long it may be and which budget it spends.

What the caller stores is ``clean_text`` -- the author's *original* input with
every flagged fragment redacted. Rules run against a normalized copy of the
text so obfuscation cannot hide a hit, but only the characters the flagged
range really occupies are replaced in what is saved, so the author's own line
breaks and spacing survive. Profanity is not touched at all: it is allowed, and
moderation is by complaint only (see ``docs/PLAN.md``).

Transaction policy (mirrors the other service modules): this layer commits for
one thing only -- the record of a hard-PII refusal, which must survive even
though nothing else about the attempt is stored. The caller owns the commit of
the content itself.
"""

from dataclasses import dataclass
from enum import Enum

from sqlalchemy.orm import Session

from app.core.logging import logger
from app.models.student import Student
from app.repositories import pii_blocked as pii_blocked_repo
from app.repositories import pii_flags as pii_flags_repo
from app.services.pii_filter import (
    FilterResult,
    Verdict,
    blocking_codes,
    check_text,
)
from app.services.rate_limit import limiter

__all__ = [
    "ANONYMOUS_ALIAS",
    "PublishError",
    "Screening",
    "display_alias_for",
    "queue_soft_flags",
    "screen_text",
]

# Shown in place of the author's alias for anonymous content. Stored verbatim as
# the ``display_alias`` snapshot at publication time.
ANONYMOUS_ALIAS = "Аноним"


class PublishError(str, Enum):
    """Machine-readable reasons a piece of content can be refused."""

    EMPTY = "empty"
    TOO_LONG = "too_long"
    RATE_LIMITED = "rate_limited"
    PII_BLOCKED = "pii_blocked"


@dataclass(frozen=True)
class Screening:
    """Outcome of the three gates.

    ``error`` is None when the text may be published. ``clean_text`` then holds
    what to store, and ``verdict`` the filter's full result so the caller can
    queue the soft hits once the row exists (see :func:`queue_soft_flags`).
    ``retry_after`` is set only for :attr:`PublishError.RATE_LIMITED` and is the
    whole number of seconds to wait.
    """

    error: PublishError | None = None
    retry_after: int = 0
    clean_text: str = ""
    verdict: FilterResult | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def display_alias_for(student: Student, is_anonymous: bool) -> str:
    """Alias snapshot to store as ``display_alias`` at publication time.

    The alias is *copied* into the row, so a later alias change does not rewrite
    what has already been published -- this is exactly the intended behaviour.
    Anonymous content always stores the fixed :data:`ANONYMOUS_ALIAS`.
    """
    return ANONYMOUS_ALIAS if is_anonymous else student.alias


def screen_text(
    db: Session,
    *,
    student_id: int,
    target_type: str,
    body: str,
    max_length: int,
    rate_key: str,
    rate_limit: int,
    rate_window_seconds: int,
) -> Screening:
    """Run the three gates over ``body`` on behalf of student ``student_id``.

    ``target_type`` names what is being published (``post``, ``comment``,
    ``review``) and is what the moderation trail records; it is taken from the
    caller, the author from the session object, never from request data.
    """
    if not body.strip():
        return Screening(error=PublishError.EMPTY)
    if len(body) > max_length:
        return Screening(error=PublishError.TOO_LONG)

    limit = limiter.check(rate_key, limit=rate_limit, window_seconds=rate_window_seconds)
    if not limit.allowed:
        # A refusal, so warning -- the same level the login throttle uses.
        logger.warning("publish_rate_limited target_type=%s", target_type)
        return Screening(
            error=PublishError.RATE_LIMITED, retry_after=limit.retry_after
        )

    screened = check_text(body)
    if screened.verdict is Verdict.BLOCK:
        # Only the fact is kept; the message itself is dropped for good. The log
        # names the target and the fired rule codes, never the text.
        codes = blocking_codes(screened)
        logger.info("publish_pii_blocked target_type=%s codes=%s", target_type, codes)
        pii_blocked_repo.record_attempt(
            db,
            student_id=student_id,
            target_type=target_type,
            rule_codes=codes,
        )
        db.commit()
        return Screening(error=PublishError.PII_BLOCKED)

    return Screening(clean_text=screened.clean_text, verdict=screened)


def queue_soft_flags(
    db: Session, target_type: str, target_id: int, verdict: FilterResult | None
) -> None:
    """Queue one moderation row per soft rule hit (no-op on a clean text).

    Only ``verdict.matches`` -- the truncated snippets -- ever reach the queue,
    and only after the content row exists, so a flag always points at something.
    """
    if verdict is None:
        return
    for match in verdict.matches:
        pii_flags_repo.create_flag(
            db,
            target_type=target_type,
            target_id=target_id,
            rule_code=match.code,
            snippet=match.fragment,
        )
