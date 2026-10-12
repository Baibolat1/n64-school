"""Data access for review criteria.

Criteria describe what a review is scored on; which ones exist, what they are
called and what scale each uses is data (``data/*.json``, seeded by
``scripts/seed_teachers.py``), not code. Nothing here interprets the labels.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.teacher import Criterion


def list_active_criteria(db: Session) -> list[Criterion]:
    """Criteria a new review is scored on, in display order."""
    stmt = (
        select(Criterion)
        .where(Criterion.is_active.is_(True))
        .order_by(Criterion.position, Criterion.id)
    )
    return list(db.execute(stmt).scalars().all())


def upsert_criterion(
    db: Session,
    *,
    code: str,
    label: str,
    scale_min: int,
    scale_max: int,
    position: int = 0,
    is_active: bool = True,
) -> tuple[Criterion, bool]:
    """Create or update the criterion with ``code``.

    ``code`` is the natural key, so re-running the seeding script updates labels
    and scales in place instead of adding a second copy of the same question.
    Returns the criterion and whether it was created.
    """
    existing = db.execute(
        select(Criterion).where(Criterion.code == code)
    ).scalar_one_or_none()

    if existing is None:
        criterion = Criterion(
            code=code,
            label=label,
            scale_min=scale_min,
            scale_max=scale_max,
            position=position,
            is_active=is_active,
        )
        db.add(criterion)
        db.flush()
        return criterion, True

    existing.label = label
    existing.scale_min = scale_min
    existing.scale_max = scale_max
    existing.position = position
    existing.is_active = is_active
    db.flush()
    return existing, False
