"""Tests for logging: startup warnings and the runtime safety of log lines.

The startup half pins the configuration warnings in :mod:`app.core.logging`; the
event half drives the real routes and asserts that each logged event appears
*and* that no personal data (query string, IP, login, e-mail, post body) leaks
alongside it.
"""

import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402
from app.core.logging import log_startup_warnings  # noqa: E402
from app.services.rate_limit import limiter  # noqa: E402
from tests.conftest import extract_csrf  # noqa: E402


def test_warns_when_debug_is_off_and_cookies_are_not_secure(monkeypatch, caplog):
    """DEBUG off + COOKIE_SECURE off is the "loaded gun" combination."""
    monkeypatch.setattr(settings, "DEBUG", False)
    monkeypatch.setattr(settings, "COOKIE_SECURE", False)
    monkeypatch.setattr(settings, "SECRET_KEY", "разумный-ключ")
    with caplog.at_level(logging.WARNING, logger="app"):
        log_startup_warnings()
    assert any(
        record.levelno == logging.WARNING and "COOKIE_SECURE" in record.message
        for record in caplog.records
    )


@pytest.mark.parametrize(
    ("debug", "secure"),
    [(True, False), (False, True), (True, True)],
)
def test_no_warnings_on_a_safe_configuration(monkeypatch, caplog, debug, secure):
    """DEBUG or COOKIE_SECURE makes the cookie warning go away."""
    monkeypatch.setattr(settings, "DEBUG", debug)
    monkeypatch.setattr(settings, "COOKIE_SECURE", secure)
    monkeypatch.setattr(settings, "SECRET_KEY", "разумный-ключ")
    with caplog.at_level(logging.WARNING, logger="app"):
        log_startup_warnings()
    assert caplog.records == []


@pytest.mark.parametrize(
    "placeholder",
    # The bare default and the placeholder the README sends you to: both must be
    # caught, or a fresh installation would ship with a public signing key.
    ["change-me", "change-me-to-a-long-random-string", ""],
)
def test_warns_about_the_default_secret_key(monkeypatch, caplog, placeholder):
    """A placeholder SECRET_KEY is called out even when cookies look fine."""
    # DEBUG keeps the cookie warning out of the way so only the key one is on.
    monkeypatch.setattr(settings, "DEBUG", True)
    monkeypatch.setattr(settings, "SECRET_KEY", placeholder)
    with caplog.at_level(logging.WARNING, logger="app"):
        log_startup_warnings()
    assert any("SECRET_KEY" in record.message for record in caplog.records)


def test_importing_the_app_does_not_fail():
    """Smoke test: wiring the warnings into app.main must not break import."""
    import app.main  # noqa: F401


# --- Runtime events: the event is logged, the personal data is not -----------


def test_csrf_refusal_is_logged_without_the_query(client, caplog):
    """A refused token logs the method and path, never the query string."""
    sentinel = "SENTINEL-EMAIL@example.com"
    with caplog.at_level(logging.INFO, logger="app"):
        response = client.post(
            f"/login?email={sentinel}",
            data={"code": "nope"},
            follow_redirects=False,
        )
    assert response.status_code == 403
    assert "csrf_refused" in caplog.text
    assert sentinel not in caplog.text


def test_admin_login_throttle_is_logged_without_ip_or_login(client, caplog, monkeypatch):
    """A throttled admin login logs the scope only -- no IP, no account name."""
    sentinel_login = "sentinel-admin-name"
    token = extract_csrf(client.get("/admin/login").text)
    monkeypatch.setattr(settings, "ADMIN_LOGIN_MAX_ATTEMPTS", 0)
    limiter.reset()
    with caplog.at_level(logging.INFO, logger="app"):
        response = client.post(
            "/admin/login",
            data={
                "username": sentinel_login,
                "password": "какой-то-пароль",
                "csrf_token": token,
            },
            follow_redirects=False,
        )
    assert response.status_code == 429
    assert "login_throttled" in caplog.text
    assert "scope=admin" in caplog.text
    assert "127.0.0.1" not in caplog.text
    assert "testclient" not in caplog.text
    assert sentinel_login not in caplog.text


def test_publish_rate_limit_is_logged_without_the_text(student, caplog, monkeypatch):
    """A post refused by the limiter logs the target type, never the body."""
    sentinel_body = "SENTINEL-RATE-BODY"
    monkeypatch.setattr(settings, "RATE_LIMIT_POSTS", 0)
    limiter.reset()
    with caplog.at_level(logging.INFO, logger="app"):
        response = student.post("/feed/posts", "/feed", {"body": sentinel_body})
    assert response.status_code == 429
    assert "publish_rate_limited" in caplog.text
    assert "target_type=post" in caplog.text
    assert sentinel_body not in caplog.text


def test_publish_pii_block_is_logged_without_the_text(student, caplog):
    """A hard-PII refusal logs the target and rule code, never the body."""
    sentinel = "SENTINEL-PII-MARKER"
    phone = "+7 912 345-67-89"
    with caplog.at_level(logging.INFO, logger="app"):
        response = student.post(
            "/feed/posts", "/feed", {"body": f"{sentinel} звони {phone}"}
        )
    assert response.status_code == 200
    assert "publish_pii_blocked" in caplog.text
    assert "phone" in caplog.text
    assert sentinel not in caplog.text
    assert phone not in caplog.text


def test_student_login_failure_is_logged_without_the_email(client, caplog):
    """A failed email login logs the event only -- the address stays out."""
    sentinel = "sentinel-student@example.com"
    token = extract_csrf(client.get("/login").text)
    with caplog.at_level(logging.INFO, logger="app"):
        response = client.post(
            "/login/email",
            data={
                "email": sentinel,
                "password": "какой-то-пароль",
                "csrf_token": token,
            },
            follow_redirects=False,
        )
    assert response.status_code == 401
    assert "student_login_failed" in caplog.text
    assert sentinel not in caplog.text


def test_admin_login_failure_is_logged_without_the_username(client, caplog):
    """A failed admin login logs the event only -- the account name stays out."""
    sentinel = "sentinel-admin-name"
    token = extract_csrf(client.get("/admin/login").text)
    with caplog.at_level(logging.INFO, logger="app"):
        response = client.post(
            "/admin/login",
            data={
                "username": sentinel,
                "password": "какой-то-пароль",
                "csrf_token": token,
            },
            follow_redirects=False,
        )
    assert response.status_code == 401
    assert "admin_login_failed" in caplog.text
    assert sentinel not in caplog.text
