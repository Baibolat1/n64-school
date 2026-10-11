"""FastAPI dependencies for admin and student authentication."""

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.config import settings
from app.core import csrf
from app.core.csrf import CSRFError
from app.db.base import get_db
from app.models.admin import Admin
from app.models.student import Student
from app.services.admin_auth import resolve_session
from app.services.student_auth import resolve_student_session

# Methods that do not change state and so need no CSRF token.
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})


class StudentBlockedError(Exception):
    """Raised for a banned student; rendered as the 403 "blocked" page."""


def client_ip(request: Request) -> str:
    """Best-effort client address, for use *only* as a rate-limit key.

    Behind a reverse proxy this is the proxy's address unless the proxy is
    configured to pass one on; the value is never persisted or logged, so the
    worst case is a coarser counter, not a privacy leak.
    """
    return request.client.host if request.client is not None else "unknown"


async def require_csrf(request: Request) -> None:
    """Reject an unsafe request whose CSRF token is missing or wrong.

    Attached at router level, so it also runs for safe methods; those return
    immediately. The form body is parsed here, and Starlette caches the parsed
    form on the request, so the endpoint's own ``Form`` parameters reuse it
    instead of reading the stream twice.

    Only form-encoded bodies can carry the token, which is fine while every
    state-changing route is an HTML form. A JSON endpoint added later must not
    reuse this dependency as-is.
    """
    if request.method in _SAFE_METHODS:
        return

    form = await request.form()
    raw_submitted = form.get(settings.CSRF_FORM_FIELD)
    submitted = raw_submitted if isinstance(raw_submitted, str) else ""
    cookie = request.cookies.get(settings.CSRF_COOKIE_NAME, "")

    if not (csrf.is_valid_token(submitted) and csrf.tokens_match(submitted, cookie)):
        raise CSRFError()


def current_admin(
    request: Request,
    db: Session = Depends(get_db),
) -> Admin | None:
    """Resolve the admin from the session cookie, or None. Never raises."""
    raw_token = request.cookies.get(settings.SESSION_COOKIE_NAME)
    if not raw_token:
        return None
    return resolve_session(db, raw_token)


def require_admin(admin: Admin | None = Depends(current_admin)) -> Admin:
    """Dependency for protected HTML pages: redirect anonymous users to login."""
    if admin is None:
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/admin/login"},
        )
    return admin


def current_student(
    request: Request,
    db: Session = Depends(get_db),
) -> Student | None:
    """Resolve the student from the signed cookie, or None. Never raises."""
    token = request.cookies.get(settings.STUDENT_COOKIE_NAME)
    if not token:
        return None
    return resolve_student_session(db, token)


def require_student(student: Student | None = Depends(current_student)) -> Student:
    """Dependency for student pages: redirect anonymous users to login."""
    if student is None:
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/login"},
        )
    return student


def require_not_banned(student: Student = Depends(require_student)) -> Student:
    """Reject a banned student with a clear 403 page (never a redirect).

    Uses the student loaded fresh from the database on this request, so a ban
    applied after the cookie was issued takes effect immediately.
    """
    if student.is_banned:
        raise StudentBlockedError()
    return student
