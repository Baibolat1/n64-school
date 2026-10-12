"""Data access for teachers.

Query-only layer, plus the upsert the seeding script uses: like the other
repositories it never commits -- the caller owns the transaction. Nothing here
knows anything about the school itself; teacher records come from a data file
(see ``scripts/seed_teachers.py``), so who is on the list is a matter of data,
not of code.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.teacher import Teacher


def list_active_teachers(db: Session) -> list[Teacher]:
    """Teachers shown to students, in display order."""
    stmt = (
        select(Teacher)
        .where(Teacher.is_active.is_(True))
        .order_by(Teacher.display_name, Teacher.id)
    )
    return list(db.execute(stmt).scalars().all())


def get_teacher(db: Session, teacher_id: int) -> Teacher | None:
    """Return the teacher with ``teacher_id`` regardless of ``is_active``.

    The flag is deliberately not filtered here: the router decides what an
    inactive teacher means for the page at hand.
    """
    return db.get(Teacher, teacher_id)


def upsert_teacher(
    db: Session,
    *,
    display_name: str,
    subject: str | None,
    is_active: bool = True,
) -> tuple[Teacher, bool]:
    """Create or update the teacher named ``display_name``.

    ``display_name`` is the natural key: re-running the seeding script with the
    same file must not duplicate anyone. Returns the teacher and whether it was
    created (False means it was updated in place).
    """
    existing = db.execute(
        select(Teacher).where(Teacher.display_name == display_name)
    ).scalar_one_or_none()

    if existing is None:
        teacher = Teacher(
            display_name=display_name, subject=subject, is_active=is_active
        )
        db.add(teacher)
        db.flush()
        return teacher, True

    existing.subject = subject
    existing.is_active = is_active
    db.flush()
    return existing, False


def deactivate_missing(db: Session, *, keep_names: set[str]) -> list[Teacher]:
    """Switch off every active teacher whose name is not in ``keep_names``.

    Used by the seeding script's ``--prune``: a teacher dropped from the data
    file leaves the site, but their row stays -- reviews point at it, and a
    teacher who comes back should find their history intact. Nothing is ever
    deleted here.
    """
    stmt = select(Teacher).where(
        Teacher.is_active.is_(True), Teacher.display_name.not_in(keep_names)
    )
    teachers = list(db.execute(stmt).scalars().all())
    for teacher in teachers:
        teacher.is_active = False
    db.flush()
    return teachers
