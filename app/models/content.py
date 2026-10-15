from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# Moderation lifecycle shared by posts, comments and reviews. Kept as a code
# constant (no DB CHECK constraint) so new states can be added without a
# migration. "pending" is only used for content held back by the PII filter;
# reviews are visible immediately (post-moderation).
CONTENT_STATUS_VISIBLE = "visible"
# Set by a moderator in stage 12; used here so callers (and tests) do not have
# to spell the string out.
CONTENT_STATUS_HIDDEN = "hidden"
CONTENT_STATUS_DELETED = "deleted"
CONTENT_STATUSES = (
    CONTENT_STATUS_VISIBLE,
    "pending",
    CONTENT_STATUS_HIDDEN,
    CONTENT_STATUS_DELETED,
)


class Post(Base):
    __tablename__ = "posts"

    id: Mapped[int] = mapped_column(primary_key=True)
    author_id: Mapped[int] = mapped_column(
        ForeignKey("students.id"), nullable=False, index=True
    )
    # Snapshot of the author's alias at publication time.
    display_alias: Mapped[str] = mapped_column(Text, nullable=False)
    is_anonymous: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default=CONTENT_STATUS_VISIBLE, index=True
    )
    # Pinned to the top of the feed and marked with a badge. A property of the
    # post itself rather than a join table: there is one pin per post, and it
    # lives and dies with the row. Only the demo seeder sets it today -- there
    # is no moderator switch for it yet (see app.repositories.posts).
    is_featured: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )


class Comment(Base):
    __tablename__ = "comments"
    __table_args__ = (
        Index("ix_comments_post_id_created_at", "post_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id"), nullable=False)
    author_id: Mapped[int] = mapped_column(
        ForeignKey("students.id"), nullable=False, index=True
    )
    # Snapshot of the author's alias at publication time.
    display_alias: Mapped[str] = mapped_column(Text, nullable=False)
    is_anonymous: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default=CONTENT_STATUS_VISIBLE
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
