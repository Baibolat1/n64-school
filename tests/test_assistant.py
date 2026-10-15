"""The assistant: what it answers from the database and what it refuses.

The hybrid promise is the thing under test. With no key configured *nothing*
leaves the server and the answer comes from the school's own tables or is an
honest "I cannot"; the LLM path is checked for its two properties that matter
here -- it is never called without a key, and a question that the database
already answers never reaches it (which is what keeps the model out of the
common case, and the question off somebody else's server).
"""

from datetime import date, timedelta

import pytest

from app.config import settings
from app.models.student import Student
from app.repositories import schedule as schedule_repo
from app.services import assistant
from app.services import school as school_service

TODAY = date(2026, 10, 13)  # a Tuesday, so "today" and "tomorrow" differ


@pytest.fixture()
def class_data(session_factory):
    """«9А» with lessons today, one tomorrow, homework and a note."""
    with session_factory() as db:
        for day, subject, room in (
            (school_service.today_weekday() or 2, "Математика", "каб. 24"),
            (3, "Химия", "каб. 28"),
        ):
            schedule_repo.upsert_entry(
                db,
                class_name="9А",
                day_of_week=day,
                lesson_no=1,
                subject=subject,
                room=room,
                starts_at="08:30",
                ends_at="09:15",
            )
        schedule_repo.upsert_homework(
            db,
            class_name="9А",
            subject="Математика",
            due_date=TODAY + timedelta(days=1),
            body="§12, №345",
        )
        student = Student(alias="Ученик-Ассистент", class_name="9А")
        db.add(student)
        db.commit()
        student_id = student.id
    return student_id


def _ask(session_factory, student_id: int, question: str):
    """Run the pipeline the router runs, without going through HTTP."""
    with session_factory() as db:
        student = db.get(Student, student_id)
        return assistant.ask(db, student, question, today=TODAY)


# --- Answers from the database ----------------------------------------------


def test_the_schedule_question_is_answered_from_the_database(session_factory, class_data):
    answer = _ask(session_factory, class_data, "какие уроки на среду?")
    assert answer.source == assistant.SOURCE_DB
    assert "Химия" in answer.text
    assert "каб. 28" in answer.text          # the room as it is written, once


def test_the_day_is_named_in_the_accusative(session_factory, class_data):
    # «Расписание на среда» is what the nominative table would produce.
    answer = _ask(session_factory, class_data, "расписание на среду")
    assert "на среду" in answer.text


def test_homework_can_be_narrowed_to_a_subject_in_any_case(session_factory, class_data):
    # «по математике» does not contain «математика»; the answer must still be
    # only the mathematics homework.
    answer = _ask(session_factory, class_data, "что задали по математике?")
    assert answer.source == assistant.SOURCE_DB
    assert "§12, №345" in answer.text
    assert "Химия" not in answer.text


def test_a_student_without_a_class_is_told_which_field_is_missing(session_factory):
    with session_factory() as db:
        student = Student(alias="Ученик-Без-Класса-И")
        db.add(student)
        db.commit()
        student_id = student.id

    answer = _ask(session_factory, student_id, "какие уроки сегодня?")
    assert answer.source == assistant.SOURCE_DB
    assert "класс" in answer.text.lower()


def test_the_help_sentence_lists_what_it_can_do(session_factory, class_data):
    answer = _ask(session_factory, class_data, "помощь")
    assert answer.source == assistant.SOURCE_HELP
    assert "расписание" in answer.text


# --- The model, and the promise that it stays out of it ----------------------


def test_nothing_is_sent_anywhere_without_a_key(session_factory, class_data, monkeypatch):
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "")
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", "")

    def explode(*args, **kwargs):  # pragma: no cover - must not run at all
        raise AssertionError("the model was called without a key")

    monkeypatch.setattr(assistant, "ask_llm", explode)
    answer = _ask(session_factory, class_data, "расскажи анекдот")
    assert answer.source == assistant.SOURCE_HELP
    assert "модель не подключена" in answer.text


def test_a_question_the_database_answers_never_reaches_the_model(
    session_factory, class_data, monkeypatch
):
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "ключ")

    def explode(*args, **kwargs):  # pragma: no cover - must not run at all
        raise AssertionError("the model was asked something the database knows")

    monkeypatch.setattr(assistant, "ask_llm", explode)
    answer = _ask(session_factory, class_data, "какие уроки на среду?")
    assert answer.source == assistant.SOURCE_DB


def test_a_failing_model_is_a_polite_answer_not_a_crash(
    session_factory, class_data, monkeypatch
):
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "ключ")

    def broken(*args, **kwargs):
        raise assistant.LlmUnavailable("нет сети")

    monkeypatch.setattr(assistant, "ask_llm", broken)
    answer = _ask(session_factory, class_data, "расскажи анекдот")
    assert answer.source == assistant.SOURCE_HELP
    assert "Модель" in answer.text


def test_the_context_sent_to_the_model_carries_no_names(session_factory, class_data):
    with session_factory() as db:
        student = db.get(Student, class_data)
        context = assistant.build_context(db, student, today=TODAY)

    # Only today's lessons and the nearest homework: no alias, no class, no
    # post. Whatever the model is told must not identify the student.
    assert student.alias not in context
    assert (student.class_name or "") not in context
    assert "Математика" in context or "Химия" in context


# --- The page and the POST behind it ----------------------------------------


def test_the_page_is_closed_to_an_anonymous_visitor(client):
    response = client.get("/assistant", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_an_empty_question_is_a_message_not_an_error(student):
    response = student.post("/assistant", "/assistant", {"question": "   "})
    assert response.status_code == 200
    assert "Введите вопрос" in response.text


def test_a_question_longer_than_the_limit_is_refused(student):
    response = student.post(
        "/assistant", "/assistant", {"question": "а" * (assistant.MAX_QUESTION_LENGTH + 1)}
    )
    assert response.status_code == 200
    # Refused with a sentence, and the input's own maxlength is what the browser
    # enforces: the server must not answer a truncated question as if it were
    # the one that was asked.
    assert "слишком длинный" in response.text.lower()


def test_a_flood_is_stopped_by_the_rate_limit(student, monkeypatch):
    # The limit is per student and counted in the limiter's window; the test
    # makes it two so the third request is the one that is refused.
    monkeypatch.setattr(settings, "RATE_LIMIT_ASSISTANT", 2)

    for _ in range(2):
        assert student.post(
            "/assistant", "/assistant", {"question": "расписание"}
        ).status_code == 200

    refused = student.post("/assistant", "/assistant", {"question": "расписание"})
    assert refused.status_code == 429
    assert int(refused.headers["Retry-After"]) > 0


def test_the_day_budget_stops_the_whole_site(student, monkeypatch):
    # Кроме личного окна есть суточный потолок на школу: вопрос, дошедший до
    # модели, стоит денег, и по одному ученику этого не видно. Отказ -- фраза,
    # которую человек понимает, а не «ошибка сервера».
    monkeypatch.setattr(settings, "RATE_LIMIT_ASSISTANT", 10)
    monkeypatch.setattr(settings, "ASSISTANT_DAILY_LIMIT", 2)

    for _ in range(2):
        assert (
            student.post("/assistant", "/assistant", {"question": "расписание"})
            .status_code
            == 200
        )

    refused = student.post("/assistant", "/assistant", {"question": "расписание"})
    assert refused.status_code == 429
    assert "На сегодня вопросы" in refused.text
    assert int(refused.headers["Retry-After"]) > 0

