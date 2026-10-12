"""Cross-cutting HTTP middleware: CSRF cookie and security response headers."""

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.config import settings
from app.core import csrf

# Headers applied to *every* response. ``setdefault`` is used so a handler that
# knows better can override a value for one route.
#
# * nosniff / DENY / no-referrer need no explanation.
# * The CSP is deliberately tight. ``script-src 'self'`` -- not ``'none'`` --
#   because two pages need JavaScript: the QR scanner (``static/scan.js`` plus
#   the vendored ``static/vendor/jsqr.min.js``) and the QR-registration poller
#   (``static/register_qr.js``). ``'self'`` allows exactly those files and still
#   refuses inline ``<script>``, ``eval`` and every third-party host, so the
#   XSS surface stays flat: no template may grow an inline script.
# * ``form-action 'self'`` and ``base-uri 'none'`` close off form hijacking and
#   <base> games; ``frame-ancestors 'none'`` repeats the framing ban for
#   browsers that prefer the CSP directive.
_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": (
        "default-src 'self'; "
        "img-src 'self'; "
        "style-src 'self'; "
        "script-src 'self'; "
        "form-action 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'none'"
    ),
}


class CSRFMiddleware(BaseHTTPMiddleware):
    """Guarantee a valid CSRF cookie and expose the token to templates.

    On every request it reads the ``csrf_token`` cookie; if it is missing or its
    signature does not verify, a fresh token is minted and written to
    ``request.state.csrf_token``. The token is always mirrored into
    ``request.state`` so the Jinja context processor can render it, and the
    cookie is set on the way out only when it was missing or invalid -- an
    existing valid cookie is left untouched.

    The middleware does *not* validate unsafe requests: that is the job of the
    ``require_csrf`` dependency, which can read the parsed form body.
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        cookie_token = request.cookies.get(settings.CSRF_COOKIE_NAME, "")
        if csrf.is_valid_token(cookie_token):
            request.state.csrf_token = cookie_token
            fresh = False
        else:
            request.state.csrf_token = csrf.generate_token()
            fresh = True

        response = await call_next(request)

        if fresh:
            response.set_cookie(
                key=settings.CSRF_COOKIE_NAME,
                value=request.state.csrf_token,
                httponly=True,
                samesite="lax",
                secure=settings.COOKIE_SECURE,
                path="/",
            )
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Attach the security headers above to every response.

    HSTS is added only when cookies are already marked ``Secure``: that flag
    means the site is served over HTTPS, and an HSTS header sent over plain HTTP
    would do nothing (browsers ignore it).
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        response = await call_next(request)
        for name, value in _SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        if settings.COOKIE_SECURE:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        return response
