"""Data access for the moderation trail (``moderation_actions``).

Same conventions as the other repositories: queries, ``add()`` and ``flush()``,
but never ``commit()`` -- ``app.services.moderation`` owns the transaction, so an
action and the change it describes land together or not at all.

Every write here is an append: a recorded action is never edited or removed, so
the trail stays a faithful account of what a moderator did and when.
"""

from dataclasses import dataclass

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.models.admin import Admin
from app.models.audit import (
    TARGET_COMMENT,
    TARGET_POST,
    TARGET_REVIEW,
    TARGET_STUDENT,
    ModerationAction,
)
from app.models.content import Comment, Post
from app.models.review import Review


@dataclass(frozen=True)
class AuditEntry:
    """One recorded action together with the username of the admin who took it."""

    action: ModerationAction
    admin_username: str


def record_action(
    db: Session,
    *,
    admin_id: int,
    action: str,
    target_type: str,
    target_id: int,
    note: str | None = None,
) -> ModerationAction:
    """Append one action to the trail and return the written row."""
    entry = ModerationAction(
        admin_id=admin_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        note=note,
    )
    db.add(entry)
    db.flush()
    return entry


def list_recent(db: Session, limit: int, offset: int) -> list[AuditEntry]:
    """The trail, newest first, each action paired with its admin's username.

    ``id`` breaks ties for actions recorded within the same second (the
    resolution of SQLite's ``CURRENT_TIMESTAMP``).
    """
    stmt = (
        select(ModerationAction, Admin.username)
        .join(Admin, Admin.id == ModerationAction.admin_id)
        .order_by(ModerationAction.created_at.desc(), ModerationAction.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return [
        AuditEntry(action=action, admin_username=username)
        for action, username in db.execute(stmt)
    ]


def list_for_target(
    db: Session, target_type: str, target_id: int, limit: int, offset: int
) -> list[AuditEntry]:
    """The trail for one target, newest first, each action paired with its admin.

    The same join and ordering as :func:`list_recent`, narrowed to a single
    object so a screen can show what was done to it.
    """
    stmt = (
        select(ModerationAction, Admin.username)
        .join(Admin, Admin.id == ModerationAction.admin_id)
        .where(
            ModerationAction.target_type == target_type,
            ModerationAction.target_id == target_id,
        )
        .order_by(ModerationAction.created_at.desc(), ModerationAction.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return [
        AuditEntry(action=action, admin_username=username)
        for action, username in db.execute(stmt)
    ]


def list_for_student(
    db: Session, student_id: int, limit: int, offset: int
) -> list[AuditEntry]:
    """The trail about one student: actions on them and on the content they wrote.

    A trail row names its target by ``(type, id)`` and never the author, so the
    actions that concern a student are split across four cases: the ones aimed
    at the student row itself (``ban`` / ``unban``) and the ones aimed at a post,
    comment or review the student authored. A student's card wants all of them in
    one newest-first list, so the three author columns are folded in here rather
    than on the screen. Ordering matches :func:`list_for_target`.
    """
    posts = select(Post.id).where(Post.author_id == student_id)
    comments = select(Comment.id).where(Comment.author_id == student_id)
    reviews = select(Review.id).where(Review.student_id == student_id)
    about_student = or_(
        and_(
            ModerationAction.target_type == TARGET_STUDENT,
            ModerationAction.target_id == student_id,
        ),
        and_(
            ModerationAction.target_type == TARGET_POST,
            ModerationAction.target_id.in_(posts),
        ),
        and_(
            ModerationAction.target_type == TARGET_COMMENT,
            ModerationAction.target_id.in_(comments),
        ),
        and_(
            ModerationAction.target_type == TARGET_REVIEW,
            ModerationAction.target_id.in_(reviews),
        ),
    )
    stmt = (
        select(ModerationAction, Admin.username)
        .join(Admin, Admin.id == ModerationAction.admin_id)
        .where(about_student)
        .order_by(ModerationAction.created_at.desc(), ModerationAction.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return [
        AuditEntry(action=action, admin_username=username)
        for action, username in db.execute(stmt)
    ]


def count_all(db: Session) -> int:
    stmt = select(func.count()).select_from(ModerationAction)
    return db.execute(stmt).scalar_one()