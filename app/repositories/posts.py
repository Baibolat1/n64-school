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


def get_post_by_body(db: Session, body: str) -> Post | None:
    """Return a post whose text is exactly ``body``, or None.

    Only the demo seeder asks this: it has no key of its own to match on, so it
    recognises the posts it wrote by their text (the demo texts are unique). It
    looks at every status, so re-seeding after a post was hidden does not insert
    a double.
    """
    stmt = select(Post).where(Post.body == body)
    return db.execute(stmt).scalars().first()


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


def set_status(db: Session, post: Post, status: str) -> None:
    """Set a post's lifecycle status (the moderation write path).

    The ``visible`` filter lives inside ``list_visible_posts`` /
    ``count_visible_posts``, so setting anything else hides the post from the
    feed and its count at once.
    """
    post.status = status
    db.flush()
