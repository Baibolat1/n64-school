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


def find_by_post_and_body(
    db: Session, post_id: int, body: str
) -> Comment | None:
    """Return the comment of ``post_id`` whose text is ``body``, or None.

    Only the demo seeder asks this, for the same reason as
    ``app.repositories.posts.get_post_by_body``: it recognises the rows it wrote
    by their text. Status is not filtered -- a hidden demo comment must not be
    inserted a second time.
    """
    stmt = select(Comment).where(Comment.post_id == post_id, Comment.body == body)
    return db.execute(stmt).scalars().first()


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


def set_status(db: Session, comment: Comment, status: str) -> None:
    """Set a comment's lifecycle status (the moderation write path).

    The ``visible`` filter lives inside ``list_visible_comments`` /
    ``count_visible_comments``, so setting anything else hides the comment from
    its post at once.
    """
    comment.status = status
    db.flush()
