"""Tests for the JSON endpoints the pages call without reloading.

Every one of them is a second door into a service that already had a first one,
so what is checked here is the door and not the room: that it refuses a request
with no CSRF token (403, the same page the forms get), that it will not answer
somebody who is not logged in, that a refusal arrives as ``{"ok": false}`` with
the sentence the service wrote, and that a success carries back the markup the
script is supposed to insert. The rules themselves -- length, PII, rate limits,
one like per student -- are tested against the services and the HTML routes
already; repeating them here would only test the service twice.
"""

import sys
from pathlib import Path

import pytest
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models.content import (  # noqa: E402
    CONTENT_STATUS_HIDDEN,
    CONTENT_STATUS_VISIBLE,
    Comment,
    Post,
)
from app.models.student import Student  # noqa: E402
from tests.conftest import extract_csrf  # noqa: E402


def _make_comment(
    session_factory,
    post_id,
    author_id,
    body="Комментарий",
    status=CONTENT_STATUS_VISIBLE,
) -> int:
    with session_factory() as db:
        comment = Comment(
            post_id=post_id,
            author_id=author_id,
            display_alias="Ученик-01",
            is_anonymous=False,
            body=body,
            status=status,
        )
        db.add(comment)
        db.commit()
        return comment.id


def _make_post(
    session_factory,
    author_id,
    body="Тема для обсуждения",
    status=CONTENT_STATUS_VISIBLE,
) -> int:
    with session_factory() as db:
        post = Post(
            author_id=author_id,
            display_alias="Ученик-01",
            is_anonymous=False,
            body=body,
            status=status,
        )
        db.add(post)
        db.commit()
        return post.id


def _posts(session_factory) -> list[Post]:
    with session_factory() as db:
        return list(db.execute(select(Post)).scalars().all())


def _card(html: str, post_id: int) -> str:
    """The markup of one post's card, read off the page that rendered it.

    A whole-page assertion would pass as soon as *any* card looked right, and
    all the cards look alike.
    """
    for card in html.split('<article class="post-card">')[1:]:
        if f"/api/posts/{post_id}/like" in card:
            return card
    raise AssertionError(f"no post card for post {post_id} in the page")


# --- The door ---------------------------------------------------------------


def test_a_json_post_without_a_csrf_token_is_refused(student, session_factory):
    # The token is what a script cannot forge: it has to come out of the page,
    # so a request that skips the page is refused exactly like a form would be.
    student.client.get("/feed")
    response = student.client.post(
        "/api/posts", data={"body": "привет"}, follow_redirects=False
    )
    assert response.status_code == 403
    assert _posts(session_factory) == []


def test_an_anonymous_json_post_is_sent_to_the_login_page(client, session_factory):
    # A script cannot follow a redirect to a login page and neither can it show
    # one, but the important half is that nothing is written.
    token = extract_csrf(client.get("/login").text)
    response = client.post(
        "/api/posts",
        data={"body": "привет", "csrf_token": token},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    assert _posts(session_factory) == []


def test_a_banned_student_gets_a_403(student, session_factory):
    # The token is taken before the ban: /feed is a 403 page afterwards, and the
    # point of the test is the write, not the page it would be read from.
    token = student.csrf_for("/feed")
    with session_factory() as db:
        row = db.get(Student, student.student_id)
        row.is_banned = True
        db.commit()

    response = student.client.post(
        "/api/posts",
        data={"body": "привет", "csrf_token": token},
        follow_redirects=False,
    )
    assert response.status_code == 403
    assert _posts(session_factory) == []


# --- Likes -------------------------------------------------------------------


def test_like_toggles_and_reports_the_state(student, session_factory):
    post_id = _make_post(session_factory, student.student_id)

    first = student.post(f"/api/posts/{post_id}/like", "/feed", {})
    assert first.status_code == 200
    assert first.json() == {"ok": True, "liked": True, "count": 1}

    second = student.post(f"/api/posts/{post_id}/like", "/feed", {})
    assert second.json() == {"ok": True, "liked": False, "count": 0}


def test_liking_a_post_that_is_not_there_is_a_json_404(student, session_factory):
    hidden = _make_post(
        session_factory, student.student_id, status=CONTENT_STATUS_HIDDEN
    )

    for post_id in (hidden, 99999):
        response = student.post(f"/api/posts/{post_id}/like", "/feed", {})
        # 404 and not 403, so a hidden post is indistinguishable from one that
        # never existed -- and the answer is JSON, not the 404 page.
        assert response.status_code == 404
        assert response.json()["ok"] is False


def test_the_reloaded_feed_agrees_with_the_toggle(student, session_factory):
    post_id = _make_post(session_factory, student.student_id)
    student.post(f"/api/posts/{post_id}/like", "/feed", {})

    # The card on the page and the card the script paints have to be in the same
    # state, or the heart and the counter disagree the moment the page is asked
    # for again. This is the half of the toggle the JSON answer cannot cover.
    card = _card(student.client.get("/feed").text, post_id)
    assert f'data-api="/api/posts/{post_id}/like"' in card
    assert 'aria-pressed="true"' in card


def test_a_comment_like_toggles_through_the_same_answer(student, session_factory):
    post_id = _make_post(session_factory, student.student_id)
    comment_id = _make_comment(session_factory, post_id, student.student_id)
    url = f"/api/posts/{post_id}/comments/{comment_id}/like"

    first = student.post(url, f"/feed/posts/{post_id}", {})
    assert first.status_code == 200
    assert first.json() == {"ok": True, "liked": True, "count": 1}

    second = student.post(url, f"/feed/posts/{post_id}", {})
    assert second.json() == {"ok": True, "liked": False, "count": 0}


def test_a_comment_of_another_post_is_a_json_404(student, session_factory):
    post_id = _make_post(session_factory, student.student_id)
    other_post = _make_post(session_factory, student.student_id)
    comment_id = _make_comment(session_factory, other_post, student.student_id)

    response = student.post(
        f"/api/posts/{post_id}/comments/{comment_id}/like", "/feed", {}
    )

    # The pair in the URL has to make sense: a comment addressed through the
    # wrong post is not found, exactly as it is by the HTML route.
    assert response.status_code == 404
    assert response.json()["ok"] is False


# --- Publishing ---------------------------------------------------------------


def test_a_new_post_comes_back_as_its_own_card(student, session_factory):
    response = student.post(
        "/api/posts", "/feed", {"body": "Новая тема", "is_anonymous": "true"}
    )
    payload = response.json()

    assert payload["ok"] is True
    # The card is the same partial the feed renders, so it arrives with the
    # like form and the async attributes already on it.
    assert "Новая тема" in payload["html"]
    assert f'data-async="like"' in payload["html"]
    assert "Аноним" in payload["html"]
    assert len(_posts(session_factory)) == 1


def test_a_refused_post_says_why_without_an_error_status(student):
    response = student.post("/api/posts", "/feed", {"body": "   "})

    # 200: nothing about the request was malformed, the text simply cannot be
    # published, and a browser would log a 4xx as an error.
    assert response.status_code == 200
    assert response.json() == {"ok": False, "error": "Текст не может быть пустым"}


def test_personal_data_in_a_post_is_refused(student, session_factory):
    response = student.post(
        "/api/posts", "/feed", {"body": "пиши мне +7 999 123-45-67"}
    )
    assert response.json()["ok"] is False
    assert "нельзя публиковать" in response.json()["error"]
    assert _posts(session_factory) == []


# --- Comments -----------------------------------------------------------------


def test_a_comment_comes_back_with_the_new_total(student, session_factory):
    post_id = _make_post(session_factory, student.student_id)

    response = student.post(
        f"/api/posts/{post_id}/comments", f"/feed/posts/{post_id}", {"body": "Первый"}
    )
    payload = response.json()

    assert payload["ok"] is True
    assert payload["count"] == 1
    assert "Первый" in payload["html"]
    assert 'class="comment"' in payload["html"]


def test_commenting_on_a_hidden_post_is_a_json_404(student, session_factory):
    hidden = _make_post(
        session_factory, student.student_id, status=CONTENT_STATUS_HIDDEN
    )
    response = student.post(
        f"/api/posts/{hidden}/comments", "/feed", {"body": "есть кто?"}
    )
    assert response.status_code == 404
    assert response.json()["ok"] is False


# --- The assistant ------------------------------------------------------------


def test_the_assistant_answers_with_rendered_html(student):
    response = student.post("/api/assistant", "/assistant", {"question": "Что ты умеешь?"})
    payload = response.json()

    assert payload["ok"] is True
    assert payload["source"]
    # Markup, not the model's text: the escaping happens on the server so there
    # is no second implementation of it in JavaScript.
    assert payload["html"].startswith("<")
    assert "<script" not in payload["html"]


def test_the_assistant_refuses_an_empty_question(student):
    response = student.post("/api/assistant", "/assistant", {"question": "   "})
    assert response.json() == {"ok": False, "error": "Введите вопрос."}


def test_the_assistant_refuses_a_question_that_is_too_long(student):
    response = student.post("/api/assistant", "/assistant", {"question": "а" * 401})
    payload = response.json()
    assert payload["ok"] is False
    assert "максимум" in payload["error"]


# --- Profile ------------------------------------------------------------------


def test_an_unknown_avatar_key_is_refused(student):
    response = student.post("/api/me/avatar", "/me", {"avatar_key": "nope"})
    assert response.json() == {"ok": False, "error": "Такой картинки нет в галерее"}


def test_an_alias_change_answers_with_the_stored_value(student):
    response = student.post("/api/me/alias", "/me", {"new_alias": "  Новый  Ник "})
    payload = response.json()

    assert payload["ok"] is True
    # Normalised, exactly as the service stored it: the field is filled from
    # this value, so it has to be the stored one and not the typed one.
    assert payload["alias"] == "Новый Ник"
    assert payload["message"]


def test_a_refused_alias_says_why(student):
    response = student.post("/api/me/alias", "/me", {"new_alias": "ab"})
    payload = response.json()
    assert payload["ok"] is False
    assert "короткий" in payload["error"]


# --- The dev team --------------------------------------------------------------


def test_a_dev_application_is_accepted_and_normalised(student, session_factory):
    from app.services import team as team_service

    response = student.post(
        "/api/team-applications",
        "/me",
        {"role": "frontend", "contact": "t.me/nickname"},
    )
    assert response.json()["ok"] is True

    with session_factory() as db:
        application = team_service.get_for_student(db, student.student_id)
        assert application is not None
        assert application.role == "frontend"
        assert application.contact == "@nickname"


@pytest.mark.parametrize(
    "role, contact",
    [
        ("smm", "@nickname"),
        ("frontend", ""),
        ("frontend", "не ник"),
    ],
)
def test_a_bad_dev_application_says_why(student, session_factory, role, contact):
    response = student.post(
        "/api/team-applications", "/me", {"role": role, "contact": contact}
    )
    payload = response.json()
    assert payload["ok"] is False
    assert payload["error"]
