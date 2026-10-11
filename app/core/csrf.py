"""CSRF protection: a signed double-submit token in a cookie and a hidden field.

How it works
------------
A GET answer makes sure the browser holds a ``csrf_token`` cookie whose value is
a *signed* random token (itsdangerous, the same SECRET_KEY as the sessions but a
salt of its own). Every form embeds that same value in a hidden field, and an
unsafe request is accepted only when the submitted value is a valid signature
*and* equals the cookie byte for byte.

Why double-submit instead of a server-side token table: the student session is
already a stateless signed cookie (see ``docs/PLAN.md``), so the project has no
per-session storage to hang a token on. Signing keeps an attacker from minting
one -- without SECRET_KEY they cannot produce a valid signature -- while the
cookie/field equality ties the token to this browser. The cookie can stay
httpOnly because the server renders the value into the form itself: no
client-side script ever reads it.

The token carries no expiry of its own: it lives as long as the browser keeps
the cookie, and a signature that is valid is valid. Freshness buys nothing here
-- the protection is the signature plus the double submission.
"""

import hmac
import secrets

from fastapi import Request
from itsdangerous import BadSignature, URLSafeTimedSerializer

from app.config import settings

__all__ = [
    "CSRFError",
    "csrf_context",
    "generate_token",
    "is_valid_token",
    "tokens_match",
]

# A salt of its own so a CSRF token can never be mistaken for a session value
# signed with the same SECRET_KEY.
_SALT = "csrf-token"

# Number of random bytes behind one token (token_urlsafe uses ~1.33 chars/byte).
_TOKEN_BYTES = 32


class CSRFError(Exception):
    """Raised when an unsafe request carries no valid CSRF token.

    Handled in ``app.main`` as a 403 page, so a missing or stale form token is a
    clear message rather than a validation error on the form itself.
    """


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.SECRET_KEY, salt=_SALT)


def generate_token() -> str:
    """Return a fresh signed CSRF token (new random value every call)."""
    return _serializer().dumps(secrets.token_urlsafe(_TOKEN_BYTES))


def is_valid_token(token: str) -> bool:
    """True if ``token`` carries a signature made with our SECRET_KEY."""
    if not token:
        return False
    try:
        _serializer().loads(token)
    except BadSignature:
        return False
    return True


def tokens_match(submitted: str, cookie: str) -> bool:
    """True if the submitted form value is the cookie's value, byte for byte."""
    if not submitted or not cookie:
        return False
    return hmac.compare_digest(submitted, cookie)


def csrf_context(request: Request) -> dict[str, str]:
    """Jinja context processor: expose the token to every template.

    The value is put on ``request.state`` by ``CSRFMiddleware`` before routing,
    so a form can always render ``{{ csrf_token }}``.
    """
    return {"csrf_token": getattr(request.state, "csrf_token", "")}
