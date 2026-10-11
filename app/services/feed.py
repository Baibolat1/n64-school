"""Feed services: publishing posts and comments.

Transaction policy (mirrors the other service modules): this layer owns commits
for write operations -- :func:`create_post` and :func:`create_comment` commit
after delegating the row construction to the repositories.

Every publish attempt goes through the same three gates, in this order:

1. **Shape** -- non-empty and within the length limit.
2. **Rate limit** -- the student's own sliding window (in-process, see
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

What gets stored is the filter's ``clean_text``, i.e. the *original* input with
every flagged fragment redacted. Rules run against a normalized copy of the text
so obfuscation cannot hide a hit, but only the characters the flagged range
really occupies are replaced in what is saved, so the author's own line breaks
and spacing survive. Profanity is not touched at all: it is allowed, and
moderation is by complaint only (see ``docs/PLAN.md``).

The result of a create attempt is a frozen dataclass carrying either the new ORM
object or a machine-readable :class:`FeedError`; the router maps the reason to a
human message and re-renders the form.
"""

from dataclasses import dataclass
from enum import Enum

from sqlalchemy.orm import Session

from app.config import settings
from app.models.audit import TARGET_COMMENT, TARGET_POST
from app.models.content import Comment, Post
from app.models.student import Student
from app.repositories import comments as comments_repo
from app.repositories import pii_blocked as pii_blocked_repo
from app.repositories import pii_flags as pii_flags_repo
from app.repositories import posts as posts_repo
from app.services.pii_filter import (
    FilterResult,
    Verdict,
    blocking_codes,
    check_text,
)
from app.services.rate_limit import limiter

# Body length limits. Still deliberately generous placeholders: the final,
# product-level limits are to be settled in stage 13.
MAX_POST_LENGTH = 5000
MAX_COMMENT_LENGTH = 2000

# Shown in place of the author's alias for anonymous content. Stored verbatim as
# the ``display_alias`` snapshot at publication time.
ANONYMOUS_ALIAS = "Аноним"


class FeedError(str, Enum):
    """Machine-readable reasons a post or comment can be refused."""

    EMPTY = "empty"
    TOO_LONG = "too_long"
    RATE_LIMITED = "rate_limited"
    PII_BLOCKED = "pii_blocked"


@dataclass(frozen=True)
class PostCreateResult:
    """Outcome of a post creation attempt.

    ``body`` always holds the submitted text so the form can echo it back and
    the author does not lose what they typed. ``retry_after`` is set only for
    :attr:`FeedError.RATE_LIMITED` and is the whole number of seconds to wait.
    """

    ok: bool
    post: Post | None = None
    body: str = ""
    reason: FeedError | None = None
    retry_after: int = 0


@dataclass(frozen=True)
class CommentCreateResult:
    """Outcome of a comment creation attempt (mirrors ``PostCreateResult``)."""

    ok: bool
    comment: Comment | None = None
    body: str = ""
    reason: FeedError | None = None
    retry_after: int = 0


def _validate_body(body: str, max_length: int) -> FeedError | None:
    """Return the refusal reason for ``body`` or None if it is acceptable."""
    if not body.strip():
        return FeedError.EMPTY
    if len(body) > max_length:
        return FeedError.TOO_LONG
    return None


def _rate_limited(key: str, limit: int, window_seconds: int) -> int | None:
    """Consume one attempt for ``key``; return seconds to wait if refused."""
    result = limiter.check(key, limit=limit, window_seconds=window_seconds)
    return None if result.allowed else result.retry_after


def _queue_flags(
    db: Session, target_type: str, target_id: int, verdict: FilterResult
) -> None:
    """Queue one moderation row per soft rule hit (no-op on a clean text).

    Only ``verdict.matches`` -- the truncated snippets -- ever reach the queue.
    """
    for match in verdict.matches:
        pii_flags_repo.create_flag(
            db,
            target_type=target_type,
            target_id=target_id,
            rule_code=match.code,
            snippet=match.fragment,
        )


def _record_blocked(
    db: Session, student: Student, target_type: str, verdict: FilterResult
) -> None:
    """Queue the fact of a hard-PII refusal (no text of it, not even a snippet).

    The author is recorded even for an anonymous attempt: this is the moderation
    trail rather than public content, and it is never shown to students.
    """
    pii_blocked_repo.record_attempt(
        db,
        student_id=student.id,
        target_type=target_type,
        rule_codes=blocking_codes(verdict),
    )


def display_alias_for(student: Student, is_anonymous: bool) -> str:
    """Alias snapshot to store as ``display_alias`` at publication time.

    The alias is *copied* into the row, so a later alias change does not rewrite
    what has already been published -- this is exactly the intended behaviour.
    Anonymous content always stores the fixed :data:`ANONYMOUS_ALIAS`.
    """
    return ANONYMOUS_ALIAS if is_anonymous else student.alias


def create_post(
    db: Session, student: Student, body: str, is_anonymous: bool
) -> PostCreateResult:
    """Validate, screen and publish a new post by ``student``.

    The author is taken from the session object, never from the request body.
    """
    reason = _validate_body(body, MAX_POST_LENGTH)
    if reason is not None:
        return PostCreateResult(ok=False, body=body, reason=reason)

    retry_after = _rate_limited(
        f"post:{student.id}",
        settings.RATE_LIMIT_POSTS,
        settings.RATE_LIMIT_POSTS_WINDOW_SECONDS,
    )
    if retry_after is not None:
        return PostCreateResult(
            ok=False, body=body, reason=FeedError.RATE_LIMITED, retry_after=retry_after
        )

    screened = check_text(body)
    if screened.verdict is Verdict.BLOCK:
        # Only the fact is kept; the message itself is dropped for good.
        _record_blocked(db, student, TARGET_POST, screened)
        db.commit()
        return PostCreateResult(ok=False, body=body, reason=FeedError.PII_BLOCKED)

    post = posts_repo.create_post(
        db,
        author_id=student.id,
        display_alias=display_alias_for(student, is_anonymous),
        is_anonymous=is_anonymous,
        body=screened.clean_text,
    )
    _queue_flags(db, TARGET_POST, post.id, screened)
    db.commit()
    return PostCreateResult(ok=True, post=post)


def create_comment(
    db: Session, student: Student, post: Post, body: str, is_anonymous: bool
) -> CommentCreateResult:
    """Validate, screen and publish a comment by ``student`` on ``post``.

    The author is taken from the session object and the target from ``post``,
    never from free-form request data (bar the URL id the router has already
    resolved to a visible post).
    """
    reason = _validate_body(body, MAX_COMMENT_LENGTH)
    if reason is not None:
        return CommentCreateResult(ok=False, body=body, reason=reason)

    retry_after = _rate_limited(
        f"comment:{student.id}",
        settings.RATE_LIMIT_COMMENTS,
        settings.RATE_LIMIT_COMMENTS_WINDOW_SECONDS,
    )
    if retry_after is not None:
        return CommentCreateResult(
            ok=False, body=body, reason=FeedError.RATE_LIMITED, retry_after=retry_after
        )

    screened = check_text(body)
    if screened.verdict is Verdict.BLOCK:
        _record_blocked(db, student, TARGET_COMMENT, screened)
        db.commit()
        return CommentCreateResult(ok=False, body=body, reason=FeedError.PII_BLOCKED)

    comment = comments_repo.create_comment(
        db,
        post_id=post.id,
        author_id=student.id,
        display_alias=display_alias_for(student, is_anonymous),
        is_anonymous=is_anonymous,
        body=screened.clean_text,
    )
    _queue_flags(db, TARGET_COMMENT, comment.id, screened)
    db.commit()
    return CommentCreateResult(ok=True, comment=comment)
