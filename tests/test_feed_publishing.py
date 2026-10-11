"""End-to-end tests of publishing: PII filter and rate limit wired into the feed.

These drive the real routes through a client, so they cover the whole path --
CSRF token, session, service gates, repository, database -- and then look at what
actually landed in the tables.
"""

import sys
from pathlib import Path

from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402
from app.models.audit import PiiBlockedAttempt, PiiFlag  # noqa: E402
from app.models.content import CONTENT_STATUS_VISIBLE, Comment, Post  # noqa: E402
from app.services.pii_filter import PLACEHOLDER  # noqa: E402


def _rows(session_factory, model):
    with session_factory() as db:
        return list(db.execute(select(model)).scalars().all())


def _make_post(session_factory, student_id, body="Тема для обсуждения"):
    with session_factory() as db:
        post = Post(
            author_id=student_id,
            display_alias="Ученик-01",
            is_anonymous=False,
            body=body,
            status=CONTENT_STATUS_VISIBLE,
        )
        db.add(post)
        db.commit()
        return post.id


def test_clean_post_is_published_unchanged(student, session_factory):
    response = student.post(
        "/feed/posts", "/feed", {"body": "Привет! Как дела у всех?"}
    )
    assert response.status_code == 303

    posts = _rows(session_factory, Post)
    assert len(posts) == 1
    assert posts[0].body == "Привет! Как дела у всех?"
    assert posts[0].status == CONTENT_STATUS_VISIBLE
    assert _rows(session_factory, PiiFlag) == []


def test_soft_pii_is_redacted_and_queued(student, session_factory):
    response = student.post(
        "/feed/posts", "/feed", {"body": "Меня зовут Иван Петров, мне 15 лет"}
    )
    assert response.status_code == 303

    posts = _rows(session_factory, Post)
    assert len(posts) == 1
    # Published immediately (not held as "pending"), with the name masked.
    assert posts[0].status == CONTENT_STATUS_VISIBLE
    assert posts[0].body == f"Меня зовут {PLACEHOLDER}, мне 15 лет"

    flags = _rows(session_factory, PiiFlag)
    assert len(flags) == 1
    assert flags[0].target_type == "post"
    assert flags[0].target_id == posts[0].id
    assert flags[0].rule_code == "full_name"
    assert flags[0].status == "open"
    # Only a truncated snippet ever reaches the queue.
    assert flags[0].snippet and len(flags[0].snippet) <= 40


def test_hard_pii_is_refused_and_only_the_fact_is_stored(student, session_factory):
    response = student.post(
        "/feed/posts", "/feed", {"body": "Позвони мне +7 912 345-67-89"}
    )
    assert response.status_code == 200  # form re-rendered, not a redirect
    assert "нельзя публиковать" in response.text

    assert _rows(session_factory, Post) == []
    assert _rows(session_factory, PiiFlag) == []

    attempts = _rows(session_factory, PiiBlockedAttempt)
    assert len(attempts) == 1
    assert attempts[0].student_id == student.student_id
    assert attempts[0].target_type == "post"
    assert attempts[0].rule_codes == "phone"
    assert attempts[0].created_at is not None
    # The row records the fact only: there is no column the message could
    # have gone into, not even truncated.
    assert set(PiiBlockedAttempt.__table__.columns.keys()) == {
        "id",
        "student_id",
        "target_type",
        "rule_codes",
        "created_at",
    }


def test_blocked_post_of_a_clean_text_leaves_no_row(student, session_factory):
    # Only refusals are recorded: an ordinary post must not touch the table.
    student.post("/feed/posts", "/feed", {"body": "Привет, как дела?"})
    assert _rows(session_factory, PiiBlockedAttempt) == []


def test_blocked_post_echoes_the_text_back(student):
    text = "Пиши на test@mail.ru"
    response = student.post("/feed/posts", "/feed", {"body": text})
    assert text in response.text


def test_clean_comment_is_published(student, session_factory):
    post_id = _make_post(session_factory, student.student_id)
    response = student.post(
        f"/feed/posts/{post_id}/comments",
        f"/feed/posts/{post_id}",
        {"body": "Согласен, хорошая идея"},
    )
    assert response.status_code == 303
    comments = _rows(session_factory, Comment)
    assert len(comments) == 1
    assert comments[0].body == "Согласен, хорошая идея"


def test_soft_pii_comment_is_redacted_and_queued(student, session_factory):
    post_id = _make_post(session_factory, student.student_id)
    response = student.post(
        f"/feed/posts/{post_id}/comments",
        f"/feed/posts/{post_id}",
        {"body": "Это Петр Иванович объяснял"},
    )
    assert response.status_code == 303
    comments = _rows(session_factory, Comment)
    assert comments[0].body == f"Это {PLACEHOLDER} объяснял"
    flags = _rows(session_factory, PiiFlag)
    assert len(flags) == 1
    assert flags[0].target_type == "comment"
    assert flags[0].target_id == comments[0].id


def test_hard_pii_comment_is_refused(student, session_factory):
    post_id = _make_post(session_factory, student.student_id)
    response = student.post(
        f"/feed/posts/{post_id}/comments",
        f"/feed/posts/{post_id}",
        {"body": "Скинь на https://example.com"},
    )
    assert response.status_code == 200
    assert "нельзя публиковать" in response.text
    assert _rows(session_factory, Comment) == []

    attempts = _rows(session_factory, PiiBlockedAttempt)
    assert len(attempts) == 1
    assert attempts[0].target_type == "comment"
    assert attempts[0].rule_codes == "link"


def test_profanity_is_published_as_typed(student, session_factory):
    # Rude words are allowed: no word list runs over posts (see docs/PLAN.md).
    body = "Это полный блядский бред, если честно"
    assert student.post("/feed/posts", "/feed", {"body": body}).status_code == 303
    assert _rows(session_factory, Post)[0].body == body
    assert _rows(session_factory, PiiFlag) == []


def test_line_breaks_and_spacing_survive_storage(student, session_factory):
    # The normalization that defeats obfuscation exists only for the rules: what
    # is stored is the author's own text, with line breaks intact.
    body = "Привет     мир\n\nвсем"
    student.post("/feed/posts", "/feed", {"body": body})
    posts = _rows(session_factory, Post)
    assert posts[0].body == body


def test_normalization_does_not_hide_a_redacted_fragment(student, session_factory):
    # The rules run on the normalized copy, so a fragment split by a full-width
    # space is still caught -- and only that fragment is replaced, not the whole
    # text around it.
    student.post("/feed/posts", "/feed", {"body": "Это Иван　Петров\nдальше строка"})
    posts = _rows(session_factory, Post)
    assert posts[0].body == f"Это {PLACEHOLDER}\nдальше строка"


def test_rate_limit_refuses_after_the_budget(student, session_factory, monkeypatch):
    monkeypatch.setattr(settings, "RATE_LIMIT_POSTS", 2)

    assert student.post("/feed/posts", "/feed", {"body": "раз"}).status_code == 303
    assert student.post("/feed/posts", "/feed", {"body": "два"}).status_code == 303

    refused = student.post("/feed/posts", "/feed", {"body": "три"})
    assert refused.status_code == 429
    assert "Слишком часто" in refused.text
    assert 3599 <= int(refused.headers["retry-after"]) <= 3600
    assert len(_rows(session_factory, Post)) == 2


def test_rejected_empty_posts_do_not_spend_the_budget(student, session_factory):
    # Shape validation runs before the limiter, so a student fumbling the form
    # does not lock themselves out.
    for _ in range(20):
        assert student.post("/feed/posts", "/feed", {"body": "   "}).status_code == 200
    assert student.post("/feed/posts", "/feed", {"body": "нормальный текст"}).status_code == 303
    assert len(_rows(session_factory, Post)) == 1
