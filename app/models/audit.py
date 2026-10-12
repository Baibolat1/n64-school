from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

MODERATION_ACTIONS = (
    "hide",
    "restore",
    "delete",
    "ban",
    "unban",
    "dismiss_report",
    "resolve_report",
)
PII_FLAG_STATUSES = ("open", "resolved", "dismissed")

# What a moderation action or a PII flag points at. ``target_id`` is the primary
# key in the table named here.
TARGET_POST = "post"
TARGET_COMMENT = "comment"
TARGET_REVIEW = "review"
TARGET_STUDENT = "student"


class ModerationAction(Base):
    __tablename__ = "moderation_actions"
    __table_args__ = (
        Index(
            "ix_moderation_actions_target_type_target_id", "target_type", "target_id"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    admin_id: Mapped[int] = mapped_column(
        ForeignKey("admins.id"), nullable=False
    )
    action: Mapped[str] = mapped_column(Text, nullable=False)
    target_type: Mapped[str] = mapped_column(Text, nullable=False)
    target_id: Mapped[int] = mapped_column(Integer, nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PiiFlag(Base):
    __tablename__ = "pii_flags"
    __table_args__ = (
        Index("ix_pii_flags_status_created_at", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    target_type: Mapped[str] = mapped_column(Text, nullable=False)
    target_id: Mapped[int] = mapped_column(Integer, nullable=False)
    rule_code: Mapped[str] = mapped_column(Text, nullable=False)
    # Truncated fragment only, never the full text.
    snippet: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="open")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class PiiBlockedAttempt(Base):
    """A publish attempt refused outright by a hard PII rule.

    The row records the *fact* of the refusal only -- who, when and which rule
    fired. The text is deliberately not stored anywhere: a blocked message is
    never persisted, not even for a moderator, so this table cannot become a
    copy of exactly the content the filter exists to keep out.

    The author is recorded even for an anonymous attempt, because this is the
    moderation trail rather than public content; nothing here is ever shown to
    students.
    """

    __tablename__ = "pii_blocked_attempts"
    __table_args__ = (
        Index("ix_pii_blocked_attempts_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id"), nullable=False)
    # "post" or "comment" -- what the student was trying to publish.
    target_type: Mapped[str] = mapped_column(Text, nullable=False)
    # Comma-separated codes of the HARD rules that fired, sorted; the reason for
    # the refusal. Never contains any text from the message.
    rule_codes: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
