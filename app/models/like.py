"""Likes: one row per student per thing liked -- a post or a comment.

A like is a many-to-many edge between students and content, so each kind gets a
table of its own rather than a counter column on ``posts``/``comments``: the
unique constraint makes a double like impossible at the database level, and a
counter can always be recomputed from the rows. Two tables rather than one
polymorphic ``target_type``/``target_id`` pair, because the foreign keys are
then real foreign keys and the database rejects a like pointing at a comment
that does not exist.

Reposts are still not a thing: only posts and comments are liked.

Unlike posts and comments there is no ``status`` here: unliking deletes the row
outright, because a like carries no content to moderate. Complaints about the
content and the account ban remain the ways to act on what somebody liked.
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class PostLike(Base):
    __tablename__ = "post_likes"
    __table_args__ = (
        # One like per student per post. Enforced by the database, not only by
        # the toggle's read-then-write: two clicks racing each other would
        # otherwise both miss the existing row and insert a second one.
        UniqueConstraint("student_id", "post_id", name="uq_post_likes_student_post"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(
        ForeignKey("students.id"), nullable=False, index=True
    )
    post_id: Mapped[int] = mapped_column(
        ForeignKey("posts.id"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )


class CommentLike(Base):
    __tablename__ = "comment_likes"
    __table_args__ = (
        # The same rule as above, one table over. A comment is the second kind
        # of content a classmate can approve of with one tap, and it keeps the
        # same guarantee: the pair is unique in the schema, not in the handler.
        UniqueConstraint(
            "student_id", "comment_id", name="uq_comment_likes_student_comment"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(
        ForeignKey("students.id"), nullable=False, index=True
    )
    comment_id: Mapped[int] = mapped_column(
        ForeignKey("comments.id"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
