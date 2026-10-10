"""Data access for posts.

Query-only layer (plus ``flush()`` where an id is needed in the same
transaction): it never commits -- ``app.services.feed`` owns ``commit()``.
The status filter lives *inside* the queries, so hiding a post (status set to
anything other than ``visible``) automatically removes it from every listing
without any caller having to remember to filter.
"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.content import CONTENT_STATUS_VISIBLE, Post


def create_post(
    db: Session,
    *,
    author_id: int,
    display_alias: str,
    is_anonymous: bool,
    body: str,
) -> Post:
    post = Post(
        author_id=author_id,
        display_alias=display_alias,
        is_anonymous=is_anonymous,
        body=body,
        status=CONTENT_STATUS_VISIBLE,
    )
    db.add(post)
    db.flush()
    return post


def get_post(db: Session, post_id: int) -> Post | None:
    """Return the post with ``post_id`` regardless of status, or None.

    Status is deliberately *not* filtered here: the caller decides what to do
    with a non-visible post (the feed router turns it into a 404).
    """
    return db.get(Post, post_id)


def list_visible_posts(db: Session, limit: int, offset: int) -> list[Post]:
    """Visible posts, newest first, paginated.

    ``id`` is a tie-breaker so that posts created within the same second (the
    resolution of SQLite's ``CURRENT_TIMESTAMP``) keep a stable newest-first
    order.
    """
    stmt = (
        select(Post)
        .where(Post.status == CONTENT_STATUS_VISIBLE)
        .order_by(Post.created_at.desc(), Post.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(db.execute(stmt).scalars().all())


def count_visible_posts(db: Session) -> int:
    stmt = (
        select(func.count())
        .select_from(Post)
        .where(Post.status == CONTENT_STATUS_VISIBLE)
    )
    return db.execute(stmt).scalar_one()
