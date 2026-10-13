"""Data access for students: the moderation ban switch, avatar reads and seeding.

Same conventions as the other repositories: queries and ``flush()`` only --
``commit()`` belongs to the service layer. Moderation writes only the ban switch
and its reason; nothing else about a student is touched there. The picture a
student chose is read here too (as one batch, for rendering post cards), since
it lives on the same row. ``upsert_student`` exists for the demo seeder
(``scripts/seed_demo.py``), which is the only caller that creates students
outside registration.
"""

from collections.abc import Iterable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.student import Student


def get_student(db: Session, student_id: int) -> Student | None:
    """Return the student with ``student_id`` regardless of ban state, or None."""
    return db.get(Student, student_id)


def get_student_by_alias(db: Session, alias: str) -> Student | None:
    """Return the student called ``alias``, or None.

    Aliases are unique, so this is a lookup by natural key rather than a search.
    The demo seeder needs it to point a post or an award at a student it has just
    created without carrying ids around.
    """
    stmt = select(Student).where(Student.alias == alias)
    return db.execute(stmt).scalar_one_or_none()


def upsert_student(
    db: Session,
    *,
    alias: str,
    class_name: str | None = None,
    avatar_key: str | None = None,
    email: str | None = None,
    password_hash: str | None = None,
) -> tuple[Student, bool]:
    """Create or update the student named ``alias`` (the demo seeder's write).

    ``alias`` is the natural key, so re-running ``scripts/seed_demo.py`` updates
    a profile instead of creating a twin. Fields left as ``None`` are not
    written on an existing row: the seeder must not wipe a picture or a password
    that somebody set by hand on the live site. A brand-new row gets whatever it
    is given, ``None`` included.
    """
    existing = db.execute(
        select(Student).where(Student.alias == alias)
    ).scalar_one_or_none()

    if existing is None:
        student = Student(
            alias=alias,
            class_name=class_name,
            avatar_key=avatar_key,
            email=email,
            password_hash=password_hash,
        )
        db.add(student)
        db.flush()
        return student, True

    if class_name is not None:
        existing.class_name = class_name
    if avatar_key is not None:
        existing.avatar_key = avatar_key
    if email is not None:
        existing.email = email
    if password_hash is not None:
        existing.password_hash = password_hash
    db.flush()
    return existing, False


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


def aliases_by_id(db: Session, ids: Iterable[int]) -> dict[int, str]:
    """Return ``{student_id: alias}`` for the given ids in one query.

    The esports page lists badge holders by pseudonym, which is a batch read like
    the avatars: one query per page, not one per row. The alias here is the
    student's *current* one, which is right for an award (unlike a post, an
    award is not a snapshot of what the student was called that day).
    """
    wanted = set(ids)
    if not wanted:
        return {}
    stmt = select(Student.id, Student.alias).where(Student.id.in_(wanted))
    return {student_id: alias for student_id, alias in db.execute(stmt)}
