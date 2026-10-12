"""Admin routes: authentication and the moderation screens."""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.core.deps import client_ip, require_admin, require_csrf
from app.core.templates import templates
from app.db.base import get_db
from app.models.admin import Admin
from app.repositories import pii_blocked as pii_blocked_repo
from app.repositories import pii_flags as pii_flags_repo
from app.repositories import reports as reports_repo
from app.repositories import students as students_repo
from app.schemas.admin import AdminLogin, AdminOut
from app.services import admin_auth
from app.services import invites as invites_service
from app.services import login_throttle

router = APIRouter(
    prefix="/admin", tags=["admin"], dependencies=[Depends(require_csrf)]
)

LOGIN_ERROR = "Неверный логин или пароль"
THROTTLE_ERROR = "Слишком много попыток входа. Попробуйте позже."


def _set_session_cookie(response: Response, raw_token: str) -> None:
    response.set_cookie(
        key=settings.SESSION_COOKIE_NAME,
        value=raw_token,
        httponly=True,
        samesite="lax",
        secure=settings.COOKIE_SECURE,
        path="/",
        max_age=settings.ADMIN_SESSION_TTL_HOURS * 3600,
    )


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request) -> Response:
    return templates.TemplateResponse(
        request, "admin/login.html", {"error": None, "username": ""}
    )


@router.post("/login", response_class=HTMLResponse)
def login_submit(
    request: Request,
    form: Annotated[AdminLogin, Form()],
    db: Session = Depends(get_db),
) -> Response:
    ip = client_ip(request)
    retry_after = login_throttle.check_admin(ip)
    if retry_after is not None:
        return templates.TemplateResponse(
            request,
            "admin/login.html",
            {"error": THROTTLE_ERROR, "username": form.username},
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            headers={"Retry-After": str(retry_after)},
        )

    raw_token = admin_auth.login(db, form.username, form.password)
    if raw_token is None:
        # Same response for "no such user" and "wrong password".
        return templates.TemplateResponse(
            request,
            "admin/login.html",
            {"error": LOGIN_ERROR, "username": form.username},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    login_throttle.clear_admin(ip)
    response = RedirectResponse(url="/admin", status_code=status.HTTP_303_SEE_OTHER)
    _set_session_cookie(response, raw_token)
    return response


@router.post("/logout")
def logout_submit(
    request: Request,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    admin_auth.logout(db, request.cookies.get(settings.SESSION_COOKIE_NAME, ""))
    response = RedirectResponse(
        url="/admin/login", status_code=status.HTTP_303_SEE_OTHER
    )
    response.delete_cookie(settings.SESSION_COOKIE_NAME, path="/")
    return response


@router.get("", response_class=HTMLResponse)
def dashboard(
    request: Request,
    admin: Admin = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Response:
    """Moderation overview: one counter per queue, each linking into it."""
    return templates.TemplateResponse(
        request,
        "admin/dashboard.html",
        {
            "admin": AdminOut.model_validate(admin),
            "open_reports": reports_repo.count_open(db),
            "open_pii_flags": pii_flags_repo.count_open(db),
            "pii_blocked_attempts": pii_blocked_repo.count_all(db),
            "banned_students": students_repo.count_banned(db),
        },
    )


@router.get("/invites", response_class=HTMLResponse)
def invites_page(
    request: Request,
    admin: Admin = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Response:
    return templates.TemplateResponse(
        request,
        "admin/invites.html",
        {
            "admin": AdminOut.model_validate(admin),
            "invites": invites_service.list_invites(db),
            "default_ttl_days": settings.INVITE_TTL_DAYS,
        },
    )


@router.post("/invites", response_class=HTMLResponse)
def invites_create(
    request: Request,
    admin: Admin = Depends(require_admin),
    ttl_days: Annotated[int, Form()] = settings.INVITE_TTL_DAYS,
    db: Session = Depends(get_db),
) -> Response:
    raw_code = invites_service.create_invite(db, admin, ttl_days)
    return templates.TemplateResponse(
        request,
        "admin/invite_created.html",
        {"raw_code": raw_code, "ttl_days": ttl_days},
    )
