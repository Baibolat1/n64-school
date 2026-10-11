"""Tests for :class:`app.core.middleware.SecurityHeadersMiddleware`."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402

# Headers that must be present, with the exact value expected.
EXPECTED = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "no-referrer",
}


@pytest.mark.parametrize("path", ["/login", "/health", "/admin/login"])
def test_headers_on_every_response(client, path):
    response = client.get(path)
    for name, value in EXPECTED.items():
        assert response.headers.get(name) == value

    csp = response.headers["content-security-policy"]
    assert "default-src 'self'" in csp
    assert "script-src 'none'" in csp
    assert "form-action 'self'" in csp
    assert "frame-ancestors 'none'" in csp


def test_headers_on_a_refused_csrf_post(client):
    """The 403 from the CSRF gate is still wrapped by the header middleware."""
    response = client.post("/login", data={"code": "x"}, follow_redirects=False)
    assert response.status_code == 403
    for name, value in EXPECTED.items():
        assert response.headers.get(name) == value


def test_hsts_is_absent_over_plain_http(client):
    # COOKIE_SECURE is False in development, so the site is not known to be on
    # HTTPS and an HSTS header would be meaningless.
    assert "strict-transport-security" not in client.get("/login").headers


def test_hsts_appears_when_cookies_are_secure(client, monkeypatch):
    monkeypatch.setattr(settings, "COOKIE_SECURE", True)
    headers = client.get("/login").headers
    assert headers["strict-transport-security"].startswith("max-age=")
