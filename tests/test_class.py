"""«Мой класс»: роспись класса, сегодняшний день, домашка и выбор класса.

Класс берётся из сессии и только оттуда: параметра с классом в адресе нет
нарочно, иначе чужую сетку уроков и чужой список псевдонимов можно было бы
выпросить прямой ссылкой. Список в настройках -- это расписание школы, а не
справочник в базе: класс существует ровно там, где стоит его сетка
(см. app.services.classes).

Часы в этих тестах заморожены на понедельник: страница показывает «сегодня», и
без остановленных часов она была бы расписанием того дня, когда их запустили.
"""

import re
from datetime import date, datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.models.student import Student
from app.repositories import schedule as schedule_repo
from app.services import school as school_service
from app.services import student_auth

from tests.conftest import StudentSession

# Понедельник 12 октября 2026, 12:00 UTC -- замороженное «сегодня».
MONDAY = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)

# Классы школы. Шесть -- чтобы список в настройках было на чём проверить:
# обычная сортировка строк поставила бы «10А» перед «8А».
CLASSES = ["9А", "10А", "8Б", "10Б", "8А", "9Б"]


class _FrozenDatetime(datetime):
    """``datetime`` с остановленными часами: «сегодня» -- всегда понедельник."""

    @classmethod
    def now(cls, tz=None):
        return MONDAY


@pytest.fixture()
def frozen(monkeypatch):
    """Останавливает часы школьной сводки на понедельнике."""
    monkeypatch.setattr(school_service, "datetime", _FrozenDatetime)
    return MONDAY


def _session_for(session_factory, alias: str, *, class_name=None) -> StudentSession:
    """Залогиненный клиент свежего ученика (по образцу tests/test_school.py)."""
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
def timetable(session_factory):
    """Сетка школы: по уроку в каждом классе плюс домашка для 9А.

    Уроки стоят на понедельник -- на нём же заморожено «сегодня», поэтому
    страница показывает именно их.
    """
    with session_factory() as db:
        for class_name in CLASSES:
            schedule_repo.upsert_entry(
                db,
                class_name=class_name,
                day_of_week=1,
                lesson_no=1,
                subject=f"Классный час {class_name}",
                room="каб. 1",
                starts_at="08:30",
                ends_at="09:15",
            )
        schedule_repo.upsert_entry(
            db,
            class_name="9А",
            day_of_week=1,
            lesson_no=2,
            subject="Математика",
            room="каб. 24",
            starts_at="09:25",
            ends_at="10:10",
        )
        # Задание на сегодня -- оно и подсвечивается -- и на завтра.
        schedule_repo.upsert_homework(
            db,
            class_name="9А",
            subject="Математика",
            due_date=MONDAY.date(),
            body="§12, №345",
        )
        schedule_repo.upsert_homework(
            db,
            class_name="9А",
            subject="Физика",
            due_date=date(2026, 10, 13),
            body="§7, вопросы",
        )
        # Домашка чужого класса: на экране 9А её быть не должно.
        schedule_repo.upsert_homework(
            db,
            class_name="10А",
            subject="Астрономия",
            due_date=MONDAY.date(),
            body="§1",
        )
        db.commit()


@pytest.fixture()
def classmates(session_factory):
    """Роспись 9А: три одноклассника, один из них с аватаркой, один забанен.

    Псевдонимы подобраны на сортировку: по коду символов «Ёж» встал бы перед
    «Абрикос» (Ё -- U+0401, А -- U+0410), а по алфавиту он между ним и
    «Яблоня».
    """
    with session_factory() as db:
        db.add(Student(alias="Яблоня", class_name="9А"))
        db.add(Student(alias="Ёж", class_name="9А", avatar_key="circle"))
        db.add(Student(alias="Абрикос", class_name="9А"))
        db.add(Student(alias="Забаненный", class_name="9А", is_banned=True))
        # Ученик чужого класса: на экране 9А его псевдонима быть не должно.
        db.add(Student(alias="Звезда", class_name="10А"))
        db.commit()


@pytest.fixture()
def pupil(client, session_factory, timetable, classmates) -> StudentSession:
    """Ученик 9А с готовой школой вокруг: сетка, домашка и одноклассники."""
    return _session_for(session_factory, "Ученик-09", class_name="9А")


# --- The page ---------------------------------------------------------------


def test_the_class_page_is_closed_to_an_anonymous_visitor(client):
    response = client.get("/me/class", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_the_page_names_the_class_and_lists_the_people_in_it(pupil):
    page = pupil.client.get("/me/class")
    assert page.status_code == 200
    assert "Мой класс · 9А" in page.text

    # Everyone in the class is here, the reader included: a roster that crossed
    # the reader out reads as "here they are -- where am I".
    for alias in ("Абрикос", "Ёж", "Яблоня", "Ученик-09"):
        assert alias in page.text
    assert page.text.count("это вы") == 1

    # By the alphabet, not by code points: SQLite would have sorted «Ёж» above
    # «Абрикос», and the alphabet puts it between «Абрикос» and «Яблоня».
    assert (
        page.text.index("Абрикос")
        < page.text.index("Ёж")
        < page.text.index("Яблоня")
    )

    # And the picture comes with the name, gallery key and all.
    assert "avatar--sm" in page.text
    assert "circle" in page.text


def test_a_banned_classmate_is_not_in_the_roster(pupil):
    # The roster is a shop window like the feed: a student moderation removed
    # from the site does not come back in through the class list.
    page = pupil.client.get("/me/class")
    assert "Забаненный" not in page.text


def test_another_class_is_not_in_the_roster(pupil):
    page = pupil.client.get("/me/class")
    assert "Звезда" not in page.text
    assert "Астрономия" not in page.text


def test_the_page_shows_the_day_and_the_homework_that_follows(pupil, frozen):
    page = pupil.client.get("/me/class")
    assert "Сегодня · Понедельник" in page.text
    assert "Математика" in page.text          # the lesson of the day
    assert "каб. 24" in page.text
    assert "§12, №345" in page.text           # due today
    assert "§7, вопросы" in page.text         # and the next one after it
    # Today's is the one marked: the highlight is what tells the two apart.
    assert "hw-item--today" in page.text

    # The whole week is the schedule page's job: the class page is the day.
    assert "Классный час 9Б" not in page.text


def test_a_student_without_a_class_is_told_where_to_choose_one(
    client, session_factory, timetable
):
    student = _session_for(session_factory, "Без-Класса")
    page = student.client.get("/me/class")

    assert page.status_code == 200
    assert "Класс не указан" in page.text
    assert 'href="/me/settings"' in page.text
    # And nothing of anybody else's class: an empty class shows no class at all.
    assert "Математика" not in page.text
    assert "Абрикос" not in page.text


def test_the_class_comes_from_the_session_and_not_from_the_address(pupil):
    page = pupil.client.get("/me/class?class=10А")
    assert page.status_code == 200
    assert "Мой класс · 9А" in page.text
    assert "Звезда" not in page.text
    assert "Астрономия" not in page.text


# --- Choosing one -----------------------------------------------------------


def test_the_settings_offer_the_schools_classes_in_school_order(pupil):
    page = pupil.client.get("/me/settings")
    assert page.status_code == 200

    select = page.text.split('name="class_name"')[1].split("</select>")[0]
    # The classes of the timetable, in school order -- not in string order,
    # which would have put 10А before 8А.
    offered = select.split('<option value="')[1:]
    assert [option.split('"')[0] for option in offered] == [
        "",
        *sorted(CLASSES, key=lambda name: (int(name[:-1]), name[-1].casefold())),
    ]
    # Only what the school knows: a class with no timetable is not on the menu.
    assert "7В" not in select


def test_the_class_form_is_also_in_the_sheet_on_the_profile(pupil):
    page = pupil.client.get("/me")
    assert "Мой класс" in page.text and 'href="/me/class"' in page.text
    assert 'id="profile-class">9А' in page.text
    # The same form as on /me/settings, in the sheet.
    form = re.search(r'<form[^>]*data-async="class".*?</form>', page.text, re.S).group(0)
    assert 'data-api="/api/me/class"' in form
    assert 'action="/me/class"' in form
    assert 'name="class_name"' in form


def test_a_class_from_the_form_is_stored_and_the_profile_is_shown(pupil, session_factory):
    response = pupil.post("/me/class", "/me/settings", {"class_name": "9Б"})

    assert response.status_code == 303
    assert response.headers["location"] == "/me"
    with session_factory() as db:
        assert db.get(Student, pupil.student_id).class_name == "9Б"


def test_a_class_the_school_does_not_have_is_refused(pupil, session_factory):
    response = pupil.post("/me/class", "/me/settings", {"class_name": "7В"})

    # The refusal comes back to the page with the field on it, like the alias.
    assert response.status_code == 200
    assert "Такого класса в школе нет" in response.text
    with session_factory() as db:
        assert db.get(Student, pupil.student_id).class_name == "9А"


def test_an_empty_choice_clears_the_class(pupil, session_factory):
    # Not an error: a student who left the school, or picked the wrong class,
    # has to be able to go back to having none.
    response = pupil.post("/me/class", "/me/settings", {"class_name": "  "})

    assert response.status_code == 303
    assert response.headers["location"] == "/me"
    with session_factory() as db:
        assert db.get(Student, pupil.student_id).class_name is None


def test_the_class_is_not_offered_when_the_school_has_no_timetable(
    client, session_factory
):
    student = _session_for(session_factory, "Без-Расписания")
    page = student.client.get("/me/settings")

    # No classes anywhere means nothing to choose from: a select holding only
    # "не указан" would be a choice that is not a choice.
    assert 'name="class_name"' not in page.text
    assert "Список классов появится" in page.text


# --- The JSON twin ----------------------------------------------------------


def test_the_api_stores_the_class_and_answers_with_it(pupil, session_factory):
    response = pupil.post("/api/me/class", "/me/settings", {"class_name": "9Б"})
    payload = response.json()

    assert payload["ok"] is True
    assert payload["class_name"] == "9Б"
    assert payload["message"]
    with session_factory() as db:
        assert db.get(Student, pupil.student_id).class_name == "9Б"


def test_the_api_refuses_an_unknown_class(pupil, session_factory):
    response = pupil.post("/api/me/class", "/me/settings", {"class_name": "7В"})
    payload = response.json()

    assert payload["ok"] is False
    assert "класс" in payload["error"]
    with session_factory() as db:
        assert db.get(Student, pupil.student_id).class_name == "9А"
