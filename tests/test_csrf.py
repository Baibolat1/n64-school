"""Tests for CSRF protection (``app.core.csrf``, ``app.core.deps.require_csrf``)."""

import sys
from pathlib import Path

import pytest
from sqlalchemy import update

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402
from app.core import csrf  # noqa: E402
from app.models.student import Student  # noqa: E402
from tests.conftest import extract_csrf  # noqa: E402


def test_get_sets_a_csrf_cookie_and_renders_the_token(client):
    response = client.get("/login")
    assert response.status_code == 200
    assert settings.CSRF_COOKIE_NAME in client.cookies
    assert extract_csrf(response.text) == client.cookies[settings.CSRF_COOKIE_NAME]


def test_csrf_cookie_is_httponly_and_lax(client):
    response = client.get("/login")
    set_cookie = response.headers["set-cookie"].lower()
    assert "httponly" in set_cookie
    assert "samesite=lax" in set_cookie


def test_post_without_a_token_is_refused(client):
    response = client.post("/login", data={"code": "nope"}, follow_redirects=False)
    assert response.status_code == 403
    assert "Не удалось отправить форму" in response.text


def test_post_with_a_garbage_token_is_refused(client):
    client.get("/login")
    response = client.post(
        "/login",
        data={"code": "nope", "csrf_token": "not-a-signed-token"},
        follow_redirects=False,
    )
    assert response.status_code == 403


def test_post_with_a_valid_token_from_another_browser_is_refused(client):
    # The cookie holds T1 (set by the GET), but the form carries a different --
    # though correctly signed -- token. Double submission must reject it.
    client.get("/login")
    other = csrf.generate_token()
    assert other != client.cookies[settings.CSRF_COOKIE_NAME]
    response = client.post(
        "/login",
        data={"code": "nope", "csrf_token": other},
        follow_redirects=False,
    )
    assert response.status_code == 403


def test_post_with_a_valid_token_reaches_the_handler(client):
    # With CSRF satisfied, a wrong invite code yields the normal 401 answer --
    # proof the request got past the CSRF gate rather than being blocked by it.
    token = extract_csrf(client.get("/login").text)
    response = client.post(
        "/login",
        data={"code": "nope", "csrf_token": token},
        follow_redirects=False,
    )
    assert response.status_code == 401


def test_logout_without_a_token_is_refused_and_keeps_the_session(student):
    student.csrf_for("/feed")  # make sure both cookies exist
    response = student.client.post("/logout", follow_redirects=False)
    assert response.status_code == 403
    assert settings.STUDENT_COOKIE_NAME in student.client.cookies


@pytest.mark.parametrize("path", ["/feed", "/me"])
def test_every_form_page_renders_a_token(student, path):
    # A page whose form lacks the hidden field would be unusable: the POST it
    # sends could never pass the gate.
    assert extract_csrf(student.client.get(path).text)


def test_blocked_page_still_renders_a_token(student, session_factory):
    # A banned student is denied every page but must still be able to log out,
    # which needs a token on the 403 page itself.
    with session_factory() as db:
        db.execute(
            update(Student).where(Student.id == student.student_id).values(is_banned=True)
        )
        db.commit()

    response = student.client.get("/feed")
    assert response.status_code == 403
    assert extract_csrf(response.text)


def test_logout_with_a_token_signs_the_student_out(student):
    response = student.post("/logout", page="/feed", data={})
    assert response.status_code == 303
    # The server asks the browser to drop the session cookie...
    set_cookie = response.headers["set-cookie"].lower()
    assert f"{settings.STUDENT_COOKIE_NAME}=" in set_cookie
    assert "max-age=0" in set_cookie
    # ...and the client honours it.
    assert student.client.cookies.get(settings.STUDENT_COOKIE_NAME) is None
