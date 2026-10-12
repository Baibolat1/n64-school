"""Data access for the moderation queue of soft PII hits (``pii_flags``).

Same conventions as the other repositories: this layer adds rows and flushes
when it needs an id, but never commits -- ``app.services.feed`` owns the
transaction, so a published post and the flags that describe it land together
or not at all.

Only the truncated snippet from the filter ever reaches this table, never the
matched text in full, so the queue cannot become a second copy of the message.
"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.audit import PII_FLAG_STATUS_OPEN, PiiFlag


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
        status=PII_FLAG_STATUS_OPEN,
    )
    db.add(flag)
    db.flush()
    return flag


def list_open(db: Session, limit: int, offset: int) -> list[PiiFlag]:
    """Open soft-PII flags, newest first, paginated."""
    stmt = (
        select(PiiFlag)
        .where(PiiFlag.status == PII_FLAG_STATUS_OPEN)
        .order_by(PiiFlag.created_at.desc(), PiiFlag.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(db.execute(stmt).scalars().all())


def count_open(db: Session) -> int:
    stmt = (
        select(func.count())
        .select_from(PiiFlag)
        .where(PiiFlag.status == PII_FLAG_STATUS_OPEN)
    )
    return db.execute(stmt).scalar_one()


def list_open_for_target(
    db: Session, target_type: str, target_id: int
) -> list[PiiFlag]:
    """Open flags about one exact target, newest first."""
    stmt = (
        select(PiiFlag)
        .where(
            PiiFlag.target_type == target_type,
            PiiFlag.target_id == target_id,
            PiiFlag.status == PII_FLAG_STATUS_OPEN,
        )
        .order_by(PiiFlag.created_at.desc(), PiiFlag.id.desc())
    )
    return list(db.execute(stmt).scalars().all())


def open_flag_counts(db: Session) -> dict[tuple[str, int], int]:
    """How many open flags each target carries, as ``{(type, id): count}``.

    Loaded in one query so the queue can show a count per row without issuing a
    query per row.
    """
    stmt = (
        select(PiiFlag.target_type, PiiFlag.target_id, func.count())
        .where(PiiFlag.status == PII_FLAG_STATUS_OPEN)
        .group_by(PiiFlag.target_type, PiiFlag.target_id)
    )
    return {
        (target_type, target_id): count
        for target_type, target_id, count in db.execute(stmt)
    }


def close(db: Session, flag: PiiFlag, *, status: str) -> None:
    """Close a flag with ``status`` (resolved or dismissed).

    There is deliberately no "closed by" column on this table: whoever closed
    the flag is recorded as a row in ``moderation_actions`` (``resolve_pii_flag``
    / ``dismiss_pii_flag``), so the trail stays in one place and this table keeps
    holding only the filter's own hit. ``resolved_at`` is set by the database
    (``func.now()``), consistent with ``created_at``.
    """
    flag.status = status
    flag.resolved_at = func.now()
    db.flush()
