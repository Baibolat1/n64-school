from pathlib import Path

from fastapi import FastAPI, Request, status
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.core.csrf import CSRFError
from app.core.deps import StudentBlockedError
from app.core.middleware import CSRFMiddleware, SecurityHeadersMiddleware
from app.core.templates import templates
from app.routers import admin as admin_router
from app.routers import auth as auth_router
from app.routers import feed as feed_router
from app.routers import register as register_router

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title=settings.APP_NAME)

# Middleware is applied to every response, error pages included. Starlette wraps
# later additions around earlier ones, so the header middleware ends up
# outermost and even a CSRF 403 goes out with the headers.
app.add_middleware(CSRFMiddleware)
app.add_middleware(SecurityHeadersMiddleware)

app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
app.include_router(auth_router.router)
app.include_router(register_router.router)
app.include_router(feed_router.router)
app.include_router(admin_router.router)


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
