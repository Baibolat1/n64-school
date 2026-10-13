"""Student authentication routes: invite-code login, logout and the /me page."""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.core.deps import (
    client_ip,
    current_student,
    require_csrf,
    require_not_banned,
)
from app.core.templates import templates
from app.db.base import get_db
from app.models.student import Student
from app.repositories import tournaments as tournaments_repo
from app.services import aliases as aliases_service
from app.services import avatars as avatars_service
from app.services import esports as esports_service
from app.services import invites as invites_service
from app.services import login_throttle
from app.services import registration
from app.services import student_auth

# ``require_csrf`` covers the login form too, so a third-party page cannot sign
# a visitor in with a code of its choosing (or trigger a logout).
router = APIRouter(tags=["auth"], dependencies=[Depends(require_csrf)])

# One message for every failure cause (unknown, expired, used) so the form
# cannot be used to enumerate valid codes.
LOGIN_ERROR = "Код приглашения недействителен"
THROTTLE_ERROR = "Слишком много попыток входа. Попробуйте позже."


def _set_session_cookie(response: Response, raw_token: str) -> None:
    response.set_cookie(
        key=settings.STUDENT_COOKIE_NAME,
        value=raw_token,
        httponly=True,
        samesite="lax",
        secure=settings.COOKIE_SECURE,
        path="/",
        max_age=settings.STUDENT_SESSION_COOKIE_DAYS * 86400,
    )


@router.get("/", response_class=HTMLResponse)
def index(student: Student | None = Depends(current_student)) -> RedirectResponse:
    """Send a logged-in student to /feed, everyone else to the login form."""
    url = "/feed" if student is not None else "/login"
    return RedirectResponse(url=url, status_code=status.HTTP_303_SEE_OTHER)


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request) -> Response:
    return templates.TemplateResponse(
        request, "login.html", {"error": None, "code": "", "email": ""}
    )


@router.post("/login", response_class=HTMLResponse)
def login_submit(
    request: Request,
    code: Annotated[str, Form()] = "",
    db: Session = Depends(get_db),
) -> Response:
    ip = client_ip(request)
    retry_after = login_throttle.check_student(ip)
    if retry_after is not None:
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": THROTTLE_ERROR, "code": "", "email": ""},
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            headers={"Retry-After": str(retry_after)},
        )

    student = invites_service.redeem_invite(db, code)
    if student is None:
        # Identical status and message for every failure reason.
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": LOGIN_ERROR, "code": "", "email": ""},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    login_throttle.clear_student(ip)
    response = RedirectResponse(url="/me", status_code=status.HTTP_303_SEE_OTHER)
    _set_session_cookie(response, student_auth.issue_student_session(student))
    return response


@router.post("/login/email", response_class=HTMLResponse)
def login_email(
    request: Request,
    email: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
    db: Session = Depends(get_db),
) -> Response:
    """Sign in a self-registered student by email and password.

    Works for every student who has a password -- both the first ones admitted
    while registration was open and later QR-confirmed ones -- regardless of the
    registration limit. Invite-code students have no password and keep using the
    code form above.
    """
    ip = client_ip(request)
    retry_after = login_throttle.check_register(ip)
    if retry_after is not None:
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": THROTTLE_ERROR, "code": "", "email": email},
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            headers={"Retry-After": str(retry_after)},
        )

    student = student_auth.login_with_password(
        db, registration.normalize_email(email), password
    )
    if student is None:
        # One message for unknown email, no password and wrong password alike.
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "Неверный email или пароль", "code": "", "email": email},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    login_throttle.clear_register(ip)
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
    if reason is aliases_service.AliasError.RESERVED:
        return "Такое имя использовать нельзя"
    if reason is aliases_service.AliasError.COOLDOWN:
        days = settings.ALIAS_CHANGE_COOLDOWN_DAYS
        when = result.retry_at.strftime("%d.%m.%Y") if result.retry_at else "позже"
        return f"Псевдоним можно менять раз в {days} дней. Следующая попытка — {when}"
    return "Не удалось изменить псевдоним"


def _avatar_error_message(result: avatars_service.AvatarChangeResult) -> str:
    """Human-readable refusal message for a failed avatar change."""
    if result.reason is avatars_service.AvatarError.INVALID_KEY:
        return "Такой картинки нет в галерее"
    return "Не удалось изменить аватарку"


def _render_me(
    request: Request,
    student: Student,
    db: Session,
    *,
    error: str | None = None,
    alias_value: str | None = None,
) -> Response:
    can_change, retry_at = aliases_service.can_change_alias(student)
    # The profile shows the esports awards the student has won: two batched
    # reads (their badges, then the tournaments those badges point at), not one
    # query per badge.
    badges = tournaments_repo.list_badges_for_student(db, student.id)
    badge_tournaments = tournaments_repo.tournaments_by_id(
        db, [badge.tournament_id for badge in badges]
    )
    return templates.TemplateResponse(
        request,
        "me.html",
        {
            "student": student,
            "can_change": can_change,
            "retry_at": retry_at,
            "cooldown_days": settings.ALIAS_CHANGE_COOLDOWN_DAYS,
            "error": error,
            "badges": badges,
            "badge_tournaments": badge_tournaments,
            "badge_label": esports_service.badge_label,
            "game_label": esports_service.game_label,
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
    db: Session = Depends(get_db),
) -> Response:
    return _render_me(request, student, db)


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
        db,
        error=_alias_error_message(result),
        alias_value=result.alias,
    )


@router.post("/me/avatar", response_class=HTMLResponse)
def change_avatar(
    request: Request,
    avatar_key: Annotated[str, Form()] = "",
    student: Student = Depends(require_not_banned),
    db: Session = Depends(get_db),
) -> Response:
    result = avatars_service.set_avatar(db, student, avatar_key)
    if result.ok:
        # Post/Redirect/Get: a reload will not resubmit the form.
        return RedirectResponse(url="/me", status_code=status.HTTP_303_SEE_OTHER)
    return _render_me(request, student, db, error=_avatar_error_message(result))
