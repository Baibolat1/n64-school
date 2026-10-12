"""Data access for complaints about published content.

Same conventions as the other repositories: queries and ``flush()`` only,
``commit()`` belongs to :mod:`app.services.reports`.

The uniqueness of a complaint -- one student, one target, forever -- is enforced
by the database (``uq_reports_reporter_id_target_type_target_id``), not by
:func:`get_report`: the query is there to give the student a readable refusal,
the constraint is what actually makes a second complaint impossible.

Reading the queue of open complaints lives here too, so the moderation screen
(stage 12) does not have to spell the ``open`` filter out per query. Every such
query keeps the filter *inside* it, so a closed complaint never appears in a
queue or its counts.
"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.report import REPORT_STATUS_OPEN, Report


def get_report(
    db: Session, *, reporter_id: int, target_type: str, target_id: int
) -> Report | None:
    """This student's complaint about this exact target, if they already filed one."""
    stmt = select(Report).where(
        Report.reporter_id == reporter_id,
        Report.target_type == target_type,
        Report.target_id == target_id,
    )
    return db.execute(stmt).scalar_one_or_none()


def create_report(
    db: Session,
    *,
    reporter_id: int,
    target_type: str,
    target_id: int,
    reason_code: str,
    comment: str | None,
) -> Report:
    report = Report(
        reporter_id=reporter_id,
        target_type=target_type,
        target_id=target_id,
        reason_code=reason_code,
        comment=comment,
        status=REPORT_STATUS_OPEN,
    )
    db.add(report)
    db.flush()
    return report


def list_open(db: Session, limit: int, offset: int) -> list[Report]:
    """Open complaints, newest first, paginated."""
    stmt = (
        select(Report)
        .where(Report.status == REPORT_STATUS_OPEN)
        .order_by(Report.created_at.desc(), Report.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(db.execute(stmt).scalars().all())


def count_open(db: Session) -> int:
    stmt = (
        select(func.count())
        .select_from(Report)
        .where(Report.status == REPORT_STATUS_OPEN)
    )
    return db.execute(stmt).scalar_one()


def list_open_for_target(
    db: Session, target_type: str, target_id: int
) -> list[Report]:
    """Open complaints about one exact target, newest first."""
    stmt = (
        select(Report)
        .where(
            Report.target_type == target_type,
            Report.target_id == target_id,
            Report.status == REPORT_STATUS_OPEN,
        )
        .order_by(Report.created_at.desc(), Report.id.desc())
    )
    return list(db.execute(stmt).scalars().all())


def open_report_counts(db: Session) -> dict[tuple[str, int], int]:
    """How many open complaints each target carries, as ``{(type, id): count}``.

    Loaded in one query so the queue can show a count per row without issuing a
    query per row.
    """
    stmt = (
        select(Report.target_type, Report.target_id, func.count())
        .where(Report.status == REPORT_STATUS_OPEN)
        .group_by(Report.target_type, Report.target_id)
    )
    return {
        (target_type, target_id): count
        for target_type, target_id, count in db.execute(stmt)
    }


def close(db: Session, report: Report, *, status: str, admin_id: int) -> None:
    """Close a complaint as resolved or dismissed by ``admin_id``.

    ``resolved_at`` is set by the database (``func.now()``) rather than from
    Python, keeping the timeframe consistent with ``created_at``.
    """
    report.status = status
    report.resolved_by = admin_id
    report.resolved_at = func.now()
    db.flush()
