"""Self-registration: open email+password, the QR flow, and the limit.

The invite-code path (which stays parallel to both) is covered here too so a
regression in the shared login page cannot slip through.
"""

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import settings
from app.main import app
from app.models.admin import Admin
from app.models.student import Student
from app.services import invites as invites_service
from app.services import qr_image
from app.services import registration as reg
from app.services import student_auth
from tests.conftest import extract_csrf

EMAIL = "novice@example.com"
PASSWORD = "secret1234"


def post_with_csrf(client: TestClient, url: str, page: str, data: dict):
    """GET ``page`` for a matching CSRF token, then POST ``data`` to ``url``."""
    token = extract_csrf(client.get(page).text)
    return client.post(
        url, data={**data, "csrf_token": token}, follow_redirects=False
    )


def _logged_in_client(session_factory, alias: str) -> TestClient:
    """A second client, already signed in as a freshly created student."""
    with session_factory() as db:
        student = Student(alias=alias)
        db.add(student)
        db.commit()
        token = student_auth.issue_student_session(student)
    scanner = TestClient(app)
    scanner.cookies.set(
        settings.STUDENT_COOKIE_NAME, token, domain="testserver.local"
    )
    return scanner


def _students(session_factory) -> list[Student]:
    with session_factory() as db:
        return list(db.execute(select(Student)).scalars())


# --- Open email+password registration ----------------------------------------


def test_open_registration_creates_student_and_logs_in(client, session_factory):
    response = post_with_csrf(
        client,
        "/register",
        "/register",
        {"email": "Mixed@Example.com", "password": PASSWORD},
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/feed"
    assert settings.STUDENT_COOKIE_NAME in response.headers["set-cookie"]

    student = _students(session_factory)[0]
    # Stored lower-cased, hashed, never in the clear.
    assert student.email == "mixed@example.com"
    assert student.password_hash and student.password_hash != PASSWORD


def test_registration_closes_at_limit(client, session_factory):
    with session_factory() as db:
        for i in range(settings.OPEN_REGISTRATION_LIMIT):
            db.add(Student(alias=f"Ученик-{i:03d}"))
        db.commit()

    # The form is gone: both the page and the POST send newcomers to the QR flow.
    page = client.get("/register", follow_redirects=False)
    assert page.status_code == 303
    assert page.headers["location"] == "/register/qr"

    response = post_with_csrf(
        client, "/register", "/register/qr", {"email": EMAIL, "password": PASSWORD}
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/register/qr"
    assert len(_students(session_factory)) == settings.OPEN_REGISTRATION_LIMIT


def test_open_registration_cap_is_enforced_by_the_insert(session_factory, monkeypatch):
    # The cap lives in the INSERT itself, not only in a pre-check: the third
    # registration is refused at the statement level even though the caller
    # never consults registration_open() first.
    monkeypatch.setattr(settings, "OPEN_REGISTRATION_LIMIT", 2)
    with session_factory() as db:
        assert reg.register_with_password(db, "one@x.io", PASSWORD).ok
        assert reg.register_with_password(db, "two@x.io", PASSWORD).ok
        third = reg.register_with_password(db, "three@x.io", PASSWORD)
        assert third.reason is reg.RegistrationError.CLOSED
        assert reg.student_count(db) == 2


def test_duplicate_email_is_rejected(client, session_factory):
    post_with_csrf(
        client, "/register", "/register", {"email": EMAIL, "password": PASSWORD}
    )
    response = post_with_csrf(
        client, "/register", "/register", {"email": EMAIL, "password": PASSWORD}
    )
    assert response.status_code == 400
    assert "уже зарегистрирован" in response.text


def test_short_password_is_rejected(client, session_factory):
    response = post_with_csrf(
        client, "/register", "/register", {"email": EMAIL, "password": "short"}
    )
    assert response.status_code == 400
    assert "слишком короткий" in response.text
    assert _students(session_factory) == []


def test_register_post_requires_csrf(client):
    response = client.post(
        "/register", data={"email": EMAIL, "password": PASSWORD}
    )
    assert response.status_code == 403


def test_email_login_works_after_limit(client, session_factory):
    with session_factory() as db:
        assert reg.register_with_password(db, EMAIL, PASSWORD).ok
        for i in range(settings.OPEN_REGISTRATION_LIMIT):
            db.add(Student(alias=f"Позже-{i:03d}"))
        db.commit()

    # Registration is closed...
    closed = client.get("/register", follow_redirects=False)
    assert closed.headers["location"] == "/register/qr"
    # ...but the account created earlier can still sign in.
    response = post_with_csrf(
        client, "/login/email", "/login", {"email": EMAIL, "password": PASSWORD}
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/me"


def test_email_login_wrong_password_is_refused(client, session_factory):
    with session_factory() as db:
        assert reg.register_with_password(db, EMAIL, PASSWORD).ok
    response = post_with_csrf(
        client, "/login/email", "/login", {"email": EMAIL, "password": "wrong-pass"}
    )
    assert response.status_code == 401


# --- QR registration ----------------------------------------------------------


def test_qr_registration_flow(client, session_factory):
    # The novice enters an email and gets a QR code on screen.
    start = post_with_csrf(client, "/register/qr", "/register/qr", {"email": EMAIL})
    assert start.status_code == 303
    assert start.headers["location"] == "/register/qr"

    raw_token = reg.load_qr_cookie(client.cookies.get(settings.QR_COOKIE_NAME))
    assert raw_token

    page = client.get("/register/qr")
    assert page.status_code == 200
    assert "/register/qr/image.png" in page.text
    assert "любому ученику сайта" in page.text

    image = client.get("/register/qr/image.png")
    assert image.status_code == 200
    assert image.headers["content-type"] == "image/png"
    assert image.content[:8] == b"\x89PNG\r\n\x1a\n"

    # An existing student scans the code with their phone.
    scanner = _logged_in_client(session_factory, "Сканер-01")
    payload = qr_image.encode_payload(raw_token)
    scan = post_with_csrf(scanner, "/scan/confirm", "/scan", {"payload": payload})
    assert scan.status_code == 200
    assert scan.json()["ok"] is True

    # The novice's page now reports confirmed and offers the password step.
    assert client.get("/register/qr/status").json()["status"] == "confirmed"

    done = post_with_csrf(
        client, "/register/qr/password", "/register/qr", {"password": PASSWORD}
    )
    assert done.status_code == 303
    assert done.headers["location"] == "/feed"

    student = _students(session_factory)
    assert len(student) == 2  # the scanner and the new novice
    novice = next(s for s in student if s.email == EMAIL)
    assert novice.password_hash


def test_expired_qr_token_cannot_be_confirmed(client, session_factory):
    post_with_csrf(client, "/register/qr", "/register/qr", {"email": EMAIL})
    raw_token = reg.load_qr_cookie(client.cookies.get(settings.QR_COOKIE_NAME))

    with session_factory() as db:
        token = reg.get_qr_token(db, raw_token)
        token.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
        db.commit()

        assert client.get("/register/qr/status").json()["status"] == "expired"
        assert client.get("/register/qr/image.png").status_code == 404

        scanner = Student(alias="Сканер-02")
        db.add(scanner)
        db.commit()
        err = reg.confirm_qr_token(db, raw_token, scanner)
        assert err is reg.RegistrationError.TOKEN_EXPIRED


def test_refresh_retires_the_old_code(client, session_factory):
    post_with_csrf(client, "/register/qr", "/register/qr", {"email": EMAIL})
    old = reg.load_qr_cookie(client.cookies.get(settings.QR_COOKIE_NAME))

    refreshed = post_with_csrf(client, "/register/qr/refresh", "/register/qr", {})
    assert refreshed.status_code == 303
    new = reg.load_qr_cookie(client.cookies.get(settings.QR_COOKIE_NAME))
    assert new and new != old

    with session_factory() as db:
        assert reg.effective_status(reg.get_qr_token(db, old)) == "expired"
        assert reg.effective_status(reg.get_qr_token(db, new)) == "pending"


def test_scan_rejects_a_foreign_qr(client, session_factory):
    scanner = _logged_in_client(session_factory, "Сканер-03")
    response = post_with_csrf(
        scanner, "/scan/confirm", "/scan", {"payload": "https://example.com"}
    )
    assert response.status_code == 400
    assert response.json()["ok"] is False


def test_scan_requires_a_logged_in_student(client):
    response = client.get("/scan", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


# --- The invite path still works ---------------------------------------------


def test_invite_login_still_works(client, session_factory):
    with session_factory() as db:
        admin = Admin(username="director", password_hash="x")
        db.add(admin)
        db.commit()
        raw_code = invites_service.create_invite(
            db, admin, settings.INVITE_TTL_DAYS
        )

    response = post_with_csrf(client, "/login", "/login", {"code": raw_code})
    assert response.status_code == 303
    assert response.headers["location"] == "/me"
    student = _students(session_factory)[0]
    assert student.email is None and student.password_hash is None
