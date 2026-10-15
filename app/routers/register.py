"""Student self-registration: open email+password, then QR confirmation.

See ``app.services.registration`` for the rules and ``docs/PLAN.md`` for why the
limit is 30 and why invite codes remain a parallel path. Every unsafe form here
goes through the router-level CSRF dependency.
"""

from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request, Response, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
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
from app.models.registration_qr import QR_STATUS_PENDING
from app.models.student import Student
from app.services import login_throttle
from app.services import qr_image
from app.services import registration as registration_service
from app.services import student_auth

router = APIRouter(tags=["registration"], dependencies=[Depends(require_csrf)])

THROTTLE_ERROR = "Слишком много попыток. Попробуйте позже."
GENERIC_ERROR = "Не удалось зарегистрироваться. Проверьте данные."
TAKEN_ERROR = "Этот email уже зарегистрирован."
INVALID_EMAIL_ERROR = "Введите корректный email."

# Refusals of the QR path (see app.services.registration): both are states of
# the site, not mistakes of the visitor, so neither is worded as an error.
QR_LIMIT_ERROR = (
    "Сайт набран: регистрация через QR-код закрыта. Попросите "
    "администрацию школы выдать пригласительный код."
)
INVITE_LIMIT_ERROR = (
    "Вы подтвердили уже достаточно новичков. Следующий можно будет "
    f"подтвердить через {settings.QR_INVITE_WINDOW_HOURS} ч."
)

# The QR token cookie outlives the token itself so an expired page can still
# offer "Обновить код" (see registration.load_qr_cookie).
_QR_COOKIE_MAX_AGE = (settings.QR_TOKEN_TTL_HOURS + 24) * 3600


def _qr_page_context(status_value: str, scan_url: str, *, error: str | None = None) -> dict:
    return {"status": status_value, "error": error, "scan_url": scan_url}


def _scan_url(request: Request, raw_token: str) -> str:
    """The same code as a link, instead of an image, for the one-device jury.

    The link lands on ``/scan``, the page an *existing* student confirms from,
    so it is opened in a second (private) window by the student who does the
    confirming while the novice stays in the first one. The payload goes in the
    query string exactly as the QR image carries it.

    Absolute on purpose: it is meant to be copied out of the page and pasted
    into another window, and a relative path pasted into the address bar is not
    a URL the browser would resolve. The scheme is forced to https wherever the
    deployment says the cookies are Secure -- the platform terminates TLS before
    the app, so the request itself looks like plain http, and a copied http link
    would open a window the session cookie cannot follow.
    """
    base_url = str(request.base_url).rstrip("/")
    if settings.COOKIE_SECURE and base_url.startswith("http://"):
        base_url = "https://" + base_url[len("http://") :]
    payload = quote(qr_image.encode_payload(raw_token), safe="")
    return f"{base_url}/scan?payload={payload}"


def _set_qr_cookie(response: Response, raw_token: str) -> None:
    response.set_cookie(
        key=settings.QR_COOKIE_NAME,
        value=registration_service.dump_qr_cookie(raw_token),
        httponly=True,
        samesite="lax",
        secure=settings.COOKIE_SECURE,
        path="/",
        max_age=_QR_COOKIE_MAX_AGE,
    )


def _clear_qr_cookie(response: Response) -> None:
    response.delete_cookie(settings.QR_COOKIE_NAME, path="/")


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


def _read_qr_token(request: Request) -> str | None:
    return registration_service.load_qr_cookie(
        request.cookies.get(settings.QR_COOKIE_NAME)
    )


# --- Open email+password registration ----------------------------------------


@router.get("/register", response_class=HTMLResponse)
def register_form(
    request: Request,
    student: Student | None = Depends(current_student),
    db: Session = Depends(get_db),
) -> Response:
    if student is not None:
        return RedirectResponse(url="/feed", status_code=status.HTTP_303_SEE_OTHER)
    if not registration_service.registration_open(db):
        # The open form is gone for good once the limit is reached.
        return RedirectResponse(
            url="/register/qr", status_code=status.HTTP_303_SEE_OTHER
        )
    return templates.TemplateResponse(
        request,
        "register.html",
        {"error": None, "email": "", "open_limit": settings.OPEN_REGISTRATION_LIMIT},
    )


@router.post("/register", response_class=HTMLResponse)
def register_submit(
    request: Request,
    email: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
    db: Session = Depends(get_db),
) -> Response:
    ip = client_ip(request)
    retry_after = login_throttle.check_register(ip)
    if retry_after is not None:
        return templates.TemplateResponse(
            request,
            "register.html",
            {"error": THROTTLE_ERROR, "email": email},
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            headers={"Retry-After": str(retry_after)},
        )

    result = registration_service.register_with_password(db, email, password)
    if not result.ok:
        if result.reason is registration_service.RegistrationError.CLOSED:
            # The 30th student just landed: the form is gone, send them to QR.
            return RedirectResponse(
                url="/register/qr", status_code=status.HTTP_303_SEE_OTHER
            )
        message = {
            registration_service.RegistrationError.EMAIL_TAKEN: TAKEN_ERROR,
            registration_service.RegistrationError.INVALID_EMAIL: INVALID_EMAIL_ERROR,
            registration_service.RegistrationError.PASSWORD_TOO_SHORT: (
                f"Пароль слишком короткий: минимум "
                f"{settings.MIN_PASSWORD_LENGTH} символов"
            ),
            registration_service.RegistrationError.PASSWORD_TOO_LONG: (
                f"Пароль слишком длинный: максимум "
                f"{settings.MAX_PASSWORD_LENGTH} символов"
            ),
        }.get(result.reason, GENERIC_ERROR)
        return templates.TemplateResponse(
            request,
            "register.html",
            {"error": message, "email": email},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    login_throttle.clear_register(ip)
    response = RedirectResponse(url="/feed", status_code=status.HTTP_303_SEE_OTHER)
    _set_session_cookie(
        response, student_auth.issue_student_session(result.student)
    )
    return response


# --- QR registration ----------------------------------------------------------


@router.get("/register/qr", response_class=HTMLResponse)
def qr_page(
    request: Request,
    student: Student | None = Depends(current_student),
    db: Session = Depends(get_db),
) -> Response:
    if student is not None:
        return RedirectResponse(url="/feed", status_code=status.HTTP_303_SEE_OTHER)

    raw_token = _read_qr_token(request)
    token = (
        registration_service.get_qr_token(db, raw_token)
        if raw_token is not None
        else None
    )
    if token is None:
        # No (valid) token yet: ask for an email to create one.
        return templates.TemplateResponse(
            request, "register_qr_email.html", {"error": None, "email": ""}
        )

    status_value = registration_service.effective_status(token)
    return templates.TemplateResponse(
        request,
        "register_qr.html",
        _qr_page_context(status_value, _scan_url(request, raw_token)),
    )


@router.post("/register/qr", response_class=HTMLResponse)
def qr_start(
    request: Request,
    email: Annotated[str, Form()] = "",
    db: Session = Depends(get_db),
) -> Response:
    ip = client_ip(request)
    retry_after = login_throttle.check_register(ip)
    if retry_after is not None:
        return templates.TemplateResponse(
            request,
            "register_qr_email.html",
            {"error": THROTTLE_ERROR, "email": email},
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            headers={"Retry-After": str(retry_after)},
        )

    result = registration_service.start_qr_registration(db, email)
    if not result.ok:
        message = {
            registration_service.RegistrationError.EMAIL_TAKEN: TAKEN_ERROR,
            registration_service.RegistrationError.INVALID_EMAIL: INVALID_EMAIL_ERROR,
            registration_service.RegistrationError.QR_LIMIT: QR_LIMIT_ERROR,
        }.get(result.reason, GENERIC_ERROR)
        return templates.TemplateResponse(
            request,
            "register_qr_email.html",
            {"error": message, "email": email},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    response = RedirectResponse(
        url="/register/qr", status_code=status.HTTP_303_SEE_OTHER
    )
    _set_qr_cookie(response, result.raw_token)
    return response


@router.get("/register/qr/image.png")
def qr_image_png(
    request: Request, db: Session = Depends(get_db)
) -> Response:
    raw_token = _read_qr_token(request)
    token = (
        registration_service.get_qr_token(db, raw_token)
        if raw_token is not None
        else None
    )
    if token is None:
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    if registration_service.effective_status(token) != QR_STATUS_PENDING:
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    png = qr_image.make_png(qr_image.encode_payload(raw_token))
    return Response(
        content=png,
        media_type="image/png",
        # Never cache a live code across refreshes.
        headers={"Cache-Control": "no-store"},
    )


@router.post("/register/qr/refresh")
def qr_refresh(request: Request, db: Session = Depends(get_db)) -> Response:
    raw_token = _read_qr_token(request)
    result = (
        registration_service.refresh_qr_registration(db, raw_token)
        if raw_token is not None
        else registration_service.QrStartResult(ok=False)
    )
    response = RedirectResponse(
        url="/register/qr", status_code=status.HTTP_303_SEE_OTHER
    )
    if result.ok:
        _set_qr_cookie(response, result.raw_token)
    else:
        # A dead cookie would only trap the page: drop it and start over.
        _clear_qr_cookie(response)
    return response


@router.get("/register/qr/status")
def qr_status(request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    raw_token = _read_qr_token(request)
    token = (
        registration_service.get_qr_token(db, raw_token)
        if raw_token is not None
        else None
    )
    if token is None:
        return JSONResponse({"status": "invalid"}, headers={"Cache-Control": "no-store"})
    return JSONResponse(
        {"status": registration_service.effective_status(token)},
        headers={"Cache-Control": "no-store"},
    )


@router.post("/register/qr/password", response_class=HTMLResponse)
def qr_password(
    request: Request,
    password: Annotated[str, Form()] = "",
    db: Session = Depends(get_db),
) -> Response:
    raw_token = _read_qr_token(request)
    if raw_token is None:
        return RedirectResponse(
            url="/register/qr", status_code=status.HTTP_303_SEE_OTHER
        )

    result = registration_service.complete_qr_registration(db, raw_token, password)
    if not result.ok:
        message = {
            registration_service.RegistrationError.PASSWORD_TOO_SHORT: (
                f"Пароль слишком короткий: минимум "
                f"{settings.MIN_PASSWORD_LENGTH} символов"
            ),
            registration_service.RegistrationError.PASSWORD_TOO_LONG: (
                f"Пароль слишком длинный: максимум "
                f"{settings.MAX_PASSWORD_LENGTH} символов"
            ),
            registration_service.RegistrationError.EMAIL_TAKEN: TAKEN_ERROR,
            registration_service.RegistrationError.QR_LIMIT: QR_LIMIT_ERROR,
        }.get(result.reason, GENERIC_ERROR)
        token = registration_service.get_qr_token(db, raw_token)
        status_value = (
            registration_service.effective_status(token) if token else "invalid"
        )
        return templates.TemplateResponse(
            request,
            "register_qr.html",
            # Ссылка нужна только ветке «ждём подтверждения», но собирается
            # всегда: страницу рендерит один шаблон, и решать здесь, какая из
            # его ветвей покажется, не за чем.
            _qr_page_context(
                status_value, _scan_url(request, raw_token), error=message
            ),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    response = RedirectResponse(url="/feed", status_code=status.HTTP_303_SEE_OTHER)
    _clear_qr_cookie(response)
    _set_session_cookie(
        response, student_auth.issue_student_session(result.student)
    )
    return response


# --- Scanner (an existing student confirms a novice) --------------------------


@router.get("/scan", response_class=HTMLResponse)
def scan_page(
    request: Request,
    payload: str = "",
    student: Student = Depends(require_not_banned),
) -> Response:
    """The scanner, or the same confirmation without a camera.

    ``payload`` arrives filled in when the page was opened from the link the
    novice's page shows next to the QR image -- the one-device case: the
    confirming student works in this window while the novice stays in the other.
    It is only handed to the template (and posted from there by ``scan.js`` to
    ``/scan/confirm``, which validates it); the page itself reads nothing out of
    it and shows nothing back.
    """
    # ``student`` drives the navigation partials in base.html; without it the
    # scanner page renders with no way off it.
    return templates.TemplateResponse(
        request, "scan.html", {"student": student, "payload": payload}
    )


@router.post("/scan/confirm")
def scan_confirm(
    payload: Annotated[str, Form()] = "",
    student: Student = Depends(require_not_banned),
    db: Session = Depends(get_db),
) -> JSONResponse:
    raw_token = qr_image.decode_payload(payload)
    if raw_token is None:
        return JSONResponse(
            {"ok": False, "error": "Это не код регистрации"},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    reason = registration_service.confirm_qr_token(db, raw_token, student)
    if reason is not None:
        message = {
            registration_service.RegistrationError.TOKEN_EXPIRED: "Код истёк",
            registration_service.RegistrationError.INVALID_TOKEN: "Код не найден",
            registration_service.RegistrationError.INVITE_LIMIT: INVITE_LIMIT_ERROR,
            registration_service.RegistrationError.QR_LIMIT: QR_LIMIT_ERROR,
        }.get(reason, "Код недействителен")
        return JSONResponse(
            {"ok": False, "error": message}, status_code=status.HTTP_400_BAD_REQUEST
        )

    return JSONResponse({"ok": True})
