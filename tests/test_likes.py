"""Tests for post likes: the toggle route, the counters and the constraints.

Two layers are covered on purpose. The route is driven through a real client --
CSRF token, session, service, repository, database -- because that is the only
way to see what a student actually gets. The table itself is checked directly as
well, since the "one like per student per post" rule must hold even if two
requests slip past each other: the guarantee is the unique constraint, not the
read-then-write in the toggle.
"""

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select, update
from sqlalchemy.exc import IntegrityError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402
from app.models.content import (  # noqa: E402
    CONTENT_STATUS_HIDDEN,
    CONTENT_STATUS_VISIBLE,
    Post,
)
from app.models.like import PostLike  # noqa: E402
from app.models.student import Student  # noqa: E402
from app.repositories import likes as likes_repo  # noqa: E402
from app.services import likes as likes_service  # noqa: E402
from app.services import student_auth  # noqa: E402
from tests.conftest import extract_csrf  # noqa: E402

LIKE_URL = "/feed/posts/{post_id}/like"


def _make_post(
    session_factory,
    author_id,
    body="Тема для обсуждения",
    status=CONTENT_STATUS_VISIBLE,
):
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


def _make_student(session_factory, alias="Ученик-02"):
    with session_factory() as db:
        other = Student(alias=alias)
        db.add(other)
        db.commit()
        return other.id


def _likes(session_factory):
    with session_factory() as db:
        return list(db.execute(select(PostLike)).scalars().all())


def _post_card_html(html: str, post_id: int) -> str:
    """The markup of one post's card, nothing else.

    A whole-page assertion would pass as soon as *any* card looked right, and the
    two cards look alike: the count and the filled heart have to be read off the
    card of the post under test.
    """
    for card in html.split('<article class="post-card">')[1:]:
        if f"/feed/posts/{post_id}/like" in card:
            return card
    raise AssertionError(f"no post card for post {post_id} in the page")


def test_liking_a_post_stores_the_like_and_returns_to_the_feed(student, session_factory):
    post_id = _make_post(session_factory, student.student_id)

    response = student.post(
        LIKE_URL.format(post_id=post_id), "/feed", {"next_url": "/feed"}
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/feed"

    likes = _likes(session_factory)
    assert len(likes) == 1
    assert likes[0].student_id == student.student_id
    assert likes[0].post_id == post_id
    assert likes[0].created_at is not None


def test_liking_again_takes_the_like_back(student, session_factory):
    post_id = _make_post(session_factory, student.student_id)
    student.post(LIKE_URL.format(post_id=post_id), "/feed", {"next_url": "/feed"})

    response = student.post(
        LIKE_URL.format(post_id=post_id), "/feed", {"next_url": "/feed"}
    )

    assert response.status_code == 303
    assert _likes(session_factory) == []


def test_a_student_cannot_like_the_same_post_twice(student, session_factory):
    # The database, not the toggle, is what makes a double like impossible.
    post_id = _make_post(session_factory, _make_student(session_factory))

    with session_factory() as db:
        db.add(PostLike(student_id=student.student_id, post_id=post_id))
        db.commit()
        db.add(PostLike(student_id=student.student_id, post_id=post_id))
        with pytest.raises(IntegrityError) as excinfo:
            db.commit()

    # SQLite names the columns it refused, not the constraint; the constraint's
    # own name is checked against the schema in the test below.
    assert "UNIQUE constraint failed" in str(excinfo.value)
    assert "post_likes.student_id" in str(excinfo.value)
    assert len(_likes(session_factory)) == 1


def test_the_table_has_the_unique_pair_and_the_lookup_indexes(db_engine):
    inspector = inspect(db_engine)

    unique = inspector.get_unique_constraints("post_likes")
    assert any(
        constraint["column_names"] == ["student_id", "post_id"]
        and constraint["name"] == "uq_post_likes_student_post"
        for constraint in unique
    )

    indexed = {tuple(index["column_names"]) for index in inspector.get_indexes("post_likes")}
    assert ("student_id",) in indexed
    assert ("post_id",) in indexed


def test_the_counter_and_the_filled_heart_appear_on_the_feed(student, session_factory):
    post_id = _make_post(session_factory, student.student_id)
    student.post(LIKE_URL.format(post_id=post_id), "/feed", {"next_url": "/feed"})

    html = student.client.get("/feed").text

    card = _post_card_html(html, post_id)
    assert "action--like is-liked" in card
    assert 'aria-pressed="true"' in card
    assert '<span class="action__count">1</span>' in card


def test_an_unliked_post_has_a_hollow_heart_and_a_zero(student, session_factory):
    post_id = _make_post(session_factory, student.student_id)

    card = _post_card_html(student.client.get("/feed").text, post_id)

    assert "action--like is-liked" not in card
    assert 'aria-pressed="false"' in card
    assert '<span class="action__count">0</span>' in card


def test_another_students_like_counts_but_does_not_fill_my_heart(
    student, session_factory
):
    other_id = _make_student(session_factory)
    post_id = _make_post(session_factory, other_id)
    with session_factory() as db:
        db.add(PostLike(student_id=other_id, post_id=post_id))
        db.commit()

    card = _post_card_html(student.client.get("/feed").text, post_id)

    assert '<span class="action__count">1</span>' in card
    assert "action--like is-liked" not in card


def test_counts_do_not_leak_between_posts(student, session_factory):
    liked_id = _make_post(session_factory, student.student_id, body="Первый")
    other_id = _make_post(session_factory, _make_student(session_factory), body="Второй")
    student.post(LIKE_URL.format(post_id=liked_id), "/feed", {"next_url": "/feed"})

    html = student.client.get("/feed").text

    assert '<span class="action__count">1</span>' in _post_card_html(html, liked_id)
    assert '<span class="action__count">0</span>' in _post_card_html(html, other_id)


def test_two_students_like_the_same_post(student, session_factory):
    post_id = _make_post(session_factory, student.student_id)
    student.post(LIKE_URL.format(post_id=post_id), "/feed", {"next_url": "/feed"})

    with session_factory() as db:
        other = Student(alias="Ученик-03")
        db.add(other)
        db.commit()
        token = student_auth.issue_student_session(other)
        other_id = other.id

    # A second browser, i.e. a separate cookie jar over the same database.
    with TestClient(app) as other_client:
        other_client.cookies.set(
            settings.STUDENT_COOKIE_NAME, token, domain="testserver.local"
        )
        card = _post_card_html(other_client.get("/feed").text, post_id)
        assert "action--like is-liked" not in card
        assert '<span class="action__count">1</span>' in card

        response = other_client.post(
            LIKE_URL.format(post_id=post_id),
            data={
                "csrf_token": extract_csrf(other_client.get("/feed").text),
                "next_url": "/feed",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303

    assert {like.student_id for like in _likes(session_factory)} == {
        student.student_id,
        other_id,
    }


def test_the_like_works_from_the_post_page_and_comes_back_to_it(
    student, session_factory
):
    post_id = _make_post(session_factory, student.student_id)
    page = f"/feed/posts/{post_id}"

    response = student.post(LIKE_URL.format(post_id=post_id), page, {"next_url": page})

    assert response.status_code == 303
    assert response.headers["location"] == page

    card = _post_card_html(student.client.get(page).text, post_id)
    assert "action--like is-liked" in card
    assert '<span class="action__count">1</span>' in card


def test_a_foreign_next_url_falls_back_to_the_feed(student, session_factory):
    # The like route must never become a redirector to somebody else's site.
    post_id = _make_post(session_factory, student.student_id)

    response = student.post(
        LIKE_URL.format(post_id=post_id),
        "/feed",
        {"next_url": "https://evil.example/steal"},
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/feed"


def test_a_missing_next_url_lands_on_the_feed(student, session_factory):
    post_id = _make_post(session_factory, student.student_id)

    response = student.post(LIKE_URL.format(post_id=post_id), "/feed", {})

    assert response.status_code == 303
    assert response.headers["location"] == "/feed"


def test_comments_have_no_like_control(student, session_factory):
    post_id = _make_post(session_factory, student.student_id)
    page = f"/feed/posts/{post_id}"
    for body in ("Первый комментарий", "Второй комментарий"):
        response = student.post(
            f"{page}/comments", page, {"body": body}
        )
        assert response.status_code == 303

    html = student.client.get(page).text

    assert html.count('class="comment"') == 2
    # Exactly one heart on the page: the post's own. No likes on comments.
    assert html.count("action--like") == 1


def test_a_hidden_post_cannot_be_liked(student, session_factory):
    post_id = _make_post(
        session_factory, _make_student(session_factory), status=CONTENT_STATUS_HIDDEN
    )

    response = student.post(
        LIKE_URL.format(post_id=post_id), "/feed", {"next_url": "/feed"}
    )

    assert response.status_code == 404
    assert _likes(session_factory) == []


def test_an_unknown_post_cannot_be_liked(student):
    response = student.post(
        LIKE_URL.format(post_id=999), "/feed", {"next_url": "/feed"}
    )

    assert response.status_code == 404


def test_a_like_without_a_csrf_token_is_refused(student, session_factory):
    post_id = _make_post(session_factory, student.student_id)
    student.csrf_for("/feed")  # both cookies in place, only the field missing

    response = student.client.post(
        LIKE_URL.format(post_id=post_id),
        data={"next_url": "/feed"},
        follow_redirects=False,
    )

    assert response.status_code == 403
    assert _likes(session_factory) == []


def test_an_anonymous_visitor_cannot_like(client, session_factory):
    post_id = _make_post(session_factory, _make_student(session_factory))
    token = extract_csrf(client.get("/login").text)

    response = client.post(
        LIKE_URL.format(post_id=post_id),
        data={"csrf_token": token, "next_url": "/feed"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    assert _likes(session_factory) == []


def test_a_banned_student_cannot_like(student, session_factory):
    post_id = _make_post(session_factory, student.student_id)
    with session_factory() as db:
        db.execute(
            update(Student)
            .where(Student.id == student.student_id)
            .values(is_banned=True)
        )
        db.commit()
    # /feed answers 403 for a banned student, so the token comes from a page
    # they can still open (they have to be able to log out).
    token = extract_csrf(student.client.get("/login").text)

    response = student.client.post(
        LIKE_URL.format(post_id=post_id),
        data={"csrf_token": token, "next_url": "/feed"},
        follow_redirects=False,
    )

    assert response.status_code == 403
    assert _likes(session_factory) == []


def test_toggle_like_reports_the_state_it_left(student, session_factory):
    post_id = _make_post(session_factory, student.student_id)

    with session_factory() as db:
        me = db.get(Student, student.student_id)
        post = db.get(Post, post_id)
        assert likes_service.toggle_like(db, me, post) is True
        assert likes_service.toggle_like(db, me, post) is False

    assert _likes(session_factory) == []


def test_toggle_like_survives_a_lost_race(student, session_factory, monkeypatch):
    # The read said "not liked", but by the time the insert runs the row is
    # there (another click of the same student won). The unique constraint
    # refuses the second row and the toggle reports the like as present instead
    # of blowing up in the student's face.
    post_id = _make_post(session_factory, student.student_id)
    with session_factory() as db:
        db.add(PostLike(student_id=student.student_id, post_id=post_id))
        db.commit()

    monkeypatch.setattr(
        "app.services.likes.likes_repo.get_like", lambda db, student_id, post_id: None
    )

    with session_factory() as db:
        me = db.get(Student, student.student_id)
        post = db.get(Post, post_id)
        assert likes_service.toggle_like(db, me, post) is True

    assert len(_likes(session_factory)) == 1


def test_counts_and_liked_ids_are_batched(session_factory, student):
    liked_id = _make_post(session_factory, student.student_id)
    other_id = _make_post(session_factory, _make_student(session_factory))
    with session_factory() as db:
        db.add(PostLike(student_id=student.student_id, post_id=liked_id))
        db.commit()

    with session_factory() as db:
        assert likes_repo.count_likes_by_post(db, [liked_id, other_id]) == {liked_id: 1}
        assert likes_repo.count_likes_by_post(db, []) == {}
        assert likes_repo.liked_post_ids(db, student.student_id, [liked_id, other_id]) == {
            liked_id
        }
        assert likes_repo.liked_post_ids(db, student.student_id, []) == set()
        assert likes_repo.get_like(db, student.student_id, other_id) is None
