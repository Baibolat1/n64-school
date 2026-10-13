"""The school day: schedule, homework, the widget on the feed and /schedule.

Two kinds of check live here. The first are pure functions of
``app.services.school`` -- the day labels are data, so they are compared against
the model's own ``SCHOOL_DAYS`` rather than against a second copy of the week.
The second are the pages: what a student of a class sees, and what a student
without a class sees instead of somebody else's timetable.
"""

from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.models.school import SCHOOL_DAYS
from app.models.student import Student
from app.repositories import schedule as schedule_repo
from app.services import school as school_service
from app.services import student_auth

from tests.conftest import StudentSession


def _monday() -> datetime:
    """A Monday in UTC, the anchor every weekday test counts from."""
    return datetime(2026, 10, 12, 9, 0, tzinfo=timezone.utc)


def _student_with_class(session_factory, alias: str, class_name: str | None):
    """A logged-in client for a fresh student, optionally with a class."""
    with session_factory() as db:
        student = Student(alias=alias, class_name=class_name)
        db.add(student)
        db.commit()
        token = student_auth.issue_student_session(student)
        student_id = student.id

    client = TestClient(app)
    client.cookies.set(settings.STUDENT_COOKIE_NAME, token, domain="testserver.local")
    return StudentSession(client=client, student_id=student_id)


@pytest.fixture()
def class_week(session_factory):
    """One lesson on Monday and homework due tomorrow, for the class «9А»."""
    with session_factory() as db:
        schedule_repo.upsert_entry(
            db,
            class_name="9А",
            day_of_week=1,
            lesson_no=1,
            subject="Математика",
            room="каб. 24",
            starts_at="08:30",
            ends_at="09:15",
        )
        schedule_repo.upsert_homework(
            db,
            class_name="9А",
            subject="Математика",
            due_date=date.today() + timedelta(days=1),
            body="§12, №345",
        )
        schedule_repo.upsert_note(
            db,
            class_name="9А",
            on_date=date.today(),
            body="Физика — замена: будет геометрия",
        )
        db.commit()


# --- Labels and pure helpers ------------------------------------------------


def test_every_weekday_has_the_labels_the_pages_ask_for():
    # The tables are keyed by the same numbers as the model's SCHOOL_DAYS, so a
    # sixth day (or a dropped one) cannot leave a label behind.
    assert set(school_service.DAY_LABELS) == set(SCHOOL_DAYS)
    assert set(school_service.DAY_SHORT) == set(SCHOOL_DAYS)
    assert set(school_service.DAY_ACCUSATIVE) == set(SCHOOL_DAYS)
    # «Расписание на вторник» needs the accusative: the nominative would read
    # «на среда». Same six days, different form.
    assert school_service.DAY_ACCUSATIVE[3] == "среду"
    assert school_service.DAY_LABELS[3] == "Среда"


@pytest.mark.parametrize(
    ("day_offset", "expected"),
    [(0, 1), (1, 2), (2, 3), (3, 4), (4, 5), (5, 6), (6, None)],
)
def test_today_weekday_counts_to_saturday_and_gives_sunday_up(day_offset, expected):
    # Sunday is not a school day: None is the honest answer, not Monday.
    assert school_service.today_weekday(_monday() + timedelta(days=day_offset)) == expected


def test_group_by_day_keeps_the_empty_days(session_factory):
    with session_factory() as db:
        schedule_repo.upsert_entry(
            db,
            class_name="9А",
            day_of_week=3,
            lesson_no=1,
            subject="Химия",
            room=None,
            starts_at="08:30",
            ends_at="09:15",
        )
        db.commit()
        entries = schedule_repo.list_for_class(db, "9А")

    grouped = school_service.group_by_day(entries)
    assert [day for day, _, _ in grouped] == list(SCHOOL_DAYS)
    assert grouped[2][2][0].subject == "Химия"
    assert grouped[0][2] == []  # Monday is in the list and is empty


def test_lesson_time_is_one_string(session_factory):
    with session_factory() as db:
        entry, created = schedule_repo.upsert_entry(
            db,
            class_name="9А",
            day_of_week=1,
            lesson_no=1,
            subject="Физика",
            room="каб. 31",
            starts_at="08:30",
            ends_at="09:15",
        )
        db.commit()
    assert created is True
    assert school_service.lesson_time(entry) == "08:30–09:15"


def test_repeating_a_lesson_updates_it_in_place(session_factory):
    # The seeder is re-runnable: the same (class, day, lesson) is one row, and
    # the second write replaces the subject instead of adding a lesson.
    with session_factory() as db:
        schedule_repo.upsert_entry(
            db,
            class_name="9А",
            day_of_week=1,
            lesson_no=1,
            subject="Математика",
            room="каб. 24",
            starts_at="08:30",
            ends_at="09:15",
        )
        entry, created = schedule_repo.upsert_entry(
            db,
            class_name="9А",
            day_of_week=1,
            lesson_no=1,
            subject="Алгебра",
            room="каб. 24",
            starts_at="08:30",
            ends_at="09:15",
        )
        db.commit()
        assert created is False
        assert len(schedule_repo.list_for_day(db, "9А", 1)) == 1
    assert entry.subject == "Алгебра"


# --- The pages --------------------------------------------------------------


def test_the_schedule_page_is_closed_to_an_anonymous_visitor(client):
    response = client.get("/schedule", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_a_student_sees_their_own_class_schedule_homework_and_notes(
    student, session_factory, class_week
):
    # The fixture's student has no class yet: the timetable is chosen by the
    # class on the profile, so this is what sets it.
    with session_factory() as db:
        db.get(Student, student.student_id).class_name = "9А"
        db.commit()

    page = student.client.get("/schedule")
    assert page.status_code == 200
    assert "Математика" in page.text
    assert "каб. 24" in page.text
    assert "§12, №345" in page.text
    assert "Физика — замена" in page.text


def test_a_student_without_a_class_is_told_so_instead_of_shown_a_timetable(
    student, class_week
):
    page = student.client.get("/schedule")
    assert page.status_code == 200
    # The class is another student's timetable key: without one there is nothing
    # correct to show, and showing any class would be showing somebody else's.
    assert "класс" in page.text.lower()
    assert "Математика" not in page.text


def test_the_feed_shows_the_school_day_widget_for_a_class(client, session_factory, class_week):
    # ``client`` is taken for its side effect: it points the app at the test
    # database, which the second client built inside the test then shares.
    logged_in = _student_with_class(session_factory, "Ученик-Виджет", "9А")
    page = logged_in.client.get("/feed")
    assert page.status_code == 200
    assert "widget" in page.text
    assert "Математика" in page.text


def test_the_feed_shows_no_widget_without_a_class(client, session_factory, class_week):
    logged_in = _student_with_class(session_factory, "Ученик-Без-Класса", None)
    page = logged_in.client.get("/feed")
    assert page.status_code == 200
    assert 'class="glass widget"' not in page.text
