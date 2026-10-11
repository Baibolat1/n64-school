from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# Lifecycle of a registration QR token. ``pending`` waits for an existing
# student to scan it; ``confirmed`` means one has; ``used`` means the novice has
# set a password and the account now exists; ``expired`` is set lazily once
# ``expires_at`` has passed (there is no background job -- see the service).
QR_STATUS_PENDING = "pending"
QR_STATUS_CONFIRMED = "confirmed"
QR_STATUS_USED = "used"
QR_STATUS_EXPIRED = "expired"


class RegistrationQrToken(Base):
    """A QR code a novice shows to an existing student to be let in.

    The plaintext token is only ever encoded into the QR image; the database
    keeps a keyed hash, exactly as invite codes do, so a database leak does not
    let anyone confirm themselves.
    """

    __tablename__ = "registration_qr_tokens"
    __table_args__ = (
        Index("ix_registration_qr_tokens_expires_at", "expires_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # The address the novice is registering with, captured before they have an
    # account. Lower-cased by the service.
    email: Mapped[str] = mapped_column(Text, nullable=False)
    token_hash: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    # The existing student who scanned and confirmed the code.
    confirmed_by: Mapped[int | None] = mapped_column(
        ForeignKey("students.id"), nullable=True
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default=QR_STATUS_PENDING
    )
