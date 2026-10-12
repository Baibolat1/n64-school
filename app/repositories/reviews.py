"""Data access for teacher reviews and their scores.

Like the other repositories this layer never commits -- ``app.services.reviews``
owns the transaction, so a review and its scores land together or not at all.

Every listing and every average filters on ``status == visible`` *inside* the
query, so hiding a review (the moderation stage) removes it from the pages and
from the statistics at once, without any caller having to remember to filter.
That is the point of computing averages here rather than in the template: a
hidden review must not weigh on a teacher's score.
"""

from dataclasses import dataclass

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models.content import CONTENT_STATUS_VISIBLE
from app.models.review import Review, ReviewScore
from app.models.teacher import Criterion


@dataclass(frozen=True)
class CriterionStat:
    """One criterion's average across a teacher's visible reviews."""

    criterion: Criterion
    average: float | None
    count: int


@dataclass(frozen=True)
class OverallStat:
    """A teacher's overall average and how many visible reviews it covers.

    ``average`` is the mean of every individual score (not the mean of the
    per-criterion averages), so a criterion with more scores does not get
    outvoted by one with fewer. Both are None/0 when there is nothing to show.
    """

    average: float | None
    review_count: int


def get_review(db: Session, student_id: int, teacher_id: int) -> Review | None:
    """The student's own review of this teacher, whatever its status.

    Only one such review can exist (``uq_reviews_student_id_teacher_id``), so
    editing means updating this row rather than adding another.
    """
    stmt = select(Review).where(
        Review.student_id == student_id, Review.teacher_id == teacher_id
    )
    return db.execute(stmt).scalar_one_or_none()


def get_review_by_id(db: Session, review_id: int) -> Review | None:
    """Return the review with ``review_id`` regardless of status, or None.

    Status is deliberately not filtered here; the caller decides what to do
    with a hidden review (the reports service treats it as non-existent).
    """
    return db.get(Review, review_id)


def create_review(
    db: Session,
    *,
    student_id: int,
    teacher_id: int,
    display_alias: str,
    is_anonymous: bool,
    comment: str,
) -> Review:
    review = Review(
        student_id=student_id,
        teacher_id=teacher_id,
        display_alias=display_alias,
        is_anonymous=is_anonymous,
        comment=comment,
        status=CONTENT_STATUS_VISIBLE,
    )
    db.add(review)
    db.flush()
    return review


def set_status(db: Session, review: Review, status: str) -> None:
    """Set a review's lifecycle status (the moderation write path).

    The ``visible`` filter lives inside ``list_visible_reviews`` /
    ``count_visible_reviews`` *and* inside the average queries, so setting
    anything else removes the review from its teacher's page and from the
    statistics at once.
    """
    review.status = status
    db.flush()


def replace_scores(db: Session, review_id: int, scores: dict[int, int]) -> None:
    """Set the review's scores to exactly ``scores`` (criterion id -> score).

    The previous set is deleted first, so an edit cannot leave a stale score
    behind for a criterion the student no longer answers.
    """
    db.execute(delete(ReviewScore).where(ReviewScore.review_id == review_id))
    for criterion_id, score in scores.items():
        db.add(
            ReviewScore(review_id=review_id, criterion_id=criterion_id, score=score)
        )
    db.flush()


def list_visible_reviews(
    db: Session, teacher_id: int, limit: int, offset: int
) -> list[Review]:
    """Visible reviews of a teacher, newest first, paginated."""
    stmt = (
        select(Review)
        .where(
            Review.teacher_id == teacher_id,
            Review.status == CONTENT_STATUS_VISIBLE,
        )
        .order_by(Review.created_at.desc(), Review.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(db.execute(stmt).scalars().all())


def count_visible_reviews(db: Session, teacher_id: int) -> int:
    stmt = (
        select(func.count())
        .select_from(Review)
        .where(
            Review.teacher_id == teacher_id,
            Review.status == CONTENT_STATUS_VISIBLE,
        )
    )
    return db.execute(stmt).scalar_one()


def scores_by_review(
    db: Session, review_ids: list[int]
) -> dict[int, dict[int, int]]:
    """Scores of the given reviews as ``{review_id: {criterion_id: score}}``."""
    if not review_ids:
        return {}
    stmt = select(
        ReviewScore.review_id, ReviewScore.criterion_id, ReviewScore.score
    ).where(ReviewScore.review_id.in_(review_ids))

    result: dict[int, dict[int, int]] = {review_id: {} for review_id in review_ids}
    for review_id, criterion_id, score in db.execute(stmt):
        result[review_id][criterion_id] = score
    return result


def criterion_stats(db: Session, teacher_id: int) -> list[CriterionStat]:
    """Average and number of scores per criterion, for the visible reviews."""
    stmt = (
        select(Criterion, func.avg(ReviewScore.score), func.count(ReviewScore.id))
        .select_from(ReviewScore)
        .join(Review, Review.id == ReviewScore.review_id)
        .join(Criterion, Criterion.id == ReviewScore.criterion_id)
        .where(
            Review.teacher_id == teacher_id,
            Review.status == CONTENT_STATUS_VISIBLE,
        )
        .group_by(Criterion.id)
        .order_by(Criterion.position, Criterion.id)
    )
    return [
        CriterionStat(criterion=criterion, average=average, count=count)
        for criterion, average, count in db.execute(stmt)
    ]


def overall_stats(db: Session, teacher_id: int) -> OverallStat:
    """Overall average score and the number of visible reviews behind it."""
    stmt = (
        select(func.avg(ReviewScore.score), func.count(func.distinct(Review.id)))
        .select_from(ReviewScore)
        .join(Review, Review.id == ReviewScore.review_id)
        .where(
            Review.teacher_id == teacher_id,
            Review.status == CONTENT_STATUS_VISIBLE,
        )
    )
    average, count = db.execute(stmt).one()
    return OverallStat(average=average, review_count=count or 0)


def overall_stats_by_teacher(db: Session) -> dict[int, OverallStat]:
    """The same figures for every teacher at once, for the list page."""
    stmt = (
        select(
            Review.teacher_id,
            func.avg(ReviewScore.score),
            func.count(func.distinct(Review.id)),
        )
        .select_from(ReviewScore)
        .join(Review, Review.id == ReviewScore.review_id)
        .where(Review.status == CONTENT_STATUS_VISIBLE)
        .group_by(Review.teacher_id)
    )
    return {
        teacher_id: OverallStat(average=average, review_count=count or 0)
        for teacher_id, average, count in db.execute(stmt)
    }
