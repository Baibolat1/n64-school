"""End-to-end tests of complaints: the form, its refusals and the queue.

Driven through real HTTP requests like the feed's and the teachers' tests, so
they cover the whole path -- session, CSRF, route, service rules, repositories,
rendering -- and then look at what actually landed in the ``reports`` table.
"""

import sys
from pathlib import Path

from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import app  # noqa: E402
from app.models.content import (  # noqa: E402
    CONTENT_STATUS_HIDDEN,
    CONTENT_STATUS_VISIBLE,
    Comment,
    Post,
)
from app.models.report import (  # noqa: E402
    REPORT_REASON_CODES,
    REPORT_STATUS_OPEN,
    Report,
)
from app.models.review import Review  # noqa: E402
from app.models.student import Student  # noqa: E402
from app.models.teacher import Teacher  # noqa: E402
from app.services import reports as reports_service  # noqa: E402

OTHER_ALIAS = "Ученик-02"


def _rows(session_factory, model):
    with session_factory() as db:
        return list(db.execute(select(model)).scalars().all())


def _make_student(session_factory, alias=OTHER_ALIAS) -> int:
    """Another student, so 'someone else's content' means just that."""
    with session_factory() as db:
        student = Student(alias=alias)
        db.add(student)
        db.commit()
        return student.id


def _make_post(
    session_factory,
    author_id,
    *,
    body="Текст поста",
    status=CONTENT_STATUS_VISIBLE,
) -> int:
    with session_factory() as db:
        post = Post(
            author_id=author_id,
            display_alias=OTHER_ALIAS,
            is_anonymous=False,
            body=body,
            status=status,
        )
        db.add(post)
        db.commit()
        return post.id


def _make_comment(
    session_factory,
    post_id,
    author_id,
    *,
    body="Комментарий",
    status=CONTENT_STATUS_VISIBLE,
) -> int:
    with session_factory() as db:
        comment = Comment(
            post_id=post_id,
            author_id=author_id,
            display_alias=OTHER_ALIAS,
            is_anonymous=False,
            body=body,
            status=status,
        )
        db.add(comment)
        db.commit()
        return comment.id


def _make_review(
    session_factory,
    teacher_id,
    author_id,
    *,
    comment="Отзыв",
    status=CONTENT_STATUS_VISIBLE,
) -> int:
    with session_factory() as db:
        review = Review(
            student_id=author_id,
            teacher_id=teacher_id,
            display_alias=OTHER_ALIAS,
            is_anonymous=False,
            comment=comment,
            status=status,
        )
        db.add(review)
        db.commit()
        return review.id


def _make_teacher(session_factory, name="Тестов Учитель") -> int:
    with session_factory() as db:
        teacher = Teacher(display_name=name, subject="Физика", is_active=True)
        db.add(teacher)
        db.commit()
        return teacher.id


def _report(student, target_type, target_id, data, *, token_page=None):
    """POST a complaint, taking the CSRF token from the form it posts to.

    A test that expects the *form* to be refused (own content, hidden target)
    passes ``token_page`` -- a page that does render -- because such a test is
    about the refusal, not about CSRF.
    """
    url = f"/report/{target_type}/{target_id}"
    return student.client.post(
        url,
        data={**data, "csrf_token": student.csrf_for(token_page or url)},
        follow_redirects=False,
    )


# --- Access ------------------------------------------------------------------


def test_anonymous_visitor_is_redirected_to_login(client, session_factory):
    other = _make_student(session_factory)
    post_id = _make_post(session_factory, other)
    for url in (f"/report/post/{post_id}", "/report/post/1"):
        response = client.get(url, follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/login"


def test_banned_student_is_refused(student, session_factory):
    other = _make_student(session_factory)
    post_id = _make_post(session_factory, other)
    with session_factory() as db:
        db.get(Student, student.student_id).is_banned = True
        db.commit()
    assert student.client.get(f"/report/post/{post_id}").status_code == 403


def test_submitting_without_csrf_token_is_refused(student, session_factory):
    other = _make_student(session_factory)
    post_id = _make_post(session_factory, other)
    response = student.client.post(
        f"/report/post/{post_id}",
        data={"reason_code": "spam"},
        follow_redirects=False,
    )
    assert response.status_code == 403
    assert _rows(session_factory, Report) == []


# --- The vocabulary ----------------------------------------------------------


def test_reason_labels_cover_exactly_the_model_codes():
    """The form's wording and the model's codes cannot drift apart."""
    assert tuple(reports_service.REASON_LABELS) == REPORT_REASON_CODES
    assert all(reports_service.REASON_LABELS.values())


def test_form_offers_every_reason(student, session_factory):
    other = _make_student(session_factory)
    post_id = _make_post(session_factory, other)

    response = student.client.get(f"/report/post/{post_id}")

    assert response.status_code == 200
    for code in REPORT_REASON_CODES:
        assert f'value="{code}"' in response.text
        assert reports_service.REASON_LABELS[code] in response.text


# --- What can be complained about --------------------------------------------


def test_unknown_target_type_is_not_found(student, session_factory):
    assert student.client.get("/report/foobar/1").status_code == 404


def test_missing_target_is_not_found(student, session_factory):
    assert student.client.get("/report/post/999").status_code == 404
    assert student.client.get("/report/comment/999").status_code == 404
    assert student.client.get("/report/review/999").status_code == 404


def test_hidden_target_is_not_found(student, session_factory):
    """A hidden post answers 404, not 403: moderation is not observable."""
    other = _make_student(session_factory)
    post_id = _make_post(session_factory, other, status=CONTENT_STATUS_HIDDEN)

    assert student.client.get(f"/report/post/{post_id}").status_code == 404
    response = _report(
        student, "post", post_id, {"reason_code": "spam"}, token_page="/feed"
    )
    assert response.status_code == 404
    assert _rows(session_factory, Report) == []


def test_comment_under_a_hidden_post_is_not_found(student, session_factory):
    other = _make_student(session_factory)
    post_id = _make_post(session_factory, other, status=CONTENT_STATUS_HIDDEN)
    comment_id = _make_comment(session_factory, post_id, other)

    assert student.client.get(f"/report/comment/{comment_id}").status_code == 404


def test_own_content_cannot_be_reported(student, session_factory):
    body = {"reason_code": "spam"}
    mine = _make_post(session_factory, student.student_id)
    assert student.client.get(f"/report/post/{mine}").status_code == 403
    assert (
        _report(student, "post", mine, body, token_page="/feed").status_code == 403
    )

    other_post = _make_post(session_factory, _make_student(session_factory))
    mine_comment = _make_comment(
        session_factory, other_post, student.student_id
    )
    assert student.client.get(f"/report/comment/{mine_comment}").status_code == 403
    assert (
        _report(
            student, "comment", mine_comment, body, token_page="/feed"
        ).status_code
        == 403
    )

    teacher_id = _make_teacher(session_factory)
    mine_review = _make_review(session_factory, teacher_id, student.student_id)
    assert student.client.get(f"/report/review/{mine_review}").status_code == 403
    assert (
        _report(
            student, "review", mine_review, body, token_page="/feed"
        ).status_code
        == 403
    )
    assert _rows(session_factory, Report) == []


# --- Filing a complaint ------------------------------------------------------


def test_post_complaint_is_recorded_as_open(student, session_factory):
    other = _make_student(session_factory)
    post_id = _make_post(session_factory, other)

    response = _report(
        student, "post", post_id, {"reason_code": "abuse", "comment": "  Травля  "}
    )

    assert response.status_code == 200
    assert "Жалоба отправлена" in response.text
    assert f'href="/feed/posts/{post_id}"' in response.text

    reports = _rows(session_factory, Report)
    assert len(reports) == 1
    report = reports[0]
    assert report.reporter_id == student.student_id
    assert report.target_type == "post"
    assert report.target_id == post_id
    assert report.reason_code == "abuse"
    assert report.comment == "Травля"  # trimmed, stored without the padding
    assert report.status == REPORT_STATUS_OPEN
    assert report.resolved_by is None
    assert report.resolved_at is None


def test_comment_complaint_returns_to_the_post(student, session_factory):
    other = _make_student(session_factory)
    post_id = _make_post(session_factory, other)
    comment_id = _make_comment(session_factory, post_id, other)

    response = _report(student, "comment", comment_id, {"reason_code": "spam"})

    assert response.status_code == 200
    assert f'href="/feed/posts/{post_id}"' in response.text
    assert _rows(session_factory, Report)[0].target_type == "comment"


def test_review_complaint_returns_to_the_teacher(student, session_factory):
    teacher_id = _make_teacher(session_factory)
    other = _make_student(session_factory)
    review_id = _make_review(session_factory, teacher_id, other)

    response = _report(student, "review", review_id, {"reason_code": "offtopic"})

    assert response.status_code == 200
    assert f'href="/teachers/{teacher_id}"' in response.text
    assert _rows(session_factory, Report)[0].target_type == "review"


def test_comment_is_stored_as_null_when_left_empty(student, session_factory):
    other = _make_student(session_factory)
    post_id = _make_post(session_factory, other)

    _report(student, "post", post_id, {"reason_code": "other", "comment": "   "})

    assert _rows(session_factory, Report)[0].comment is None


# --- Refusals the student can fix -------------------------------------------


def test_second_complaint_about_the_same_target_is_refused(
    student, session_factory
):
    other = _make_student(session_factory)
    post_id = _make_post(session_factory, other)

    assert (
        _report(student, "post", post_id, {"reason_code": "spam"}).status_code == 200
    )
    second = _report(student, "post", post_id, {"reason_code": "abuse"})

    assert second.status_code == 200
    assert "уже жаловались" in second.text
    assert len(_rows(session_factory, Report)) == 1
    # The first complaint is untouched by the refused second one.
    assert _rows(session_factory, Report)[0].reason_code == "spam"


def test_another_students_complaint_about_the_same_target_is_allowed(
    student, session_factory
):
    """The refusal is per student, not per target."""
    other = _make_student(session_factory)
    post_id = _make_post(session_factory, other)
    _report(student, "post", post_id, {"reason_code": "spam"})

    with session_factory() as db:
        db.add(
            Report(
                reporter_id=other,
                target_type="post",
                target_id=post_id,
                reason_code="abuse",
                status=REPORT_STATUS_OPEN,
            )
        )
        db.commit()

    assert len(_rows(session_factory, Report)) == 2


def test_unknown_reason_is_refused(student, session_factory):
    other = _make_student(session_factory)
    post_id = _make_post(session_factory, other)

    response = _report(student, "post", post_id, {"reason_code": "nonsense"})

    assert response.status_code == 200
    assert "Выберите причину" in response.text
    assert _rows(session_factory, Report) == []


def test_missing_reason_is_refused(student, session_factory):
    other = _make_student(session_factory)
    post_id = _make_post(session_factory, other)

    response = _report(student, "post", post_id, {})

    assert response.status_code == 200
    assert "Выберите причину" in response.text
    assert _rows(session_factory, Report) == []


def test_overlong_comment_is_refused(student, session_factory):
    other = _make_student(session_factory)
    post_id = _make_post(session_factory, other)
    too_long = "я" * (reports_service.MAX_REPORT_COMMENT_LENGTH + 1)

    response = _report(
        student, "post", post_id, {"reason_code": "other", "comment": too_long}
    )

    assert response.status_code == 200
    assert "максимум" in response.text
    assert _rows(session_factory, Report) == []


# --- The button on the page --------------------------------------------------


def test_feed_offers_the_button_on_someone_elses_post_only(
    student, session_factory
):
    other = _make_student(session_factory)
    theirs = _make_post(session_factory, other, body="Чужой пост")
    mine = _make_post(session_factory, student.student_id, body="Мой пост")

    response = student.client.get("/feed")

    assert response.status_code == 200
    assert f'href="/report/post/{theirs}"' in response.text
    assert f'href="/report/post/{mine}"' not in response.text


def test_post_page_offers_the_button_on_someone_elses_comment(
    student, session_factory
):
    other = _make_student(session_factory)
    post_id = _make_post(session_factory, other)
    theirs = _make_comment(session_factory, post_id, other)
    mine = _make_comment(session_factory, post_id, student.student_id)

    response = student.client.get(f"/feed/posts/{post_id}")

    assert response.status_code == 200
    assert f'href="/report/comment/{theirs}"' in response.text
    assert f'href="/report/comment/{mine}"' not in response.text
    # The post itself is someone else's too.
    assert f'href="/report/post/{post_id}"' in response.text


def test_teacher_page_offers_the_button_on_someone_elses_review(
    student, session_factory
):
    teacher_id = _make_teacher(session_factory)
    other = _make_student(session_factory)
    theirs = _make_review(session_factory, teacher_id, other)
    mine = _make_review(session_factory, teacher_id, student.student_id)

    response = student.client.get(f"/teachers/{teacher_id}")

    assert response.status_code == 200
    assert f'href="/report/review/{theirs}"' in response.text
    assert f'href="/report/review/{mine}"' not in response.text


def test_a_hidden_review_is_not_offered(student, session_factory):
    teacher_id = _make_teacher(session_factory)
    other = _make_student(session_factory)
    _make_review(session_factory, teacher_id, other, status=CONTENT_STATUS_HIDDEN)

    response = student.client.get(f"/teachers/{teacher_id}")

    assert response.status_code == 200
    assert "/report/review/" not in response.text


def test_route_is_mounted_under_the_report_prefix(student):
    """A smoke test that the router is actually wired into the app."""
    assert app.url_path_for("report_form", target_type="post", target_id=1) == (
        "/report/post/1"
    )
