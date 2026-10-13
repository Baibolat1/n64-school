"""Liking and unliking a post.

One operation only, and it is a toggle rather than two routes: the card shows a
single heart, and a student who has already liked the post is the only one who
could ask to like it again -- so the second click can only mean "take it back".
Recomputing the current state from the session on every request is what makes
that safe: nobody can dictate a target state through the request body.

Transaction policy (mirrors the other service modules): this layer owns commits
-- :func:`toggle_like` commits.

There is deliberately no rate limit and no PII screening here: a like carries no
text at all, so the gates that guard every kind of content
(:mod:`app.services.publication`) have nothing to check. The anti-abuse story is
structural: one row per student per post, unique by constraint.
"""

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.content import Post
from app.models.student import Student
from app.repositories import likes as likes_repo


def toggle_like(db: Session, student: Student, post: Post) -> bool:
    """Like ``post`` for ``student``, or take the like back.

    Returns the state after the call: ``True`` when the post is liked now,
    ``False`` when the like was removed. The identity comes from the session
    object, never from the request body.
    """
    existing = likes_repo.get_like(db, student.id, post.id)
    if existing is not None:
        likes_repo.remove_like(db, existing)
        db.commit()
        return False
    try:
        likes_repo.add_like(db, student.id, post.id)
        db.commit()
    except IntegrityError:
        # Two clicks of the same student raced: the unique constraint rejected
        # the second row, which means the like is already there. Undo the failed
        # transaction and report the state the winner produced.
        db.rollback()
        return True
    return True
