"""The ``maxlength``/``max`` caps rendered into the public forms.

Every free-text field a stranger can reach carries an upper bound, both as a
server-side constant (checked in ``app.config``) and as an HTML attribute. The
attribute is a convenience for an honest user, not a guard -- but it must be
there and must match the constant, which is what the markup checks pin down.
The second half of the file drives the same limits through the services and the
routes, where the cap is what actually refuses oversized input.
"""

import sys
from datetime import timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app.config import settings  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.core.templates import templates  # noqa: E402
from app.models.admin import Admin  # noqa: E402
from app.models.invite import Invite  # noqa: E402
from app.repositories.admins import create_admin  # noqa: E402
from app.services import admin_auth  # noqa: E402
from app.services import invites as invites_service  # noqa: E402
from app.services import registration as reg  # noqa: E402
from app.services import student_auth  # noqa: E402
from tests.conftest import extract_csrf  # noqa: E402

EMAIL = "novice@example.com"
PASSWORD = "secret1234"


def _post(client, url: str, page: str, data: dict):
    """GET ``page`` for a matching CSRF token, then POST ``data`` to ``url``."""
    token = extract_csrf(client.get(page).text)
    return client.post(
        url, data={**data, "csrf_token": token}, follow_redirects=False
    )


def _assert_attribute(html: str, attribute: str) -> None:
    """The page renders ``attribute`` (e.g. ``maxlength="254"``) verbatim."""
    assert attribute in html, f"{attribute!r} missing from the page"


def test_login_form_caps_its_fields(client):
    response = client.get("/login")

    assert response.status_code == 200
    _assert_attribute(response.text, 'maxlength="254"')  # email
    _assert_attribute(response.text, 'maxlength="128"')  # password
    _assert_attribute(response.text, 'maxlength="32"')   # invite code


def test_register_form_caps_its_fields(client):
    response = client.get("/register")

    assert response.status_code == 200
    _assert_attribute(response.text, 'maxlength="254"')  # email
    _assert_attribute(response.text, 'maxlength="128"')  # password


def test_qr_email_form_caps_the_email(client):
    response = client.get("/register/qr")

    assert response.status_code == 200
    _assert_attribute(response.text, 'maxlength="254"')


def test_qr_password_form_caps_the_password():
    # The password field lives on the "confirmed" screen of /register/qr, which
    # needs a confirmed QR token to reach over HTTP. Rendering the template
    # directly with the "confirmed" status checks the markup without that setup.
    html = templates.env.get_template("register_qr.html").render(
        status="confirmed", error=None, csrf_token="test", student=None
    )
    _assert_attribute(html, f'maxlength="{settings.MAX_PASSWORD_LENGTH}"')


def test_admin_login_form_caps_its_fields(client):
    response = client.get("/admin/login")

    assert response.status_code == 200
    _assert_attribute(response.text, 'maxlength="64"')   # username
    _assert_attribute(response.text, 'maxlength="128"')  # password


def test_admin_invite_form_caps_the_ttl(admin):
    response = admin.client.get("/admin/invites")

    assert response.status_code == 200
    _assert_attribute(response.text, 'max="365"')


def test_input_length_constants_have_the_expected_values():
    assert settings.MAX_EMAIL_LENGTH == 254
    assert settings.MAX_PASSWORD_LENGTH == 128
    assert settings.MAX_INVITE_CODE_INPUT_LENGTH == 32
    assert settings.MAX_ADMIN_USERNAME_LENGTH == 64
    assert settings.INVITE_TTL_MAX_DAYS == 365


# --- Service-level caps -------------------------------------------------------


def test_register_rejects_an_overlong_email(session_factory):
    # The address would pass the shape regex (local@domain.tld) and only the
    # length cap stands in its way, so the cap -- not the regex -- is what fired.
    overlong = "a" * settings.MAX_EMAIL_LENGTH + "@example.com"
    with session_factory() as db:
        result = reg.register_with_password(db, overlong, PASSWORD)
        assert result.reason is reg.RegistrationError.INVALID_EMAIL
        assert reg.student_count(db) == 0


def test_qr_start_rejects_an_overlong_email(session_factory):
    overlong = "a" * settings.MAX_EMAIL_LENGTH + "@example.com"
    with session_factory() as db:
        result = reg.start_qr_registration(db, overlong)
        assert result.reason is reg.RegistrationError.INVALID_EMAIL


def test_register_rejects_an_overlong_password(session_factory):
    overlong = "x" * (settings.MAX_PASSWORD_LENGTH + 1)
    with session_factory() as db:
        result = reg.register_with_password(db, EMAIL, overlong)
        assert result.reason is reg.RegistrationError.PASSWORD_TOO_LONG
        assert reg.student_count(db) == 0


def test_complete_qr_registration_rejects_an_overlong_password(session_factory):
    # The length cap is checked before the token is even looked up, so a bogus
    # token still reports the oversized password rather than INVALID_TOKEN.
    overlong = "x" * (settings.MAX_PASSWORD_LENGTH + 1)
    with session_factory() as db:
        result = reg.complete_qr_registration(db, "not-a-real-token", overlong)
        assert result.reason is reg.RegistrationError.PASSWORD_TOO_LONG


def test_values_exactly_at_the_caps_are_accepted(session_factory):
    """The caps are inclusive bounds: ``len == MAX`` must go through.

    Only the refusal side is covered elsewhere, which a ``>=`` typo in the
    comparison would pass unnoticed -- hence a pair of accepted boundary values.
    """
    with session_factory() as db:
        password = "x" * settings.MAX_PASSWORD_LENGTH
        email = "a" * (settings.MAX_EMAIL_LENGTH - len("@example.com")) + "@example.com"
        assert len(email) == settings.MAX_EMAIL_LENGTH

        result = reg.register_with_password(db, email, password)
        assert result.ok, result.reason
        assert result.student is not None


def test_empty_credentials_keep_their_own_refusals(session_factory):
    """An empty field is refused for the reason it deserves, not by the cap."""
    with session_factory() as db:
        assert (
            reg.register_with_password(db, "", PASSWORD).reason
            is reg.RegistrationError.INVALID_EMAIL
        )
        assert (
            reg.register_with_password(db, EMAIL, "").reason
            is reg.RegistrationError.PASSWORD_TOO_SHORT
        )
        assert student_auth.login_with_password(db, "", "") is None


def test_student_email_login_refuses_overlong_input(session_factory):
    with session_factory() as db:
        overlong_email = "a" * 1000 + "@example.com"
        assert student_auth.login_with_password(db, overlong_email, PASSWORD) is None
        assert student_auth.login_with_password(db, EMAIL, "x" * 1000) is None


def test_admin_login_refuses_overlong_input(session_factory):
    with session_factory() as db:
        create_admin(
            db, username="root", password_hash=hash_password("correct-password-123")
        )
        db.commit()

        overlong_username = "r" * (settings.MAX_ADMIN_USERNAME_LENGTH + 1)
        assert admin_auth.login(db, overlong_username, "correct-password-123") is None
        assert admin_auth.login(db, "root", "x" * 1000) is None
        # The real credentials still work, proving the cap is what refused.
        assert admin_auth.login(db, "root", "correct-password-123") is not None


def test_redeem_invite_refuses_an_overlong_code(session_factory):
    with session_factory() as db:
        # 10 000 characters: refused before normalization/hashing, and no crash.
        assert invites_service.redeem_invite(db, "A" * 10000) is None


def test_create_invite_clamps_the_ttl(session_factory):
    with session_factory() as db:
        admin = Admin(username="director", password_hash="x")
        db.add(admin)
        db.commit()

        invites_service.create_invite(db, admin, 0)
        invites_service.create_invite(db, admin, 10**6)

        spans = sorted(
            invite.expires_at - invite.created_at
            for invite in db.execute(select(Invite)).scalars()
        )
        assert len(spans) == 2
        # ``ttl_days=0`` is lifted to one day...
        assert timedelta(hours=23) < spans[0] <= timedelta(days=1, hours=1)
        # ...and ``10**6`` is capped at INVITE_TTL_MAX_DAYS.
        assert timedelta(days=364) < spans[1] <= timedelta(days=settings.INVITE_TTL_MAX_DAYS + 1)


# --- HTTP-level caps ----------------------------------------------------------


def test_register_post_rejects_an_overlong_email(client):
    response = _post(
        client,
        "/register",
        "/register",
        {"email": "a" * 1000 + "@example.com", "password": PASSWORD},
    )
    assert response.status_code == 400
    assert "корректный email" in response.text


def test_register_post_rejects_an_overlong_password(client):
    response = _post(
        client,
        "/register",
        "/register",
        {"email": EMAIL, "password": "x" * 1000},
    )
    assert response.status_code == 400
    assert "слишком длинный" in response.text


def test_email_login_post_refuses_overlong_input(client):
    response = _post(
        client,
        "/login/email",
        "/login",
        {"email": "a" * 1000 + "@example.com", "password": PASSWORD},
    )
    assert response.status_code == 401
    assert "Неверный email или пароль" in response.text


def test_admin_login_post_refuses_overlong_input(client):
    response = _post(
        client,
        "/admin/login",
        "/admin/login",
        {"username": "u" * 1000, "password": "x" * 1000},
    )
    assert response.status_code == 401
    assert "Неверный логин или пароль" in response.text


def test_invite_login_post_refuses_an_overlong_code(client):
    response = _post(client, "/login", "/login", {"code": "A" * 10000})
    assert response.status_code == 401
    assert "недействителен" in response.text


@pytest.mark.parametrize("bad_ttl", [0, 100000])
def test_admin_invite_post_rejects_out_of_range_ttl(admin, session_factory, bad_ttl):
    response = admin.post("/admin/invites", "/admin/invites", {"ttl_days": bad_ttl})

    assert response.status_code == 400
    # The invite form is redrawn, not a bare error page.
    assert "Коды приглашений" in response.text
    assert 'name="ttl_days"' in response.text
    # And the reason is on the page, not only in the status code.
    assert "Срок действия должен быть от 1 до" in response.text

    with session_factory() as db:
        assert invites_service.list_invites(db) == []


def test_a_non_numeric_ttl_never_reaches_the_route(admin):
    """A TTL that is not a number at all is answered by FastAPI, not by us.

    The range guard in the route covers numbers outside the window; ``abc``
    fails form parsing first and comes back as the framework's 422. Pinned down
    here so the edge stays visible rather than surprising.
    """
    response = admin.post("/admin/invites", "/admin/invites", {"ttl_days": "abc"})

    assert response.status_code == 422
