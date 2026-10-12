"""End-to-end tests of teacher reviews: the list, a teacher's page and the form.

Driven through real HTTP requests like the feed's tests, so they cover the whole
path -- session, CSRF, route, service gates, repositories, rendering -- and then
look at what actually landed in the tables.
"""

import sys
from pathlib import Path

from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402
from app.models.audit import PiiBlockedAttempt, PiiFlag  # noqa: E402
from app.models.content import CONTENT_STATUS_VISIBLE  # noqa: E402
from app.models.review import Review, ReviewScore  # noqa: E402
from app.models.student import Student  # noqa: E402
from app.models.teacher import Criterion, Teacher  # noqa: E402
from app.services.pii_filter import PLACEHOLDER  # noqa: E402

CRITERIA = (
    # code, label, scale_min, scale_max, position
    ("explains", "Понятно объясняет", 1, 5, 10),
    ("fair", "Справедливо оценивает", 1, 5, 20),
)


def _rows(session_factory, model):
    with session_factory() as db:
        return list(db.execute(select(model)).scalars().all())


def _seed_criteria(session_factory):
    with session_factory() as db:
        for code, label, low, high, position in CRITERIA:
            db.add(
                Criterion(
                    code=code,
                    label=label,
                    scale_min=low,
                    scale_max=high,
                    position=position,
                )
            )
        db.commit()


def _make_teacher(session_factory, name="Тестов Учитель", *, subject="Физика",
                  is_active=True):
    with session_factory() as db:
        teacher = Teacher(display_name=name, subject=subject, is_active=is_active)
        db.add(teacher)
        db.commit()
        return teacher.id


def _criterion_ids(session_factory):
    return {c.code: c.id for c in _rows(session_factory, Criterion)}


def _make_review(session_factory, *, teacher_id, scores, comment="Норм",
                 alias="Ученик-02", status=CONTENT_STATUS_VISIBLE):
    """Write a review straight through the ORM, as another student's would be."""
    with session_factory() as db:
        student = Student(alias=alias)
        db.add(student)
        db.flush()
        review = Review(
            student_id=student.id,
            teacher_id=teacher_id,
            display_alias=alias,
            is_anonymous=False,
            comment=comment,
            status=status,
        )
        db.add(review)
        db.flush()
        for criterion_id, score in scores.items():
            db.add(
                ReviewScore(
                    review_id=review.id, criterion_id=criterion_id, score=score
                )
            )
        db.commit()
        return review.id


def _submit(student, teacher_id, *, comment="Хороший учитель", scores=(5, 5),
            is_anonymous=None, extra=None):
    """POST the review form, filling one score per criterion by default.

    ``scores`` runs parallel to ``CRITERIA``; a None entry leaves that criterion
    unanswered, which is how the "missing score" refusal is reached.
    """
    data = {"comment": comment}
    for (code, _, _, _, _), score in zip(CRITERIA, scores):
        if score is not None:
            data[f"score_{code}"] = str(score)
    if is_anonymous is not None:
        data["is_anonymous"] = "true" if is_anonymous else "false"
    if extra:
        data.update(extra)
    return student.post(
        f"/teachers/{teacher_id}/reviews", f"/teachers/{teacher_id}", data
    )


def test_anonymous_visitor_is_redirected_to_login(client, session_factory):
    teacher_id = _make_teacher(session_factory)
    for url in ("/teachers", f"/teachers/{teacher_id}"):
        response = client.get(url, follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/login"


def test_banned_student_is_refused(student, session_factory):
    teacher_id = _make_teacher(session_factory)
    with session_factory() as db:
        db.get(Student, student.student_id).is_banned = True
        db.commit()
    response = student.client.get("/teachers")
    assert response.status_code == 403


def test_list_shows_active_teachers_only(student, session_factory):
    _make_teacher(session_factory, "Активный Учитель", subject="Химия")
    _make_teacher(session_factory, "Отключённый Учитель", is_active=False)

    response = student.client.get("/teachers")
    assert response.status_code == 200
    assert "Активный Учитель" in response.text
    assert "Отключённый Учитель" not in response.text


def test_inactive_teacher_page_is_not_found(student, session_factory):
    teacher_id = _make_teacher(session_factory, "Отключённый", is_active=False)
    assert student.client.get(f"/teachers/{teacher_id}").status_code == 404


def test_unknown_teacher_page_is_not_found(student):
    assert student.client.get("/teachers/999").status_code == 404


def test_form_fields_come_from_the_criteria(student, session_factory):
    # Which questions exist is data, so the form has to be generated from the
    # criteria rows -- one score field per criterion code.
    _seed_criteria(session_factory)
    teacher_id = _make_teacher(session_factory)

    response = student.client.get(f"/teachers/{teacher_id}")
    assert response.status_code == 200
    assert 'name="score_explains"' in response.text
    assert 'name="score_fair"' in response.text
    assert "Понятно объясняет" in response.text


def test_page_without_criteria_offers_no_form(student, session_factory):
    teacher_id = _make_teacher(session_factory)
    response = student.client.get(f"/teachers/{teacher_id}")
    assert response.status_code == 200
    assert "Критерии оценки ещё не заданы" in response.text
    assert "<form" not in response.text


def test_clean_review_is_saved_with_scores(student, session_factory):
    _seed_criteria(session_factory)
    teacher_id = _make_teacher(session_factory)

    response = _submit(student, teacher_id, comment="Объясняет понятно")
    assert response.status_code == 303
    assert response.headers["location"] == f"/teachers/{teacher_id}"

    reviews = _rows(session_factory, Review)
    assert len(reviews) == 1
    assert reviews[0].comment == "Объясняет понятно"
    assert reviews[0].status == CONTENT_STATUS_VISIBLE
    # The alias is snapshotted, as on posts and comments.
    assert reviews[0].display_alias == "Ученик-01"
    assert reviews[0].is_anonymous is False

    scores = {(s.criterion_id, s.score) for s in _rows(session_factory, ReviewScore)}
    ids = _criterion_ids(session_factory)
    assert scores == {(ids["explains"], 5), (ids["fair"], 5)}
    assert _rows(session_factory, PiiFlag) == []


def test_second_submit_replaces_the_first(student, session_factory):
    _seed_criteria(session_factory)
    teacher_id = _make_teacher(session_factory)

    assert _submit(student, teacher_id, comment="Сначала так").status_code == 303
    response = _submit(student, teacher_id, comment="Передумал", scores=(3, 4))
    assert response.status_code == 303

    reviews = _rows(session_factory, Review)
    assert len(reviews) == 1  # one review per student and teacher, ever
    assert reviews[0].comment == "Передумал"

    ids = _criterion_ids(session_factory)
    scores = {(s.criterion_id, s.score) for s in _rows(session_factory, ReviewScore)}
    assert scores == {(ids["explains"], 3), (ids["fair"], 4)}


def test_anonymous_review_stores_the_anonymous_snapshot(student, session_factory):
    _seed_criteria(session_factory)
    teacher_id = _make_teacher(session_factory)

    assert _submit(student, teacher_id, is_anonymous=True).status_code == 303
    review = _rows(session_factory, Review)[0]
    assert review.is_anonymous is True
    assert review.display_alias == "Аноним"

    page = student.client.get(f"/teachers/{teacher_id}").text
    # The review is signed "Аноним"; the student's own alias only appears in the
    # top bar, never next to this review.
    assert '<span class="post-author">Аноним</span>' in page
    assert '<span class="post-author">Ученик-01</span>' not in page


def test_missing_score_is_refused(student, session_factory):
    _seed_criteria(session_factory)
    teacher_id = _make_teacher(session_factory)

    response = _submit(student, teacher_id, scores=(5, None))
    assert response.status_code == 200
    assert "Поставьте оценку по каждому критерию" in response.text
    assert _rows(session_factory, Review) == []
    # The text the student typed survives the refusal.
    assert "Хороший учитель" in response.text


def test_score_out_of_range_is_refused(student, session_factory):
    _seed_criteria(session_factory)
    teacher_id = _make_teacher(session_factory)

    response = _submit(student, teacher_id, scores=(9, 5))
    assert response.status_code == 200
    assert "Оценка вне допустимого диапазона" in response.text
    assert _rows(session_factory, Review) == []


def test_unparseable_score_counts_as_no_answer(student, session_factory):
    _seed_criteria(session_factory)
    teacher_id = _make_teacher(session_factory)

    response = _submit(student, teacher_id, scores=("abc", 5))
    assert response.status_code == 200
    assert "Поставьте оценку по каждому критерию" in response.text
    assert _rows(session_factory, Review) == []


def test_empty_comment_is_refused(student, session_factory):
    _seed_criteria(session_factory)
    teacher_id = _make_teacher(session_factory)

    response = _submit(student, teacher_id, comment="   ")
    assert response.status_code == 200
    assert "не может быть пустым" in response.text
    assert _rows(session_factory, Review) == []


def test_soft_pii_is_redacted_and_queued(student, session_factory):
    _seed_criteria(session_factory)
    teacher_id = _make_teacher(session_factory)

    response = _submit(student, teacher_id, comment="Это Петр Иванович, он классный")
    assert response.status_code == 303

    review = _rows(session_factory, Review)[0]
    assert review.comment == f"Это {PLACEHOLDER}, он классный"

    flags = _rows(session_factory, PiiFlag)
    assert len(flags) == 1
    assert flags[0].target_type == "review"
    assert flags[0].target_id == review.id


def test_hard_pii_is_refused_and_only_the_fact_is_stored(student, session_factory):
    _seed_criteria(session_factory)
    teacher_id = _make_teacher(session_factory)

    response = _submit(student, teacher_id, comment="Звони +7 912 345-67-89")
    assert response.status_code == 200
    assert "нельзя публиковать" in response.text
    assert _rows(session_factory, Review) == []

    attempts = _rows(session_factory, PiiBlockedAttempt)
    assert len(attempts) == 1
    assert attempts[0].student_id == student.student_id
    assert attempts[0].target_type == "review"
    assert attempts[0].rule_codes == "phone"


def test_rate_limit_refuses_after_the_budget(student, session_factory, monkeypatch):
    _seed_criteria(session_factory)
    teacher_id = _make_teacher(session_factory)
    monkeypatch.setattr(settings, "RATE_LIMIT_REVIEWS", 2)

    assert _submit(student, teacher_id, comment="раз").status_code == 303
    assert _submit(student, teacher_id, comment="два").status_code == 303

    refused = _submit(student, teacher_id, comment="три")
    assert refused.status_code == 429
    assert 3599 <= int(refused.headers["retry-after"]) <= 3600


def test_rejected_form_does_not_spend_the_budget(student, session_factory):
    # Scores are checked before the limiter, like the feed's shape validation:
    # a fumbled radio button must not lock the student out of their own review.
    _seed_criteria(session_factory)
    teacher_id = _make_teacher(session_factory)
    for _ in range(settings.RATE_LIMIT_REVIEWS + 5):
        assert _submit(student, teacher_id, scores=(9, 5)).status_code == 200
    assert _submit(student, teacher_id).status_code == 303


def test_csrf_token_is_required(student, session_factory):
    _seed_criteria(session_factory)
    teacher_id = _make_teacher(session_factory)

    response = student.client.post(
        f"/teachers/{teacher_id}/reviews",
        data={"comment": "Без токена", "score_explains": "5", "score_fair": "5"},
        follow_redirects=False,
    )
    assert response.status_code == 403
    assert _rows(session_factory, Review) == []


def test_averages_ignore_hidden_reviews(student, session_factory):
    # Stage 10 in one assertion: a review taken off the page must not weigh on
    # the teacher's score either.
    _seed_criteria(session_factory)
    teacher_id = _make_teacher(session_factory)
    ids = _criterion_ids(session_factory)
    _make_review(
        session_factory, teacher_id=teacher_id, scores={ids["explains"]: 5},
        comment="Отличный учитель", alias="Ученик-02",
    )
    _make_review(
        session_factory, teacher_id=teacher_id, scores={ids["explains"]: 1},
        comment="Скрытый отзыв", alias="Ученик-03",
        status="hidden",
    )

    page = student.client.get(f"/teachers/{teacher_id}").text
    assert "Отличный учитель" in page
    assert "Скрытый отзыв" not in page
    assert "5.0" in page      # the average over visible reviews only
    assert "по 1 отзывам" in page


def test_editing_a_hidden_review_does_not_resurrect_it(student, session_factory):
    _seed_criteria(session_factory)
    teacher_id = _make_teacher(session_factory)
    assert _submit(student, teacher_id, comment="Черновик").status_code == 303

    with session_factory() as db:
        review = db.execute(select(Review)).scalars().one()
        review.status = "hidden"
        db.commit()

    assert _submit(student, teacher_id, comment="Правка").status_code == 303
    with session_factory() as db:
        review = db.execute(select(Review)).scalars().one()
        assert review.comment == "Правка"
        assert review.status == "hidden"

    # The student is told, and the hidden review stays off the public list.
    page = student.client.get(f"/teachers/{teacher_id}").text
    assert "скрыт модератором" in page
    assert "Правка" not in page.split("<h2>Отзывы")[1].split("</section>")[0]


def test_pagination_appears_only_when_needed(student, session_factory):
    _seed_criteria(session_factory)
    teacher_id = _make_teacher(session_factory)
    ids = _criterion_ids(session_factory)
    for index in range(11):
        _make_review(
            session_factory, teacher_id=teacher_id,
            scores={ids["explains"]: 4}, comment=f"Отзыв {index}",
            alias=f"Прохожий-{index:02d}",
        )

    first = student.client.get(f"/teachers/{teacher_id}").text
    assert "Страница 1 из 2" in first
    assert "Отзыв 10" in first
    assert "Отзыв 0" not in first

    second = student.client.get(f"/teachers/{teacher_id}?page=2").text
    assert "Отзыв 0" in second

    # Junk and out-of-range pages land on a sane page instead of an error.
    assert student.client.get(f"/teachers/{teacher_id}?page=abc").status_code == 200
    assert "Страница 2 из 2" in student.client.get(
        f"/teachers/{teacher_id}?page=99"
    ).text
