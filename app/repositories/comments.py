"""Data access for comments.

Same conventions as ``app.repositories.posts``: queries and ``flush()`` only,
``commit()`` belongs to the service layer, and the status filter is baked into
the queries so hidden comments never leak into a listing.
"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.content import CONTENT_STATUS_VISIBLE, Comment


def create_comment(
    db: Session,
    *,
    post_id: int,
    author_id: int,
    display_alias: str,
    is_anonymous: bool,
    body: str,
) -> Comment:
    comment = Comment(
        post_id=post_id,
        author_id=author_id,
        display_alias=display_alias,
        is_anonymous=is_anonymous,
        body=body,
        status=CONTENT_STATUS_VISIBLE,
    )
    db.add(comment)
    db.flush()
    return comment


def get_comment(db: Session, comment_id: int) -> Comment | None:
    """Return the comment with ``comment_id`` regardless of status, or None.

    Status is deliberately not filtered here: the caller decides what a
    non-visible comment means (the reports service refuses it like a
    non-existent one, so the endpoint cannot be used to probe what a moderator
    has hidden).
    """
    return db.get(Comment, comment_id)


def list_visible_comments(db: Session, post_id: int) -> list[Comment]:
    """Visible comments of a post, oldest first (natural reading order)."""
    stmt = (
        select(Comment)
        .where(
            Comment.post_id == post_id,
            Comment.status == CONTENT_STATUS_VISIBLE,
        )
        .order_by(Comment.created_at.asc(), Comment.id.asc())
    )
    return list(db.execute(stmt).scalars().all())


def count_visible_comments(db: Session, post_id: int) -> int:
    stmt = (
        select(func.count())
        .select_from(Comment)
        .where(
            Comment.post_id == post_id,
            Comment.status == CONTENT_STATUS_VISIBLE,
        )
    )
    return db.execute(stmt).scalar_one()
