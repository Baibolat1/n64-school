"""Data access for students: the moderation ban switch and avatar reads.

Same conventions as the other repositories: queries and ``flush()`` only --
``commit()`` belongs to the service layer. Moderation writes only the ban switch
and its reason; nothing else about a student is touched there. The picture a
student chose is read here too (as one batch, for rendering post cards), since
it lives on the same row.
"""

from collections.abc import Iterable

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


def avatar_keys_by_id(db: Session, ids: Iterable[int]) -> dict[int, str | None]:
    """Return ``{student_id: avatar_key}`` for the given ids in one query.

    The author's picture is drawn on every post and comment card, so fetching a
    student one at a time would be an N+1; the batch keeps it to a single query.
    Keys that are not in the gallery are not filtered out here -- that decision
    belongs to the presentation layer (``app.core.avatars``).
    """
    wanted = set(ids)
    if not wanted:
        # A list with no authors to resolve must not issue an empty query.
        return {}
    stmt = select(Student.id, Student.avatar_key).where(Student.id.in_(wanted))
    return {student_id: key for student_id, key in db.execute(stmt)}
