"""Data access for likes -- the ones on posts and the ones on comments.

Same conventions as ``app.repositories.comments``: queries and ``flush()`` only,
``commit()`` belongs to the service layer (``app.services.likes``).

Reads are batched because the feed would otherwise ask the database twice per
card: the counters for a whole page come back in one query, and so does the set
of posts the current student has already liked, which is what the card needs to
draw the heart filled or hollow. Comments are batched the same way, one table
over, because a post page shows a whole column of them.
"""

from collections.abc import Iterable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.like import CommentLike, PostLike


def get_like(db: Session, student_id: int, post_id: int) -> PostLike | None:
    """Return the like ``student_id`` left on ``post_id``, or None."""
    stmt = select(PostLike).where(
        PostLike.student_id == student_id, PostLike.post_id == post_id
    )
    return db.execute(stmt).scalar_one_or_none()


def add_like(db: Session, student_id: int, post_id: int) -> PostLike:
    """Insert a like and flush it.

    The unique constraint on ``(student_id, post_id)`` is the real guard: if a
    second click of the same student reaches this line before the first one is
    committed, the insert raises ``IntegrityError`` and the caller (the toggle
    in the service) treats that as "already liked".
    """
    like = PostLike(student_id=student_id, post_id=post_id)
    db.add(like)
    db.flush()
    return like


def remove_like(db: Session, like: PostLike) -> None:
    """Delete a like row.

    The row is deleted outright rather than flagged: a like holds no content, so
    there is nothing for moderation to keep or restore.
    """
    db.delete(like)
    db.flush()


def count_likes_by_post(
    db: Session, post_ids: Iterable[int]
) -> dict[int, int]:
    """Return ``{post_id: like_count}`` for the given posts in one query.

    Posts without a single like are simply absent from the mapping; callers that
    need a zero use ``.get(post_id, 0)``. An empty input does not query at all.
    """
    wanted = set(post_ids)
    if not wanted:
        return {}
    stmt = (
        select(PostLike.post_id, func.count())
        .where(PostLike.post_id.in_(wanted))
        .group_by(PostLike.post_id)
    )
    return {post_id: count for post_id, count in db.execute(stmt)}


def liked_post_ids(
    db: Session, student_id: int, post_ids: Iterable[int]
) -> set[int]:
    """Return which of ``post_ids`` this student has already liked, in one query.

    Drives the filled/hollow heart on every card of the page; empty input does
    not query at all.
    """
    wanted = set(post_ids)
    if not wanted:
        return set()
    stmt = select(PostLike.post_id).where(
        PostLike.student_id == student_id, PostLike.post_id.in_(wanted)
    )
    return set(db.execute(stmt).scalars().all())


def get_comment_like(
    db: Session, student_id: int, comment_id: int
) -> CommentLike | None:
    """Return the like ``student_id`` left on ``comment_id``, or None."""
    stmt = select(CommentLike).where(
        CommentLike.student_id == student_id,
        CommentLike.comment_id == comment_id,
    )
    return db.execute(stmt).scalar_one_or_none()


def add_comment_like(db: Session, student_id: int, comment_id: int) -> CommentLike:
    """Insert a like on a comment and flush it.

    As above, the unique constraint is the guard: a racing second click raises
    ``IntegrityError`` and the toggle reads that as "already liked".
    """
    like = CommentLike(student_id=student_id, comment_id=comment_id)
    db.add(like)
    db.flush()
    return like


def remove_comment_like(db: Session, like: CommentLike) -> None:
    """Delete a like on a comment outright -- it holds no content to keep."""
    db.delete(like)
    db.flush()


def count_likes_by_comment(
    db: Session, comment_ids: Iterable[int]
) -> dict[int, int]:
    """Return ``{comment_id: like_count}`` for the given comments, in one query.

    Comments without a single like are absent from the mapping; callers that
    need a zero use ``.get(comment_id, 0)``.
    """
    wanted = set(comment_ids)
    if not wanted:
        return {}
    stmt = (
        select(CommentLike.comment_id, func.count())
        .where(CommentLike.comment_id.in_(wanted))
        .group_by(CommentLike.comment_id)
    )
    return {comment_id: count for comment_id, count in db.execute(stmt)}


def liked_comment_ids(
    db: Session, student_id: int, comment_ids: Iterable[int]
) -> set[int]:
    """Which of ``comment_ids`` this student has already liked, in one query."""
    wanted = set(comment_ids)
    if not wanted:
        return set()
    stmt = select(CommentLike.comment_id).where(
        CommentLike.student_id == student_id, CommentLike.comment_id.in_(wanted)
    )
    return set(db.execute(stmt).scalars().all())
