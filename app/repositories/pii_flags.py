"""Data access for the moderation queue of soft PII hits (``pii_flags``).

Same conventions as the other repositories: this layer adds rows and flushes
when it needs an id, but never commits -- ``app.services.feed`` owns the
transaction, so a published post and the flags that describe it land together
or not at all.

Only the truncated snippet from the filter ever reaches this table, never the
matched text in full, so the queue cannot become a second copy of the message.
"""

from sqlalchemy.orm import Session

from app.models.audit import PiiFlag


def create_flag(
    db: Session,
    *,
    target_type: str,
    target_id: int,
    rule_code: str,
    snippet: str,
) -> PiiFlag:
    """Queue one soft PII hit for a moderator (status starts as ``open``)."""
    flag = PiiFlag(
        target_type=target_type,
        target_id=target_id,
        rule_code=rule_code,
        snippet=snippet,
        status="open",
    )
    db.add(flag)
    db.flush()
    return flag
