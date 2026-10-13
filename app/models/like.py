"""Likes: one row per student per post.

A like is a many-to-many edge between students and posts, so it gets a table of
its own rather than a counter column on ``posts``: the unique constraint makes a
double like impossible at the database level, and a counter can always be
recomputed from the rows. There are deliberately no likes on comments and no
reposts -- only posts are liked.

Unlike posts and comments there is no ``status`` here: unliking deletes the row
outright, because a like carries no content to moderate. Complaints about posts
and the account ban remain the ways to act on what somebody liked.
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
