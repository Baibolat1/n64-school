"""Data access for complaints about published content.

Same conventions as the other repositories: queries and ``flush()`` only,
``commit()`` belongs to :mod:`app.services.reports`.

The uniqueness of a complaint -- one student, one target, forever -- is enforced
by the database (``uq_reports_reporter_id_target_type_target_id``), not by
:func:`get_report`: the query is there to give the student a readable refusal,
the constraint is what actually makes a second complaint impossible.

Reading the queue of open complaints belongs to the moderation screen (stage
12), so nothing here filters on ``status`` yet.
"""

from sqlalchemy import select
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
