"""Data access for students, as far as moderation is concerned.

Same conventions as the other repositories: queries and ``flush()`` only --
``commit()`` belongs to the service layer. Only the ban switch and its reason
are written here; nothing else about a student is touched by moderation.
"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.student import Student


def get_student(db: Session, student_id: int) -> Student | None:
    """Return the student with ``student_id`` regardless of ban state, or None."""
    return db.get(Student, student_id)


def set_banned(
    db: Session,
    student: Student,
    *,
    banned: bool,
    reason: str | None = None,
) -> None:
    """Set the student's ban flag; lifting a ban clears its reason.

    The reason only means something while the ban stands, so unbanning drops it
    rather than leaving a stale explanation behind.
    """
    student.is_banned = banned
    student.banned_reason = reason if banned else None
    db.flush()


def count_banned(db: Session) -> int:
    stmt = (
        select(func.count())
        .select_from(Student)
        .where(Student.is_banned.is_(True))
    )
    return db.execute(stmt).scalar_one()
