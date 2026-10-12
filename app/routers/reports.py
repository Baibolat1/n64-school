"""Report routes: the complaint form and its submission.

One route pair serves every kind of content (``/report/post/12``,
``/report/comment/34``, ``/report/review/56``): which types exist and how each
one is found is the service's business (``app.services.reports``), so adding a
complainable kind later does not add a route here.

Both routes require a valid student session and a non-banned student, and the
router carries ``require_csrf`` -- the POST is a plain form. The reporter is
taken from the session, never from the form or the URL.

The refusals split three ways, matching how the rest of the app answers:

- no such target, or a hidden one -> **404**, so the endpoint cannot be used to
  probe what a moderator has removed;
- your own content -> **403** (not a silent 404: the student knows it is theirs);
- anything else (unknown reason, too long, already reported) -> the form is
  re-rendered with a message, because the student can fix all three.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.core.deps import require_csrf, require_not_banned
from app.core.templates import templates
from app.db.base import get_db
from app.models.student import Student
from app.services import reports as reports_service

# ``require_csrf`` is attached to the whole router: it is a no-op for safe
# methods, and the one state-changing route here is a form POST.
router = APIRouter(
    prefix="/report", tags=["reports"], dependencies=[Depends(require_csrf)]
)


def _error_message(reason: reports_service.ReportError) -> str:
    """Human-readable refusal message for a refused complaint."""
    if reason is reports_service.ReportError.UNKNOWN_REASON:
        return "Выберите причину из списка"
    if reason is reports_service.ReportError.TOO_LONG:
        return (
            "Слишком длинный комментарий: максимум "
            f"{reports_service.MAX_REPORT_COMMENT_LENGTH} символов"
        )
    if reason is reports_service.ReportError.DUPLICATE:
        return "Вы уже жаловались на это — жалоба у модератора"
    return "Не удалось отправить жалобу"


def _get_target(
    db: Session, student: Student, target_type: str, target_id: int
) -> reports_service.ReportTarget:
    """Resolve what is being complained about, or refuse the request.

    404 for a target that does not exist or is not visible, 403 for the
    student's own content.
    """
    target = reports_service.resolve_report_target(db, target_type, target_id)
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Объект жалобы не найден"
        )
    if target.author_id == student.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="На свой контент жаловаться нельзя",
        )
    return target


def _render_form(
    request: Request,
    student: Student,
    target: reports_service.ReportTarget,
    *,
    reason_code: str = "",
    comment: str = "",
    error: str | None = None,
    status_code: int = status.HTTP_200_OK,
) -> Response:
    """The complaint form, echoing back what was just submitted.

    ``reasons`` is the service's own code -> label mapping: the form iterates it,
    so adding or rewording a reason is a change in one module, not in a template.
    """
    return templates.TemplateResponse(
        request,
        "report.html",
        {
            "student": student,
            "target": target,
            "reasons": reports_service.REASON_LABELS,
            "max_comment_length": reports_service.MAX_REPORT_COMMENT_LENGTH,
            "reason_code": reason_code,
            "comment": comment,
            "error": error,
        },
        status_code=status_code,
    )


@router.get("/{target_type}/{target_id}", response_class=HTMLResponse)
def report_form(
    request: Request,
    target_type: str,
    target_id: int,
    student: Student = Depends(require_not_banned),
    db: Session = Depends(get_db),
) -> Response:
    target = _get_target(db, student, target_type, target_id)
    return _render_form(request, student, target)


@router.post("/{target_type}/{target_id}", response_class=HTMLResponse)
def submit_report(
    request: Request,
    target_type: str,
    target_id: int,
    reason_code: Annotated[str, Form()] = "",
    comment: Annotated[str, Form()] = "",
    student: Student = Depends(require_not_banned),
    db: Session = Depends(get_db),
) -> Response:
    result = reports_service.submit_report(
        db,
        student,
        target_type=target_type,
        target_id=target_id,
        reason_code=reason_code,
        comment=comment,
    )
    if result.ok:
        # A complaint changes nothing the student can see, so there is no page
        # to redirect to that would show it: confirm it right here, with a link
        # back to what was reported. Re-posting the form is harmless -- the
        # second attempt is refused as a duplicate.
        return templates.TemplateResponse(
            request,
            "report_sent.html",
            {
                "student": student,
                "target": result.target,
                "reason_label": reports_service.REASON_LABELS[result.reason_code],
            },
        )

    if result.reason is reports_service.ReportError.UNKNOWN_TARGET:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Объект жалобы не найден"
        )
    if result.reason is reports_service.ReportError.OWN_CONTENT:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="На свой контент жаловаться нельзя",
        )

    # The remaining refusals all leave the target standing, hence the form.
    return _render_form(
        request,
        student,
        result.target,
        reason_code=reason_code.strip(),
        comment=comment,
        error=_error_message(result.reason),
    )
