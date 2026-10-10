"""Student authentication routes: invite-code login, logout and the /me page."""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.core.deps import current_student, require_not_banned
from app.core.templates import templates
from app.db.base import get_db
from app.models.student import Student
from app.services import aliases as aliases_service
from app.services import invites as invites_service
from app.services import student_auth

router = APIRouter(tags=["auth"])

# One message for every failure cause (unknown, expired, used) so the form
# cannot be used to enumerate valid codes.
LOGIN_ERROR = "Код приглашения недействителен"


def _set_session_cookie(response: Response, raw_token: str) -> None:
    response.set_cookie(
        key=settings.STUDENT_COOKIE_NAME,
        value=raw_token,
        httponly=True,
        samesite="lax",
        secure=settings.COOKIE_SECURE,
        path="/",
        max_age=settings.STUDENT_SESSION_TTL_DAYS * 86400,
    )


@router.get("/", response_class=HTMLResponse)
def index(student: Student | None = Depends(current_student)) -> RedirectResponse:
    """Send a logged-in student to /feed, everyone else to the login form."""
    url = "/feed" if student is not None else "/login"
    return RedirectResponse(url=url, status_code=status.HTTP_303_SEE_OTHER)


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request) -> Response:
    return templates.TemplateResponse(
        request, "login.html", {"error": None, "code": ""}
    )


@router.post("/login", response_class=HTMLResponse)
def login_submit(
    request: Request,
    code: Annotated[str, Form()] = "",
    db: Session = Depends(get_db),
) -> Response:
    student = invites_service.redeem_invite(db, code)
    if student is None:
        # Identical status and message for every failure reason.
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": LOGIN_ERROR, "code": ""},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    response = RedirectResponse(url="/me", status_code=status.HTTP_303_SEE_OTHER)
    _set_session_cookie(response, student_auth.issue_student_session(student))
    return response


@router.post("/logout")
def logout_submit() -> RedirectResponse:
    response = RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(settings.STUDENT_COOKIE_NAME, path="/")
    return response


def _alias_error_message(result: aliases_service.AliasChangeResult) -> str:
    """Human-readable refusal message for a failed alias change."""
    reason = result.reason
    if reason is aliases_service.AliasError.TAKEN:
        return "Псевдоним занят"
    if reason is aliases_service.AliasError.TOO_SHORT:
        return "Слишком короткий: минимум 3 символа"
    if reason is aliases_service.AliasError.TOO_LONG:
        return "Слишком длинный: максимум 24 символа"
    if reason is aliases_service.AliasError.LEADING_DIGIT:
        return "Псевдоним не может начинаться с цифры"
    if reason is aliases_service.AliasError.INVALID_CHARS:
        return "Недопустимые символы: буквы, цифры, пробел, дефис и подчёркивание"
    if reason in (
        aliases_service.AliasError.RESERVED,
        aliases_service.AliasError.FORBIDDEN,
    ):
        return "Такое имя использовать нельзя"
    if reason is aliases_service.AliasError.COOLDOWN:
        days = settings.ALIAS_CHANGE_COOLDOWN_DAYS
        when = result.retry_at.strftime("%d.%m.%Y") if result.retry_at else "позже"
        return f"Псевдоним можно менять раз в {days} дней. Следующая попытка — {when}"
    return "Не удалось изменить псевдоним"


def _render_me(
    request: Request,
    student: Student,
    *,
    error: str | None = None,
    alias_value: str | None = None,
) -> Response:
    can_change, retry_at = aliases_service.can_change_alias(student)
    return templates.TemplateResponse(
        request,
        "me.html",
        {
            "student": student,
            "can_change": can_change,
            "retry_at": retry_at,
            "cooldown_days": settings.ALIAS_CHANGE_COOLDOWN_DAYS,
            "error": error,
            # Echo the submitted text back on error so it need not be retyped.
            "alias_value": (
                alias_value if alias_value is not None else student.alias
            ),
        },
    )


@router.get("/me", response_class=HTMLResponse)
def me(
    request: Request,
    student: Student = Depends(require_not_banned),
) -> Response:
    return _render_me(request, student)


@router.post("/me/alias", response_class=HTMLResponse)
def change_alias(
    request: Request,
    new_alias: Annotated[str, Form()] = "",
    student: Student = Depends(require_not_banned),
    db: Session = Depends(get_db),
) -> Response:
    result = aliases_service.set_alias(db, student, new_alias)
    if result.ok:
        # Post/Redirect/Get: a reload will not resubmit the form.
        return RedirectResponse(url="/me", status_code=status.HTTP_303_SEE_OTHER)
    return _render_me(
        request,
        student,
        error=_alias_error_message(result),
        alias_value=result.alias,
    )
