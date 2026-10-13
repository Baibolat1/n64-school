"""Feed services: publishing posts and comments.

Both operations delegate the three gates -- shape, rate limit, PII filter -- to
:mod:`app.services.publication`, so the feed follows exactly the same rules as
every other kind of content. This module only says what each one is: how long it
may be, which budget it spends, and what the moderation trail calls it.

Transaction policy (mirrors the other service modules): this layer owns commits
for write operations -- :func:`create_post` and :func:`create_comment` commit
after delegating the row construction to the repositories.

The result of a create attempt is a frozen dataclass carrying either the new ORM
object or a machine-readable :class:`FeedError`; the router maps the reason to a
human message and re-renders the form.
"""

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.config import settings
from app.models.audit import TARGET_COMMENT, TARGET_POST
from app.models.content import Comment, Post
from app.models.student import Student
from app.repositories import comments as comments_repo
from app.repositories import posts as posts_repo
from app.services.publication import (
    ANONYMOUS_ALIAS,  # noqa: F401  (re-exported: the feed's public vocabulary)
    PublishError,
    display_alias_for,
    queue_soft_flags,
    screen_text,
)

# Body length limits. Still deliberately generous placeholders: the final,
# product-level limits are to be settled in stage 13.
MAX_POST_LENGTH = 5000
MAX_COMMENT_LENGTH = 2000

# The feed's own name for the shared refusal reasons, so callers of this module
# keep reading ``FeedError`` while there is one set of reasons for all content.
FeedError = PublishError


def refusal_message(
    reason: FeedError | None, max_length: int, retry_after: int = 0
) -> str:
    """Human-readable sentence for a refused create attempt.

    Here rather than in a router because there are two ways in now -- the form
    that reloads the page and the async endpoint that does not (see
    app.routers.api) -- and a student must not be told two different things
    about the same refusal depending on whether their browser ran a script.
    """
    if reason is FeedError.EMPTY:
        return "Текст не может быть пустым"
    if reason is FeedError.TOO_LONG:
        return f"Слишком длинно: максимум {max_length} символов"
    if reason is FeedError.RATE_LIMITED:
        return f"Слишком часто. Попробуйте снова через {retry_after} с."
    if reason is FeedError.PII_BLOCKED:
        return (
            "В тексте есть данные, которые нельзя публиковать: телефоны, "
            "адреса почты, ссылки и мессенджеры. Уберите их и отправьте снова."
        )
    return "Не удалось сохранить"


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


def create_post(
    db: Session, student: Student, body: str, is_anonymous: bool
) -> PostCreateResult:
    """Validate, screen and publish a new post by ``student``.

    The author is taken from the session object, never from the request body.
    """
    screening = screen_text(
        db,
        student_id=student.id,
        target_type=TARGET_POST,
        body=body,
        max_length=MAX_POST_LENGTH,
        rate_key=f"post:{student.id}",
        rate_limit=settings.RATE_LIMIT_POSTS,
        rate_window_seconds=settings.RATE_LIMIT_POSTS_WINDOW_SECONDS,
    )
    if not screening.ok:
        return PostCreateResult(
            ok=False, body=body, reason=screening.error, retry_after=screening.retry_after
        )

    post = posts_repo.create_post(
        db,
        author_id=student.id,
        display_alias=display_alias_for(student, is_anonymous),
        is_anonymous=is_anonymous,
        body=screening.clean_text,
    )
    queue_soft_flags(db, TARGET_POST, post.id, screening.verdict)
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
    screening = screen_text(
        db,
        student_id=student.id,
        target_type=TARGET_COMMENT,
        body=body,
        max_length=MAX_COMMENT_LENGTH,
        rate_key=f"comment:{student.id}",
        rate_limit=settings.RATE_LIMIT_COMMENTS,
        rate_window_seconds=settings.RATE_LIMIT_COMMENTS_WINDOW_SECONDS,
    )
    if not screening.ok:
        return CommentCreateResult(
            ok=False, body=body, reason=screening.error, retry_after=screening.retry_after
        )

    comment = comments_repo.create_comment(
        db,
        post_id=post.id,
        author_id=student.id,
        display_alias=display_alias_for(student, is_anonymous),
        is_anonymous=is_anonymous,
        body=screening.clean_text,
    )
    queue_soft_flags(db, TARGET_COMMENT, comment.id, screening.verdict)
    db.commit()
    return CommentCreateResult(ok=True, comment=comment)
