"""Data access for hard-PII refusals (``pii_blocked_attempts``).

Same conventions as the other repositories: this layer adds rows and flushes
when it needs an id, but never commits -- the caller owns the transaction.

The table stores the fact of a refusal and nothing else: who tried, when, what
they were publishing and which hard rule fired. No fragment of the message is
written here, not even truncated, so the audit trail never holds the content
the filter was built to keep out.
"""

from sqlalchemy.orm import Session

from app.models.audit import PiiBlockedAttempt


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
