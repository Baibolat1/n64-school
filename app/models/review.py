from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.content import CONTENT_STATUS_VISIBLE


class Review(Base):
    __tablename__ = "reviews"
    __table_args__ = (
        UniqueConstraint(
            "student_id", "teacher_id", name="uq_reviews_student_id_teacher_id"
        ),
        Index("ix_reviews_teacher_id_status", "teacher_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(
        ForeignKey("students.id"), nullable=False
    )
    teacher_id: Mapped[int] = mapped_column(
        ForeignKey("teachers.id"), nullable=False
    )
    # Snapshot of the author's alias at publication time, exactly as on posts and
    # comments: a later alias change does not rewrite what was published.
    display_alias: Mapped[str] = mapped_column(Text, nullable=False)
    is_anonymous: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # The text as written, with any flagged personal-data fragment already
    # replaced by the filter's placeholder -- there is no second copy of it
    # anywhere (the original wording is not kept, see app.services.publication).
    comment: Mapped[str] = mapped_column(Text, nullable=False)
    # Reviews are post-moderated: they are visible right after creation.
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default=CONTENT_STATUS_VISIBLE
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class ReviewScore(Base):
    __tablename__ = "review_scores"
    __table_args__ = (
        UniqueConstraint(
            "review_id", "criterion_id", name="uq_review_scores_review_id_criterion_id"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    review_id: Mapped[int] = mapped_column(
        ForeignKey("reviews.id", ondelete="CASCADE"), nullable=False
    )
    criterion_id: Mapped[int] = mapped_column(
        ForeignKey("criteria.id"), nullable=False
    )
    score: Mapped[int] = mapped_column(Integer, nullable=False)
