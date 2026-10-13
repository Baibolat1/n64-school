"""Applications to the platform's development team.

One row per student, not one per application: applying again is not a second
application, it is the same student saying the same thing with a different role
or a corrected handle, so the row is replaced in place (the unique constraint on
``student_id`` is what makes that structural rather than a convention in the
service).

The handle is the whole point of the row -- it is how the team gets in touch --
so it is stored as given, and it is personal data. Unlike a post it is not
published: nobody but an admin ever reads it, and the student wrote it down
knowing that. That is also why it does not go through the PII filter, which
exists to stop contact details from being *published* (see
app.services.publication).
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DevApplication(Base):
    __tablename__ = "dev_applications"

    id: Mapped[int] = mapped_column(primary_key=True)
    # One application per student: a second one replaces the first.
    student_id: Mapped[int] = mapped_column(
        ForeignKey("students.id"), nullable=False, unique=True, index=True
    )
    # One of app.services.team.ROLES. Stored as the code, never as the label --
    # the label is presentation and lives with the other human-readable names.
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    # Telegram handle, normalised to "@name" by the service.
    contact: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
