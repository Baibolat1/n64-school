"""Tests for login throttling (``app.services.login_throttle``).

Driven through the real routes: the point is not the limiter's arithmetic (that
is covered in ``test_rate_limit.py``) but that the counter is consulted before
the credentials, that a refusal is a 429 carrying ``Retry-After``, and that a
successful login clears the counter.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.repositories.admins import create_admin  # noqa: E402
from app.services import invites as invites_service  # noqa: E402
from tests.conftest import extract_csrf  # noqa: E402

ADMIN_PASSWORD = "правильный-пароль-123"


def _login(client, path, data, page):
    token = extract_csrf(client.get(page).text)
    return client.post(
        path, data={**data, "csrf_token": token}, follow_redirects=False
    )


def _wrong_student_codes(client, times):
    response = None
    for _ in range(times):
        response = _login(client, "/login", {"code": "НЕВЕРНЫЙ"}, "/login")
    return response


@pytest.fixture()
def admin(session_factory):
    with session_factory() as db:
        admin = create_admin(
            db, username="admin", password_hash=hash_password(ADMIN_PASSWORD)
        )
        db.commit()
        # Detached but usable: ``expire_on_commit`` is off, so ``admin.id``
        # stays readable after the session closes.
        return admin


def test_student_login_is_refused_after_the_limit(client, monkeypatch):
    monkeypatch.setattr(settings, "STUDENT_LOGIN_MAX_ATTEMPTS", 2)

    assert _wrong_student_codes(client, 2).status_code == 401
    refused = _login(client, "/login", {"code": "НЕВЕРНЫЙ"}, "/login")

    assert refused.status_code == 429
    assert "Слишком много попыток" in refused.text
    assert 899 <= int(refused.headers["retry-after"]) <= 900


def test_a_throttled_student_form_still_carries_a_token(client, monkeypatch):
    # The 429 page is a form like any other: without a token the visitor could
    # never submit again once the window expires.
    monkeypatch.setattr(settings, "STUDENT_LOGIN_MAX_ATTEMPTS", 0)
    refused = _login(client, "/login", {"code": "НЕВЕРНЫЙ"}, "/login")
    assert refused.status_code == 429
    assert extract_csrf(refused.text)


def test_admin_login_is_refused_after_the_limit(client, admin, monkeypatch):
    monkeypatch.setattr(settings, "ADMIN_LOGIN_MAX_ATTEMPTS", 2)

    wrong = {"username": "admin", "password": "не тот"}
    for _ in range(2):
        assert _login(client, "/admin/login", wrong, "/admin/login").status_code == 401

    refused = _login(client, "/admin/login", wrong, "/admin/login")
    assert refused.status_code == 429
    assert "Слишком много попыток" in refused.text
    assert int(refused.headers["retry-after"]) > 0


def test_correct_credentials_still_work_below_the_limit(client, admin, monkeypatch):
    monkeypatch.setattr(settings, "ADMIN_LOGIN_MAX_ATTEMPTS", 3)

    wrong = {"username": "admin", "password": "не тот"}
    assert _login(client, "/admin/login", wrong, "/admin/login").status_code == 401

    good = {"username": "admin", "password": ADMIN_PASSWORD}
    response = _login(client, "/admin/login", good, "/admin/login")
    assert response.status_code == 303
    assert response.headers["location"] == "/admin"


def test_a_successful_login_clears_the_counter(client, admin, monkeypatch):
    monkeypatch.setattr(settings, "ADMIN_LOGIN_MAX_ATTEMPTS", 2)

    wrong = {"username": "admin", "password": "не тот"}
    assert _login(client, "/admin/login", wrong, "/admin/login").status_code == 401
    good = {"username": "admin", "password": ADMIN_PASSWORD}
    assert _login(client, "/admin/login", good, "/admin/login").status_code == 303

    # The counter is back to zero, so the next typo is not the one that trips.
    assert _login(client, "/admin/login", wrong, "/admin/login").status_code == 401


def test_the_student_and_admin_counters_are_separate(client, monkeypatch):
    monkeypatch.setattr(settings, "ADMIN_LOGIN_MAX_ATTEMPTS", 0)

    assert _login(client, "/admin/login", {"username": "x", "password": "y"},
                  "/admin/login").status_code == 429
    # Tiring the admin form must not lock the student one, from the same IP.
    assert _login(client, "/login", {"code": "НЕВЕРНЫЙ"}, "/login").status_code == 401


def test_a_successful_student_login_clears_the_counter(
    client, session_factory, admin, monkeypatch
):
    monkeypatch.setattr(settings, "STUDENT_LOGIN_MAX_ATTEMPTS", 3)
    with session_factory() as db:
        code = invites_service.create_invite(db, admin, 1)

    assert _wrong_student_codes(client, 2).status_code == 401
    response = _login(client, "/login", {"code": code}, "/login")
    assert response.status_code == 303
    assert response.headers["location"] == "/me"

    # Counter cleared: three attempts were made, so without the reset the next
    # one would be the fourth and refused with 429 instead of 401.
    assert _login(client, "/login", {"code": "НЕВЕРНЫЙ"}, "/login").status_code == 401
