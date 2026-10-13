"""Tests for :class:`app.core.middleware.SecurityHeadersMiddleware`."""

import re
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
    "x-robots-tag": "noindex, nofollow",
}


@pytest.mark.parametrize("path", ["/login", "/health", "/admin/login"])
def test_headers_on_every_response(client, path):
    response = client.get(path)
    for name, value in EXPECTED.items():
        assert response.headers.get(name) == value

    csp = response.headers["content-security-policy"]
    assert "default-src 'self'" in csp
    assert "script-src 'self'" in csp
    assert "form-action 'self'" in csp
    assert "frame-ancestors 'none'" in csp


def test_no_inline_scripts_in_templates():
    """Every <script> in a template must have a ``src``.

    The CSP allows ``script-src 'self'`` and nothing else, so an inline block
    is dropped by the browser with no error anywhere -- the page just quietly
    stops working. Scanning the templates here turns that into a failing test.
    """
    templates = Path(__file__).resolve().parents[1] / "app" / "templates"
    offenders = []
    for path in sorted(templates.rglob("*.html")):
        text = path.read_text(encoding="utf-8")
        # Jinja comments never reach the browser, and prose in them may well
        # mention "<script>" while explaining why there must not be one.
        text = re.sub(r"\{#.*?#\}", "", text, flags=re.S)
        for match in re.finditer(r"<script\b[^>]*>", text):
            tag = match.group(0)
            if "src=" not in tag:
                offenders.append(f"{path.name}: {tag}")
    assert offenders == [], f"inline <script> is blocked by the CSP: {offenders}"


def test_headers_on_a_refused_csrf_post(client):
    """The 403 from the CSRF gate is still wrapped by the header middleware."""
    response = client.post("/login", data={"code": "x"}, follow_redirects=False)
    assert response.status_code == 403
    for name, value in EXPECTED.items():
        assert response.headers.get(name) == value


def test_robots_txt_disallows_everything(client):
    """The second layer of the noindex ban, for crawlers that skip the header."""
    response = client.get("/robots.txt")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "Disallow: /" in response.text


def test_hsts_is_absent_over_plain_http(client):
    # COOKIE_SECURE is False in development, so the site is not known to be on
    # HTTPS and an HSTS header would be meaningless.
    assert "strict-transport-security" not in client.get("/login").headers


def test_hsts_appears_when_cookies_are_secure(client, monkeypatch):
    monkeypatch.setattr(settings, "COOKIE_SECURE", True)
    headers = client.get("/login").headers
    assert headers["strict-transport-security"].startswith("max-age=")
