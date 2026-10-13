from pathlib import Path

from fastapi import FastAPI, Request, status
from fastapi.responses import PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.core.csrf import CSRFError
from app.core.deps import StudentBlockedError
from app.core.logging import configure_logging, log_startup_warnings
from app.core.middleware import CSRFMiddleware, SecurityHeadersMiddleware
from app.core.templates import templates
from app.routers import admin as admin_router
from app.routers import admin_moderation as admin_moderation_router
from app.routers import api as api_router
from app.routers import auth as auth_router
from app.routers import feed as feed_router
from app.routers import register as register_router
from app.routers import reports as reports_router
from app.routers import school as school_router
from app.routers import teachers as teachers_router

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title=settings.APP_NAME)

# One logger with one format for the whole project, and a warning (never a
# crash) when the configuration is unsafe to expose on the internet.
configure_logging()
log_startup_warnings()

# Middleware is applied to every response, error pages included. Starlette wraps
# later additions around earlier ones, so the header middleware ends up
# outermost and even a CSRF 403 goes out with the headers.
app.add_middleware(CSRFMiddleware)
app.add_middleware(SecurityHeadersMiddleware)

app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
app.include_router(auth_router.router)
app.include_router(register_router.router)
app.include_router(feed_router.router)
# The JSON twin of the routes above: same services, same rules, answers a script
# can use without a page turn (see app.routers.api).
app.include_router(api_router.router)
app.include_router(teachers_router.router)
app.include_router(reports_router.router)
app.include_router(school_router.router)
app.include_router(admin_router.router)
app.include_router(admin_moderation_router.router)


@app.exception_handler(StudentBlockedError)
async def student_blocked_handler(
    request: Request, exc: StudentBlockedError
) -> Response:
    """Render the 403 page for a banned student."""
    return templates.TemplateResponse(
        request, "blocked.html", {}, status_code=status.HTTP_403_FORBIDDEN
    )


@app.exception_handler(CSRFError)
async def csrf_error_handler(request: Request, exc: CSRFError) -> Response:
    """Render the 403 page for a missing or stale CSRF token."""
    return templates.TemplateResponse(
        request, "csrf_error.html", {}, status_code=status.HTTP_403_FORBIDDEN
    )


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/robots.txt")
async def robots_txt() -> PlainTextResponse:
    # A second layer on top of the X-Robots-Tag header: crawlers that ignore the
    # header on non-HTML responses (or do not read headers at all) still obey
    # this file. The whole site is closed, so everything is disallowed.
    return PlainTextResponse("User-agent: *\nDisallow: /\n")


@app.get("/manifest.json")
async def web_manifest() -> Response:
    """The web app manifest, served from the repository root.

    It is a file next to the code rather than under ``/static`` because the
    browser looks for it at the site root and its ``start_url`` and ``scope``
    are then read against the same root -- ``/`` is the app, not a subdirectory
    of one. The file is read per request, so an edit shows up on a reload (the
    same reason ``/robots.txt`` is a route and not a mounted file: nothing in
    the repository root is served otherwise).

    Public on purpose: a manifest behind a login cannot be read, and both the
    browser and the "add to home screen" flow fetch it before any session
    exists. It describes the *shell* only -- no data and no account is in it.
    """
    return Response(
        content=(BASE_DIR.parent / "manifest.json").read_text(encoding="utf-8"),
        media_type="application/manifest+json",
    )
