"""Tournaments, badges and the labels the pages put on them.

The codes live in the database and the words live in
``app.services.esports``; the two are kept apart on purpose, and the first
checks here are the ones that notice if they drift -- a new game in
``GAME_CODES`` with no label would otherwise reach the page as a raw code.
"""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import settings
from app.main import app
from app.models.esports import BADGE_CODES, GAME_CODES, TOURNAMENT_STATUSES
from app.models.student import Student
from app.repositories import tournaments as tournaments_repo
from app.services import esports as esports_service
from app.services import student_auth

from tests.conftest import StudentSession


MVP_ALIAS = "Полярная Сова"


def _session_for(session_factory, student: Student) -> StudentSession:
    """A logged-in client for a student row that already exists."""
    with session_factory() as db:
        student = db.merge(student)
        db.commit()
        token = student_auth.issue_student_session(student)
        student_id = student.id

    client = TestClient(app)
    client.cookies.set(settings.STUDENT_COOKIE_NAME, token, domain="testserver.local")
    return StudentSession(client=client, student_id=student_id)


def _student(session_factory, alias: str, class_name: str | None = None):
    """A new student, with a logged-in client for them."""
    with session_factory() as db:
        student = Student(alias=alias, class_name=class_name, avatar_key="moon")
        db.add(student)
        db.commit()
    return _session_for(session_factory, student)


@pytest.fixture()
def tournament(session_factory):
    """A live Brawl Stars tournament with one MVP and one captain."""
    with session_factory() as db:
        cup, _ = tournaments_repo.upsert_tournament(
            db,
            game="brawl",
            title="Кубок школы 3×3",
            format="3×3, BO1",
            starts_at=datetime.now(timezone.utc),
            status="live",
            description="Финалы после уроков.",
        )
        mvp = Student(alias=MVP_ALIAS, avatar_key="moon")
        captain = Student(alias="Быстрый Кот")
        db.add_all([mvp, captain])
        db.flush()
        tournaments_repo.upsert_badge(
            db, student_id=mvp.id, tournament_id=cup.id, code="mvp"
        )
        tournaments_repo.upsert_badge(
            db, student_id=captain.id, tournament_id=cup.id, code="captain"
        )
        db.commit()
        return cup.id


def _by_alias(session_factory, alias: str) -> Student:
    with session_factory() as db:
        return db.execute(
            select(Student).where(Student.alias == alias)
        ).scalar_one()


# --- Labels are data in one place -------------------------------------------


def test_every_code_has_a_label():
    # A code without a word would reach the page as itself ("cs2", "mvp").
    assert set(esports_service.GAME_LABELS) == set(GAME_CODES)
    assert set(esports_service.BADGE_LABELS) == set(BADGE_CODES)
    assert set(esports_service.STATUS_LABELS) == set(TOURNAMENT_STATUSES)
    assert set(esports_service.GAME_SHORT) == set(GAME_CODES)


def test_an_unknown_code_is_echoed_rather_than_crashing():
    # A row with a code the service has never heard of (an old value, a new game
    # in the database) must still render: the page shows the code itself.
    assert esports_service.game_label("dota") == "dota"
    assert esports_service.badge_label("судья") == "судья"
    assert esports_service.status_label("перенесён") == "перенесён"


def test_a_badge_is_not_awarded_twice(session_factory):
    # (student, tournament, award) is the key, so a re-run of the seeder awards
    # the same badge once -- the unique index would refuse the second row.
    with session_factory() as db:
        cup, _ = tournaments_repo.upsert_tournament(db, game="cs2", title="Кубок")
        player = Student(alias="Игрок-1")
        db.add(player)
        db.flush()
        _, created_first = tournaments_repo.upsert_badge(
            db, student_id=player.id, tournament_id=cup.id, code="mvp"
        )
        _, created_second = tournaments_repo.upsert_badge(
            db, student_id=player.id, tournament_id=cup.id, code="mvp"
        )
        db.commit()
        assert created_first is True
        assert created_second is False
        assert len(tournaments_repo.list_badges_for_student(db, player.id)) == 1


# --- The pages --------------------------------------------------------------


def test_the_esports_page_is_closed_to_an_anonymous_visitor(client):
    response = client.get("/esports", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_the_page_lists_the_tournament_with_its_roster(client, session_factory, tournament):
    # ``client`` is taken for its side effect: it points the app at the test
    # database that the viewer's own client then shares.
    viewer = _student(session_factory, "Зритель")
    page = viewer.client.get("/esports")
    assert page.status_code == 200
    assert "Кубок школы 3×3" in page.text
    assert "Идёт" in page.text                      # the status, not "live"
    assert MVP_ALIAS in page.text                   # the MVP
    assert "Быстрый Кот" in page.text               # the captain
    assert "MVP" in page.text and "Капитан" in page.text


def test_the_badges_are_shown_on_the_owner_profile(client, session_factory, tournament):
    owner = _session_for(session_factory, _by_alias(session_factory, MVP_ALIAS))
    page = owner.client.get("/me")
    assert page.status_code == 200
    assert "Кубок школы 3×3" in page.text
    assert "MVP" in page.text


def test_a_student_without_awards_sees_no_awards_section(client, session_factory, tournament):
    plain = _student(session_factory, "Без-Наград")
    page = plain.client.get("/me")
    assert page.status_code == 200
    assert "Награды" not in page.text
