"""Complaints about published content.

A complaint is the *only* moderation trigger in this project: profanity is
allowed, nothing is screened out in advance, and the mod queue is built from
what students report (see ``docs/PLAN.md``). That makes the complaint endpoint
the one place where a student can put someone else's content in front of a
moderator, so the rules here are about what a complaint may *not* be:

- **A reason from the closed list.** ``reason_code`` must be one of
  :data:`app.models.report.REPORT_REASON_CODES`; anything else is refused, so a
  typo cannot create a complaint nobody can classify.
- **Not about your own content** -- that would only be a way to queue your own
  post for review.
- **Not a second time.** One student, one target, forever: the check here gives
  the readable refusal, the database's unique constraint is what guarantees it.
- **Only about what exists and is visible.** A hidden post, a comment on a
  hidden post, a hidden review -- all refused exactly like one that never
  existed, so the endpoint cannot be used to probe what a moderator removed.

Nothing here is a "report button in a template": the reason list lives in this
module (:data:`REASON_LABELS`), the form iterates it, and the target is
resolved from the URL against the repositories -- never taken from the form.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Callable

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.audit import TARGET_COMMENT, TARGET_POST, TARGET_REVIEW
from app.models.content import CONTENT_STATUS_VISIBLE
from app.models.report import Report
from app.models.student import Student
from app.repositories import comments as comments_repo
from app.repositories import posts as posts_repo
from app.repositories import reports as reports_repo
from app.repositories import reviews as reviews_repo

# How long a complaint's free-text comment may be. A complaint is an explanation,
# not a second post.
MAX_REPORT_COMMENT_LENGTH = 1000

# The reasons a complaint can carry, in the order they are offered -- the form
# iterates this mapping directly, so the wording of the moderation vocabulary
# lives here and nowhere else. The keys must be exactly the codes declared on
# the model (``REPORT_REASON_CODES``); ``tests/test_reports.py`` fails if the two
# drift apart.
REASON_LABELS: dict[str, str] = {
    "spam": "Спам или реклама",
    "pii": "Персональные данные",
    "abuse": "Оскорбления или травля",
    "offtopic": "Не по теме",
    "other": "Другое",
}


class ReportError(str, Enum):
    """Machine-readable reasons a complaint can be refused."""

    UNKNOWN_TARGET = "unknown_target"
    OWN_CONTENT = "own_content"
    UNKNOWN_REASON = "unknown_reason"
    TOO_LONG = "too_long"
    DUPLICATE = "duplicate"


@dataclass(frozen=True)
class ReportTarget:
    """A complaint's subject: what it is, whose it is, and where it lives.

    ``back_url`` is the page the student is returned to, so the router never has
    to know how a comment or a review is addressed.
    """

    target_type: str
    target_id: int
    author_id: int
    back_url: str


@dataclass(frozen=True)
class ReportResult:
    """Outcome of a complaint.

    ``target`` is filled whenever it could be resolved, even on a refusal, so
    the router can re-render the form or the confirmation page from it. For
    :attr:`ReportError.UNKNOWN_TARGET` it stays None.
    """

    ok: bool
    report: Report | None = None
    reason: ReportError | None = None
    target: ReportTarget | None = None
    reason_code: str = ""


def _resolve_post(db: Session, target_id: int) -> ReportTarget | None:
    post = posts_repo.get_post(db, target_id)
    if post is None or post.status != CONTENT_STATUS_VISIBLE:
        return None
    return ReportTarget(TARGET_POST, post.id, post.author_id, f"/feed/posts/{post.id}")


def _resolve_comment(db: Session, target_id: int) -> ReportTarget | None:
    comment = comments_repo.get_comment(db, target_id)
    if comment is None or comment.status != CONTENT_STATUS_VISIBLE:
        return None
    # A visible comment under a hidden post is unreachable content: the feed
    # 404s the post, so there is no page a moderator could be sent to.
    post = posts_repo.get_post(db, comment.post_id)
    if post is None or post.status != CONTENT_STATUS_VISIBLE:
        return None
    return ReportTarget(
        TARGET_COMMENT, comment.id, comment.author_id, f"/feed/posts/{post.id}"
    )


def _resolve_review(db: Session, target_id: int) -> ReportTarget | None:
    review = reviews_repo.get_review_by_id(db, target_id)
    if review is None or review.status != CONTENT_STATUS_VISIBLE:
        return None
    return ReportTarget(
        TARGET_REVIEW, review.id, review.student_id, f"/teachers/{review.teacher_id}"
    )


# Which kinds of content can be complained about, and how each one is found.
# Keyed by the same codes the moderation trail uses (``app.models.audit``), so a
# complaint's target_type means the same thing everywhere. This map is filtered
# to visible content on purpose; moderation has its own reader without that
# filter -- ``resolve_moderation_target`` in ``app.services.moderation``. When a
# new kind of content is added, both maps must be extended.
_TARGET_RESOLVERS: dict[str, Callable[[Session, int], ReportTarget | None]] = {
    TARGET_POST: _resolve_post,
    TARGET_COMMENT: _resolve_comment,
    TARGET_REVIEW: _resolve_review,
}


def resolve_report_target(
    db: Session, target_type: str, target_id: int
) -> ReportTarget | None:
    """Find what a complaint would be about, or None if there is no such thing.

    A target type nobody can be complained about (or a hidden/absent target)
    answers None, and the caller turns that into the same 404.
    """
    resolver = _TARGET_RESOLVERS.get(target_type)
    if resolver is None:
        return None
    return resolver(db, target_id)


def submit_report(
    db: Session,
    student: Student,
    *,
    target_type: str,
    target_id: int,
    reason_code: str,
    comment: str,
) -> ReportResult:
    """Record ``student``'s complaint, or say why it is refused.

    The reporter comes from the session and the target from the URL, never from
    the form; the form only carries the reason and the explanation.
    """
    target = resolve_report_target(db, target_type, target_id)
    if target is None:
        return ReportResult(ok=False, reason=ReportError.UNKNOWN_TARGET)

    if target.author_id == student.id:
        return ReportResult(ok=False, reason=ReportError.OWN_CONTENT, target=target)

    reason_code = reason_code.strip()
    if reason_code not in REASON_LABELS:
        return ReportResult(
            ok=False, reason=ReportError.UNKNOWN_REASON, target=target
        )

    comment = comment.strip()
    if len(comment) > MAX_REPORT_COMMENT_LENGTH:
        return ReportResult(ok=False, reason=ReportError.TOO_LONG, target=target)

    already = reports_repo.get_report(
        db,
        reporter_id=student.id,
        target_type=target.target_type,
        target_id=target.target_id,
    )
    if already is not None:
        return ReportResult(ok=False, reason=ReportError.DUPLICATE, target=target)

    try:
        report = reports_repo.create_report(
            db,
            reporter_id=student.id,
            target_type=target.target_type,
            target_id=target.target_id,
            reason_code=reason_code,
            comment=comment or None,
        )
        db.commit()
    except IntegrityError:
        # Lost a race with the same student's other tab: the unique constraint
        # is the authority, the check above is only the friendly path.
        db.rollback()
        return ReportResult(ok=False, reason=ReportError.DUPLICATE, target=target)

    return ReportResult(
        ok=True, report=report, target=target, reason_code=reason_code
    )
