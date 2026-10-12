"""Teacher reviews: submitting and editing a student's review of a teacher.

A student has at most one review per teacher (``uq_reviews_student_id_teacher_id``),
so this is a single operation that either creates the review or replaces the one
already there. Editing keeps the row's status untouched: a review a moderator hid
must not come back to life just because its author opened the form again.

The text goes through the same gates as everything else published on the site --
shape, rate limit, PII filter -- because they live in
:mod:`app.services.publication`, not here. Scores are checked before the limiter
runs: a fumbled radio button must not cost budget, exactly as an empty post does
not.

Transaction policy (as in ``app.services.feed``): this layer owns the commit.
"""

from dataclasses import dataclass
from enum import Enum

from sqlalchemy.orm import Session

from app.config import settings
from app.models.audit import TARGET_REVIEW
from app.models.review import Review
from app.models.student import Student
from app.models.teacher import Criterion, Teacher
from app.repositories import reviews as reviews_repo
from app.services.publication import display_alias_for, queue_soft_flags, screen_text

# Review text length limit. Like the feed's, a generous placeholder until the
# product-level limits are settled in stage 13.
MAX_REVIEW_LENGTH = 3000


class ReviewError(str, Enum):
    """Machine-readable reasons a review can be refused."""

    EMPTY = "empty"
    TOO_LONG = "too_long"
    RATE_LIMITED = "rate_limited"
    PII_BLOCKED = "pii_blocked"
    SCORE_REQUIRED = "score_required"
    SCORE_OUT_OF_RANGE = "score_out_of_range"
    NO_CRITERIA = "no_criteria"


@dataclass(frozen=True)
class ReviewSubmitResult:
    """Outcome of a submit attempt.

    ``comment`` and ``is_anonymous`` always hold what was submitted, so the form
    can be re-rendered with the author's own words still in it.
    """

    ok: bool
    review: Review | None = None
    comment: str = ""
    is_anonymous: bool = False
    reason: ReviewError | None = None
    retry_after: int = 0


def _score_error(
    criteria: list[Criterion], scores: dict[int, int]
) -> ReviewError | None:
    """Check the scores against the criteria the form is built from.

    Every active criterion must be answered with a value on its own scale; a
    missing answer and a value outside the scale are different refusals, because
    they mean different mistakes on the student's side.
    """
    if not criteria:
        return ReviewError.NO_CRITERIA

    for criterion in criteria:
        if criterion.id not in scores:
            return ReviewError.SCORE_REQUIRED
        if not criterion.scale_min <= scores[criterion.id] <= criterion.scale_max:
            return ReviewError.SCORE_OUT_OF_RANGE
    return None


def submit_review(
    db: Session,
    student: Student,
    teacher: Teacher,
    *,
    comment: str,
    scores: dict[int, int],
    criteria: list[Criterion],
    is_anonymous: bool,
) -> ReviewSubmitResult:
    """Create or replace ``student``'s review of ``teacher``.

    The author comes from the session object and the teacher from the resolved
    route parameter; ``criteria`` is the same list the form was rendered from, so
    a score can only ever be attached to a criterion the student was shown.
    """
    if not comment.strip():
        return ReviewSubmitResult(
            ok=False,
            comment=comment,
            is_anonymous=is_anonymous,
            reason=ReviewError.EMPTY,
        )
    if len(comment) > MAX_REVIEW_LENGTH:
        return ReviewSubmitResult(
            ok=False,
            comment=comment,
            is_anonymous=is_anonymous,
            reason=ReviewError.TOO_LONG,
        )

    score_error = _score_error(criteria, scores)
    if score_error is not None:
        return ReviewSubmitResult(
            ok=False, comment=comment, is_anonymous=is_anonymous, reason=score_error
        )

    screening = screen_text(
        db,
        student_id=student.id,
        target_type=TARGET_REVIEW,
        body=comment,
        max_length=MAX_REVIEW_LENGTH,
        rate_key=f"review:{student.id}",
        rate_limit=settings.RATE_LIMIT_REVIEWS,
        rate_window_seconds=settings.RATE_LIMIT_REVIEWS_WINDOW_SECONDS,
    )
    if not screening.ok:
        return ReviewSubmitResult(
            ok=False,
            comment=comment,
            is_anonymous=is_anonymous,
            reason=ReviewError(screening.error.value),
            retry_after=screening.retry_after,
        )

    alias = display_alias_for(student, is_anonymous)
    existing = reviews_repo.get_review(db, student.id, teacher.id)
    if existing is None:
        review = reviews_repo.create_review(
            db,
            student_id=student.id,
            teacher_id=teacher.id,
            display_alias=alias,
            is_anonymous=is_anonymous,
            comment=screening.clean_text,
        )
    else:
        review = existing
        review.display_alias = alias
        review.is_anonymous = is_anonymous
        review.comment = screening.clean_text
        # status is deliberately left as it is -- see the module docstring.
        db.flush()

    reviews_repo.replace_scores(db, review.id, scores)
    queue_soft_flags(db, TARGET_REVIEW, review.id, screening.verdict)
    db.commit()
    return ReviewSubmitResult(ok=True, review=review)
