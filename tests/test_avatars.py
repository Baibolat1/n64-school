"""The avatar gallery: its keys, its URLs and the anonymity guard.

Pure presentation helpers plus one check that a freshly created student starts
without a picture, so a migration that forgets the column (or a default that
picks one) cannot slip through.
"""

import re
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402
from app.core import avatars  # noqa: E402
from app.core.templates import templates  # noqa: E402
from app.main import app  # noqa: E402
from app.models.content import Post  # noqa: E402
from app.models.student import Student  # noqa: E402
from app.repositories import students as students_repo  # noqa: E402
from app.services import publication  # noqa: E402
from app.services import student_auth  # noqa: E402
from app.services.avatars import AvatarError, set_avatar  # noqa: E402
from tests.conftest import FormSession, extract_csrf  # noqa: E402
from tests.test_admin_queues import (  # noqa: E402
    _make_blocked,
    _make_flag,
    _make_post,
    _make_report,
    _make_student,
)
from tests.test_teachers import _make_teacher, _seed_criteria, _submit  # noqa: E402


def test_avatar_keys_match_the_files_on_disk():
    on_disk = {p.stem for p in avatars.AVATAR_DIR.glob("*.svg")}
    assert set(avatars.AVATAR_KEYS) == on_disk


def test_anonymous_label_matches_publication():
    # The two live in different layers, so nothing but this test keeps them
    # equal; a drift would let an anonymous record keep a personal picture.
    assert avatars.ANONYMOUS_LABEL == publication.ANONYMOUS_ALIAS


def test_avatar_url_rejects_anything_outside_the_gallery():
    assert avatars.avatar_url(None) is None
    assert avatars.avatar_url("nope") is None
    assert avatars.avatar_url("../circle") is None
    assert avatars.avatar_url("circle/../../etc/passwd") is None
    assert avatars.avatar_url("") is None
    assert avatars.avatar_url("circle") == "/static/img/avatars/circle.svg"


def test_anonymous_content_never_gets_a_picture():
    assert avatars.avatar_image_key("Аноним", "circle") is None
    assert avatars.avatar_image_key("Кот", None) is None
    assert avatars.avatar_image_key("Кот", "../x") is None
    assert avatars.avatar_image_key("Кот", "circle") == "circle"


def test_a_new_student_has_no_avatar(session_factory):
    with session_factory() as db:
        student = Student(alias="Ученик-Аватар")
        db.add(student)
        db.commit()
        student_id = student.id

    with session_factory() as db:
        stored = db.execute(
            select(Student).where(Student.id == student_id)
        ).scalar_one()
        assert stored.avatar_key is None


def _render_avatar(pseudonym, size, key):
    template = templates.env.from_string(
        '{% import "_macros.html" as ui %}{{ ui.avatar(pseudonym, size, key) }}'
    )
    return template.render(pseudonym=pseudonym, size=size, key=key)


def test_the_macro_draws_a_picture_only_for_a_permitted_record():
    # The guard is inside the macro, so even a caller that passes a key must not
    # get a picture out of an anonymous record.
    anonymous = _render_avatar("Аноним", "sm", "circle")
    assert 'class="avatar avatar--sm"' in anonymous
    assert "/static/img/avatars/" not in anonymous

    permitted = _render_avatar("Кот", "sm", "circle")
    assert "/static/img/avatars/circle.svg" in permitted

    # No key at all is the ordinary student: the drawn circle stays.
    no_picture = _render_avatar("Кот", "sm", None)
    assert "/static/img/avatars/" not in no_picture
    assert "data-color=" in no_picture

    # A crafted key never reaches the URL (avatar_url is the single gate).
    traversal = _render_avatar("Кот", "sm", "../x")
    assert "/static/img/avatars/" not in traversal


def test_the_stylesheet_ties_the_markup_together():
    css = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "static"
        / "css"
        / "components.css"
    ).read_text(encoding="utf-8")
    assert ".avatar--img" in css
    assert "object-fit: cover" in css
    assert ".avatar-picker" in css
    assert ".avatar-option" in css
    assert "input:checked + .avatar" in css


def test_avatar_options_list_the_whole_gallery_in_order():
    # Keys only: the URL is built by the macro, which must not be bypassed.
    assert list(avatars.avatar_options()) == list(avatars.AVATAR_KEYS)


def test_avatar_keys_by_id_maps_only_the_asked_for_students(session_factory):
    with session_factory() as db:
        first = Student(alias="Ученик-Аватар-1", avatar_key="circle")
        second = Student(alias="Ученик-Аватар-2", avatar_key=None)
        db.add_all([first, second])
        db.commit()
        first_id, second_id = first.id, second.id

    with session_factory() as db:
        keys = students_repo.avatar_keys_by_id(db, [first_id, second_id, 999999])
    assert keys == {first_id: "circle", second_id: None}
    assert 999999 not in keys


def test_avatar_keys_by_id_handles_an_empty_and_a_duplicated_list(session_factory):
    with session_factory() as db:
        student = Student(alias="Ученик-Аватар-3", avatar_key="ring")
        db.add(student)
        db.commit()
        student_id = student.id

    with session_factory() as db:
        assert students_repo.avatar_keys_by_id(db, []) == {}
        assert students_repo.avatar_keys_by_id(db, [student_id, student_id]) == {
            student_id: "ring"
        }


def test_set_avatar_stores_a_gallery_key(session_factory):
    with session_factory() as db:
        student = Student(alias="Ученик-Аватар-4")
        db.add(student)
        db.commit()
        student_id = student.id
        result = set_avatar(db, student, "circle")

    assert result.ok
    assert result.key == "circle"

    # A fresh session sees the value only because the service committed it.
    with session_factory() as db:
        stored = db.get(Student, student_id)
        assert stored.avatar_key == "circle"


def test_set_avatar_clears_the_picture_on_an_empty_value(session_factory):
    with session_factory() as db:
        student = Student(alias="Ученик-Аватар-5", avatar_key="circle")
        db.add(student)
        db.commit()
        student_id = student.id
        result = set_avatar(db, student, "")

    assert result.ok
    assert result.key is None

    with session_factory() as db:
        stored = db.get(Student, student_id)
        assert stored.avatar_key is None


@pytest.mark.parametrize(
    "bad_key", ["nope", "../circle", "circle/../../etc/passwd", " circle.svg "]
)
def test_set_avatar_refuses_a_key_outside_the_gallery(session_factory, bad_key):
    with session_factory() as db:
        student = Student(alias="Ученик-Аватар-6", avatar_key="moon")
        db.add(student)
        db.commit()
        student_id = student.id
        result = set_avatar(db, student, bad_key)

    assert not result.ok
    assert result.reason is AvatarError.INVALID_KEY

    # Nothing was written, so the previous picture is still the one on record.
    with session_factory() as db:
        stored = db.get(Student, student_id)
        assert stored.avatar_key == "moon"


def test_set_avatar_trims_surrounding_whitespace(session_factory):
    with session_factory() as db:
        student = Student(alias="Ученик-Аватар-7")
        db.add(student)
        db.commit()
        result = set_avatar(db, student, "  circle  ")

    assert result.ok
    assert result.key == "circle"


def test_every_gallery_key_has_its_own_label():
    # The labels give each radio an accessible name (the picture is
    # aria-hidden), so every key must have one and no two may read the same.
    assert set(avatars.AVATAR_LABELS) == set(avatars.AVATAR_KEYS)
    labels = list(avatars.AVATAR_LABELS.values())
    assert all(label.strip() for label in labels)
    assert len(set(labels)) == 12

    assert avatars.avatar_label("circle") == avatars.AVATAR_LABELS["circle"]
    # An unknown key is echoed, never a crash.
    assert avatars.avatar_label("нет-такого") == "нет-такого"


# --- The picker on /me and the POST behind it -------------------------------


def _page_header(html: str) -> str:
    """The identity header of /me, where the student's own picture is shown."""
    match = re.search(r'<div class="page-header">(.*?)</div>', html, re.S)
    assert match, "the page rendered no header"
    return match.group(1)


def test_the_me_page_offers_the_whole_gallery(student):
    response = student.client.get("/me")
    assert response.status_code == 200
    html = response.text

    assert 'name="csrf_token"' in html

    values = re.findall(r'name="avatar_key" value="([^"]*)"', html)
    assert len([value for value in values if value]) == 12
    assert values.count("") == 1

    for key in avatars.AVATAR_KEYS:
        assert f"/static/img/avatars/{key}.svg" in html


def test_choosing_a_picture_shows_it_on_me(student, session_factory):
    response = student.post("/me/avatar", "/me", {"avatar_key": "circle"})
    assert response.status_code == 303
    assert response.headers["location"] == "/me"

    page = student.client.get("/me")
    assert "/static/img/avatars/circle.svg" in _page_header(page.text)

    # A fresh session sees the value only because the service committed it.
    with session_factory() as db:
        stored = db.get(Student, student.student_id)
        assert stored.avatar_key == "circle"


def test_dropping_the_picture_brings_the_initial_back(student):
    student.post("/me/avatar", "/me", {"avatar_key": "circle"})
    response = student.post("/me/avatar", "/me", {"avatar_key": ""})
    assert response.status_code == 303

    # Only the identity header is checked: the picker below always renders every
    # gallery picture, so the whole page can never be free of
    # ``/static/img/avatars/``. The header is where the student's own choice
    # shows, and there the drawn initial must be back.
    header = _page_header(student.client.get("/me").text)
    assert "/static/img/avatars/" not in header
    assert "data-color=" in header


@pytest.mark.parametrize("bad_key", ["nope", "../circle"])
def test_an_unknown_key_is_refused_and_changes_nothing(
    student, session_factory, bad_key
):
    response = student.post("/me/avatar", "/me", {"avatar_key": bad_key})
    assert response.status_code == 200
    assert "Такой картинки нет в галерее" in response.text

    # Nothing was written: a student testing the form never set a picture.
    with session_factory() as db:
        stored = db.get(Student, student.student_id)
        assert stored.avatar_key is None


def test_the_avatar_route_is_closed_to_an_anonymous_visitor(client, session_factory):
    with session_factory() as db:
        student = Student(alias="Ученик-Аватар-8", avatar_key="moon")
        db.add(student)
        db.commit()
        student_id = student.id

    token = extract_csrf(client.get("/login").text)
    response = client.post(
        "/me/avatar",
        data={"avatar_key": "circle", "csrf_token": token},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/login"

    # Nobody changed the picture: the row the anonymous request could not reach
    # still holds what it held before.
    with session_factory() as db:
        assert db.get(Student, student_id).avatar_key == "moon"


# --- The author's current picture in the feed, comments and reviews ----------


def _student_with_a_picture(session_factory, alias, avatar_key):
    """A second logged-in client for a freshly created student with a picture."""
    with session_factory() as db:
        student = Student(alias=alias, avatar_key=avatar_key)
        db.add(student)
        db.commit()
        token = student_auth.issue_student_session(student)
    other = TestClient(app)
    other.cookies.set(settings.STUDENT_COOKIE_NAME, token, domain="testserver.local")
    return FormSession(client=other)


def test_a_non_anonymous_post_shows_the_author_picture(student):
    student.post("/me/avatar", "/me", {"avatar_key": "circle"})
    assert student.post(
        "/feed/posts", "/feed", {"body": "Привет всем"}
    ).status_code == 303

    response = student.client.get("/feed")
    assert response.status_code == 200
    assert "/static/img/avatars/circle.svg" in response.text


def test_an_anonymous_post_never_shows_a_picture(student):
    student.post("/me/avatar", "/me", {"avatar_key": "circle"})
    assert student.post(
        "/feed/posts", "/feed", {"body": "Привет всем", "is_anonymous": "true"}
    ).status_code == 303

    response = student.client.get("/feed")
    assert response.status_code == 200
    # The only card on the page is the anonymous one, and the feed's chrome draws
    # no picture of the viewer: a gallery URL here would de-anonymise the author.
    assert "/static/img/avatars/" not in response.text
    assert '<span class="post-author">Аноним</span>' in response.text


def test_the_picture_is_always_the_current_one(student):
    student.post("/me/avatar", "/me", {"avatar_key": "circle"})
    assert student.post("/feed/posts", "/feed", {"body": "Пост"}).status_code == 303

    # The picture is read on every render, not snapshotted with the post: a
    # change after publishing is the one the feed shows.
    student.post("/me/avatar", "/me", {"avatar_key": "moon"})
    page = student.client.get("/feed").text
    assert "/static/img/avatars/moon.svg" in page
    assert "/static/img/avatars/circle.svg" not in page


def test_comment_avatars_follow_the_same_rule(student, session_factory):
    # The author of both posts carries `leaf`, the commenter carries `star`.
    student.post("/me/avatar", "/me", {"avatar_key": "leaf"})
    assert student.post(
        "/feed/posts", "/feed", {"body": "Первый пост"}
    ).status_code == 303
    assert student.post(
        "/feed/posts", "/feed", {"body": "Второй пост"}
    ).status_code == 303

    commenter = _student_with_a_picture(session_factory, "Ученик-Комментатор", "star")
    with session_factory() as db:
        first_id, second_id = [
            post.id for post in db.execute(select(Post).order_by(Post.id)).scalars()
        ]

    assert commenter.post(
        f"/feed/posts/{first_id}/comments",
        f"/feed/posts/{first_id}",
        {"body": "Обычный комментарий"},
    ).status_code == 303
    page = student.client.get(f"/feed/posts/{first_id}").text
    assert "/static/img/avatars/leaf.svg" in page  # the post's author
    assert "/static/img/avatars/star.svg" in page  # the signed commenter

    # A second post isolates the anonymous comment: it is the only one on that
    # page, so its author's picture must be nowhere on it.
    assert commenter.post(
        f"/feed/posts/{second_id}/comments",
        f"/feed/posts/{second_id}",
        {"body": "Анонимный комментарий", "is_anonymous": "true"},
    ).status_code == 303
    page = student.client.get(f"/feed/posts/{second_id}").text
    assert "/static/img/avatars/leaf.svg" in page
    assert "/static/img/avatars/star.svg" not in page


def test_review_avatars_are_shown_for_the_author(student, session_factory):
    _seed_criteria(session_factory)
    teacher_id = _make_teacher(session_factory)

    student.post("/me/avatar", "/me", {"avatar_key": "peaks"})
    assert _submit(student, teacher_id, comment="Хороший учитель").status_code == 303

    # An anonymous review from a student who does have a picture: the picture
    # must not appear next to "Аноним".
    anonymous = _student_with_a_picture(session_factory, "Ученик-Скрытый", "ring")
    assert _submit(
        anonymous, teacher_id, comment="Тоже нравится", is_anonymous=True
    ).status_code == 303

    page = student.client.get(f"/teachers/{teacher_id}").text
    assert "/static/img/avatars/peaks.svg" in page     # the named reviewer
    assert "/static/img/avatars/ring.svg" not in page  # the anonymous one


def test_teachers_keep_their_initial(student, session_factory):
    # Teachers have no accounts and no gallery picture: only the drawn initial.
    # The viewer has one, so the check cannot pass by accident on a page that
    # simply draws nobody's picture.
    student.post("/me/avatar", "/me", {"avatar_key": "petals"})
    teacher_id = _make_teacher(session_factory)
    for url in ("/teachers", f"/teachers/{teacher_id}"):
        response = student.client.get(url)
        assert response.status_code == 200
        assert '<span class="avatar' in response.text
        assert "/static/img/avatars/" not in response.text


def test_a_stale_key_in_the_database_does_not_break_the_feed(student, session_factory):
    # A key that never went through the picker (a hand-edited row) must still be
    # harmless: the guard lives in `avatar_url`, not only in the form's check.
    with session_factory() as db:
        db.get(Student, student.student_id).avatar_key = "../../etc/passwd"
        db.commit()

    assert student.post(
        "/feed/posts", "/feed", {"body": "Пост с мусорным ключом"}
    ).status_code == 303
    response = student.client.get("/feed")
    assert response.status_code == 200
    assert "/static/img/avatars/" not in response.text
    assert "Пост с мусорным ключом" in response.text


# --- The picture in the admin: only the student card -------------------------


def test_the_admin_student_card_shows_the_picture(admin, session_factory):
    with session_factory() as db:
        student = Student(alias="Ученик-Админ-Картинка", avatar_key="spiral")
        db.add(student)
        db.commit()
        student_id = student.id

    response = admin.client.get(f"/admin/students/{student_id}")
    assert response.status_code == 200
    assert "/static/img/avatars/spiral.svg" in response.text


def test_the_moderation_queues_stay_text_only(admin, session_factory):
    # The decision is that a picture shows in the admin student card and nowhere
    # else in the panel: every queue lists students by their pseudonym, and a
    # picture there would add a personal signal the text-only rows deliberately
    # do not carry. Each queue is fed one object so none is skipped as empty.
    with session_factory() as db:
        author = Student(alias="Ученик-Админ-Очереди", avatar_key="waves")
        db.add(author)
        db.commit()
        author_id = author.id

    post_id = _make_post(session_factory, author_id, "Ученик-Админ-Очереди")
    _make_report(
        session_factory, _make_student(session_factory, "Ученик-Жалобщик"), "post", post_id
    )
    _make_flag(session_factory, "post", post_id, rule_code="phone")
    _make_blocked(session_factory, author_id, "post", "phone")

    for url in ("/admin/reports", "/admin/pii", "/admin/blocked"):
        response = admin.client.get(url)
        assert response.status_code == 200
        assert "/static/img/avatars/" not in response.text


# The picker's checked state and the route's refusals. The first check is the
# one the markup depends on for showing the choice at all; the rest are the
# edges a hand-crafted request can reach.


def test_the_current_choice_is_the_marked_one(student, session_factory):
    student.post("/me/avatar", "/me", {"avatar_key": "moon"})

    page = student.client.get("/me").text
    assert 'name="avatar_key" value="moon" checked' in page
    assert 'name="avatar_key" value="" checked' not in page

    student.post("/me/avatar", "/me", {"avatar_key": ""})

    page = student.client.get("/me").text
    assert 'name="avatar_key" value="" checked' in page
    assert 'name="avatar_key" value="moon" checked' not in page


def test_a_missing_field_clears_the_picture(student):
    # The field is declared with a default of "", so a form that sends nothing
    # clears the picture instead of answering with the framework's 422.
    student.post("/me/avatar", "/me", {"avatar_key": "leaf"})
    response = student.post("/me/avatar", "/me", {})

    assert response.status_code == 303
    header = _page_header(student.client.get("/me").text)
    assert "/static/img/avatars/" not in header
    assert "data-color=" in header


def test_a_banned_student_cannot_change_the_picture(student, session_factory):
    student.post("/me/avatar", "/me", {"avatar_key": "ring"})

    with session_factory() as db:
        db.get(Student, student.student_id).is_banned = True
        db.commit()

    # The token comes from a page a banned student can still load: /me answers
    # with the 403 page, so the helper's usual "visit the page first" would fail
    # before the request this test is about is even sent.
    token = extract_csrf(student.client.get("/login").text)
    response = student.client.post(
        "/me/avatar",
        data={"avatar_key": "peaks", "csrf_token": token},
        follow_redirects=False,
    )

    assert response.status_code == 403
    # The ban page is what came back, and the stored picture is untouched.
    with session_factory() as db:
        assert db.get(Student, student.student_id).avatar_key == "ring"


def test_the_avatar_route_takes_the_student_from_the_session(client, session_factory):
    # No id is read from the form, so a crafted request cannot repaint someone
    # else's record: only the session decides whose picture changes.
    with session_factory() as db:
        victim = Student(alias="Ученик-Чужая-Картинка", avatar_key="grid")
        db.add(victim)
        db.commit()
        victim_id = victim.id

    token = extract_csrf(client.get("/login").text)
    client.post(
        "/me/avatar",
        data={"avatar_key": "dawn", "student_id": str(victim_id), "csrf_token": token},
        follow_redirects=False,
    )

    with session_factory() as db:
        assert db.get(Student, victim_id).avatar_key == "grid"
