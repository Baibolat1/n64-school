from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# Time convention: every timestamp is DateTime(timezone=True) with a UTC default.
# We use a server-side default (func.now() -> CURRENT_TIMESTAMP, UTC on SQLite)
# so the database fills created_at even for rows inserted outside the ORM.
ALIAS_STRATEGIES = ("constant", "per_post")


class Student(Base):
    __tablename__ = "students"

    id: Mapped[int] = mapped_column(primary_key=True)
    alias: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    alias_strategy: Mapped[str] = mapped_column(
        Text, nullable=False, default="constant"
    )
    # When the student last changed their alias by hand. NULL means the alias has
    # never been changed manually (the first change is always allowed); the
    # change cooldown is measured from this timestamp.
    alias_changed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    is_banned: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, index=True
    )
    banned_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    invite_id: Mapped[int | None] = mapped_column(
        ForeignKey("invites.id"), nullable=True
    )
    # Credentials for students who registered themselves (first
    # ``OPEN_REGISTRATION_LIMIT`` by email+password, later by QR confirmation).
    # Students created from an admin invite code have neither and sign in with
    # the code alone. ``email`` is stored lower-cased so uniqueness is
    # case-insensitive; SQLite allows many NULLs in a unique index, so the
    # invite-only students do not collide.
    email: Mapped[str | None] = mapped_column(
        Text, unique=True, index=True, nullable=True
    )
    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # NULL means the student is drawn as a coloured circle with an initial;
    # otherwise the value is a key of an image from the built-in gallery (there
    # are no file uploads, see app.core.avatars).
    avatar_key: Mapped[str | None] = mapped_column(Text, nullable=True)
