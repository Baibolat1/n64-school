"""Data access for hard-PII refusals (``pii_blocked_attempts``).

Same conventions as the other repositories: this layer adds rows and flushes
when it needs an id, but never commits -- the caller owns the transaction.

The table stores the fact of a refusal and nothing else: who tried, when, what
they were publishing and which hard rule fired. No fragment of the message is
written here, not even truncated, so the audit trail never holds the content
the filter was built to keep out.
"""

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.audit import PiiBlockedAttempt
from app.models.student import Student


@dataclass(frozen=True)
class BlockedEntry:
    """One refusal together with the alias of the student who tried to publish."""

    attempt: PiiBlockedAttempt
    student_alias: str


def record_attempt(
    db: Session,
    *,
    student_id: int,
    target_type: str,
    rule_codes: tuple[str, ...],
) -> PiiBlockedAttempt:
    """Record that ``student_id`` had a ``target_type`` refused by ``rule_codes``."""
    attempt = PiiBlockedAttempt(
        student_id=student_id,
        target_type=target_type,
        rule_codes=",".join(rule_codes),
    )
    db.add(attempt)
    db.flush()
    return attempt


def list_recent(db: Session, limit: int, offset: int) -> list[BlockedEntry]:
    """Refusals, newest first, each with the student's current alias.

    The alias is read from ``students.alias`` rather than from a snapshot: this
    table records the author as ``student_id``, so there is no per-row copy of
    the name to show. ``id`` breaks ties within the same second.
    """
    stmt = (
        select(PiiBlockedAttempt, Student.alias)
        .join(Student, Student.id == PiiBlockedAttempt.student_id)
        .order_by(
            PiiBlockedAttempt.created_at.desc(), PiiBlockedAttempt.id.desc()
        )
        .limit(limit)
        .offset(offset)
    )
    return [
        BlockedEntry(attempt=attempt, student_alias=alias)
        for attempt, alias in db.execute(stmt)
    ]


def count_all(db: Session) -> int:
    stmt = select(func.count()).select_from(PiiBlockedAttempt)
    return db.execute(stmt).scalar_one()
