"""Feed services: publishing posts and comments.

Transaction policy (mirrors the other service modules): this layer owns commits
for write operations -- :func:`create_post` and :func:`create_comment` commit
after delegating the row construction to the repositories.

The result of a create attempt is a frozen dataclass carrying either the new ORM
object or a machine-readable :class:`FeedError`; the router maps the reason to a
human message and re-renders the form.
"""

from dataclasses import dataclass
from enum import Enum

from sqlalchemy.orm import Session

from app.models.content import Comment, Post
from app.models.student import Student
from app.repositories import comments as comments_repo
from app.repositories import posts as posts_repo

# Body length limits. These are deliberately generous placeholders; the exact,
# final limits are to be settled together with the content filter in stage 13.
MAX_POST_LENGTH = 5000
MAX_COMMENT_LENGTH = 2000

# Shown in place of the author's alias for anonymous content. Stored verbatim as
# the ``display_alias`` snapshot at publication time.
ANONYMOUS_ALIAS = "Аноним"


class FeedError(str, Enum):
    """Machine-readable reasons a post or comment can be refused."""

    EMPTY = "empty"
    TOO_LONG = "too_long"


@dataclass(frozen=True)
class PostCreateResult:
    """Outcome of a post creation attempt.

    ``body`` always holds the submitted text so the form can echo it back and
    the author does not lose what they typed.
    """

    ok: bool
    post: Post | None = None
    body: str = ""
    reason: FeedError | None = None


@dataclass(frozen=True)
class CommentCreateResult:
    """Outcome of a comment creation attempt (mirrors ``PostCreateResult``)."""

    ok: bool
    comment: Comment | None = None
    body: str = ""
    reason: FeedError | None = None


def _validate_body(body: str, max_length: int) -> FeedError | None:
    """Return the refusal reason for ``body`` or None if it is acceptable."""
    if not body.strip():
        return FeedError.EMPTY
    if len(body) > max_length:
        return FeedError.TOO_LONG
    return None


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
    """Validate and publish a new post by ``student``.

    The author is taken from the session object, never from the request body.
    """
    reason = _validate_body(body, MAX_POST_LENGTH)
    if reason is not None:
        return PostCreateResult(ok=False, body=body, reason=reason)

    # --- Extension point (stages 6-8) -------------------------------------
    # The PII/content filter scans ``body`` here and the rate limiter checks the
    # author's recent activity -- both *before* anything is persisted, and both
    # able to refuse by returning an error result. Neither is wired up yet.
    # ----------------------------------------------------------------------

    post = posts_repo.create_post(
        db,
        author_id=student.id,
        display_alias=display_alias_for(student, is_anonymous),
        is_anonymous=is_anonymous,
        body=body,
    )
    db.commit()
    return PostCreateResult(ok=True, post=post)


def create_comment(
    db: Session, student: Student, post: Post, body: str, is_anonymous: bool
) -> CommentCreateResult:
    """Validate and publish a comment by ``student`` on ``post``.

    The author is taken from the session object and the target from ``post``,
    never from free-form request data (bar the URL id the router has already
    resolved to a visible post).
    """
    reason = _validate_body(body, MAX_COMMENT_LENGTH)
    if reason is not None:
        return CommentCreateResult(ok=False, body=body, reason=reason)

    # --- Extension point (stages 6-8): PII/content filter and rate limit, as
    # in :func:`create_post`.

    comment = comments_repo.create_comment(
        db,
        post_id=post.id,
        author_id=student.id,
        display_alias=display_alias_for(student, is_anonymous),
        is_anonymous=is_anonymous,
        body=body,
    )
    db.commit()
    return CommentCreateResult(ok=True, comment=comment)
