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


def _newest_first(limit: int, offset: int, *, featured: bool | None):
    """Visible posts, newest first, with ``id`` as the tie-breaker.

    The tie-breaker keeps posts created within the same second (the resolution
    of SQLite's ``CURRENT_TIMESTAMP``) in a stable order. ``featured`` narrows
    the listing to the pinned posts (True), to the stream without them (False)
    or leaves it whole (None).
    """
    stmt = select(Post).where(Post.status == CONTENT_STATUS_VISIBLE)
    if featured is not None:
        stmt = stmt.where(Post.is_featured.is_(featured))
    return stmt.order_by(Post.created_at.desc(), Post.id.desc()).limit(limit).offset(offset)


def list_visible_posts(db: Session, limit: int, offset: int) -> list[Post]:
    """Visible posts of any kind, newest first, paginated."""
    return list(db.execute(_newest_first(limit, offset, featured=None)).scalars().all())


def list_stream_posts(db: Session, limit: int, offset: int) -> list[Post]:
    """The feed's stream: visible posts that are *not* pinned, newest first.

    Pinned posts are shown at the top of the first page instead, so they must
    not be counted into the stream as well (see :func:`list_feed_page`).
    """
    return list(db.execute(_newest_first(limit, offset, featured=False)).scalars().all())


def count_featured_posts(db: Session) -> int:
    stmt = (
        select(func.count())
        .select_from(Post)
        .where(Post.status == CONTENT_STATUS_VISIBLE, Post.is_featured.is_(True))
    )
    return db.execute(stmt).scalar_one()


def list_feed_page(db: Session, limit: int, offset: int) -> list[Post]:
    """One page of the feed: the pinned posts first, then the stream.

    A pinned post belongs to the top of the feed, so it is shown exactly once --
    on the first page. Every later page skips pinned posts and shifts ``offset``
    by how many of them the first page took, or the pinned post would come round
    again once the stream reached its age. ``count_visible_posts`` keeps counting
    it, so the number of pages does not change with a pin.

    Only a page's worth of pinned posts is ever taken: more of them would push
    the stream off the first page entirely.
    """
    if offset == 0:
        featured = list(db.execute(
            _newest_first(limit, 0, featured=True)
        ).scalars().all())
        stream_offset = 0
    else:
        featured = []
        stream_offset = offset - count_featured_posts(db)
    # The first page took ``len(featured)`` posts out of ``limit``; the stream
    # fills whatever is left of it.
    stream_limit = limit - len(featured)
    if stream_limit <= 0:
        return featured
    stream = list_stream_posts(db, limit=stream_limit, offset=max(0, stream_offset))
    return featured + stream


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


def set_featured(db: Session, post: Post, featured: bool) -> None:
    """Pin a post to the top of the feed, or unpin it.

    Same rule as everywhere else in this module: the write is a ``flush()``, the
    caller commits. Hidden posts stay out of the pinned block without any extra
    bookkeeping -- the ``visible`` filter is inside the queries, so hiding a
    pinned post unpins it for the reader and keeps it on the post itself.
    """
    post.is_featured = featured
    db.flush()
