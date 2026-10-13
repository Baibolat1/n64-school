"""Tests for applications to the development team.

The card on /me, the sheet it opens, the page behind the sheet and the JSON
endpoint are four ways into one service, so the rules are tested on the service
and the wiring is tested on the pages. What matters at the top is that an
application a student sends is actually stored and readable afterwards -- a
button that looks like it worked and discards the answer is worse than no
button -- and that sending again replaces the row instead of piling them up.
"""

import sys
from pathlib import Path

import pytest
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models.student import Student  # noqa: E402
from app.models.team import DevApplication  # noqa: E402
from app.services import team as team_service  # noqa: E402


def _rows(session_factory) -> list[DevApplication]:
    with session_factory() as db:
        return list(db.execute(select(DevApplication)).scalars().all())


def _student(session_factory, student_id: int) -> Student:
    with session_factory() as db:
        return db.get(Student, student_id)


# --- The handle ----------------------------------------------------------------


@pytest.mark.parametrize(
    "typed, stored",
    [
        ("@nickname", "@nickname"),
        ("nickname", "@nickname"),
        ("  @nickname  ", "@nickname"),
        ("t.me/nickname", "@nickname"),
        ("https://t.me/nickname", "@nickname"),
        ("https://telegram.me/nickname/", "@nickname"),
    ],
)
def test_a_handle_is_stored_in_one_shape(typed, stored):
    # Copied out of Telegram, a handle arrives as a link as often as as a name,
    # and a student who pastes one should not be told they are wrong.
    assert team_service.normalize_contact(typed) == stored


@pytest.mark.parametrize(
    "typed",
    ["", "   ", "аб", "no spaces allowed", "nickname!", "t.me/ab", "@nickname@x"],
)
def test_something_that_is_not_a_handle_is_refused(typed):
    assert team_service.normalize_contact(typed) is None


# --- The service ---------------------------------------------------------------


@pytest.mark.parametrize("role", team_service.ROLES)
def test_every_offered_role_is_accepted(session_factory, student, role):
    with session_factory() as db:
        row = db.get(Student, student.student_id)
        result = team_service.submit_application(db, row, role, "@nickname")

    assert result.ok is True
    assert result.application.role == role


def test_an_unknown_role_is_refused(session_factory, student):
    with session_factory() as db:
        row = db.get(Student, student.student_id)
        result = team_service.submit_application(db, row, "smm", "@nickname")

    assert result.ok is False
    assert result.reason is team_service.TeamError.UNKNOWN_ROLE
    assert team_service.refusal_message(result)


def test_applying_twice_replaces_the_answer(session_factory, student):
    with session_factory() as db:
        row = db.get(Student, student.student_id)
        team_service.submit_application(db, row, "frontend", "@first")
    with session_factory() as db:
        row = db.get(Student, student.student_id)
        team_service.submit_application(db, row, "backend", "@second")

    rows = _rows(session_factory)
    assert len(rows) == 1, "the latest word is the application, not a second row"
    assert (rows[0].role, rows[0].contact) == ("backend", "@second")


def test_a_refusal_carries_the_typed_handle_back(session_factory, student):
    # The form echoes it: making a student retype a handle because of a typo in
    # a role would be punishing the wrong field.
    with session_factory() as db:
        row = db.get(Student, student.student_id)
        result = team_service.submit_application(db, row, "smm", "@nickname")

    assert result.contact == "@nickname"
    assert _rows(session_factory) == []


def test_an_unknown_role_still_has_a_label():
    # A role removed from ROLES after somebody applied for it must not blank out
    # the row that is already in the database.
    assert team_service.role_label("frontend") == "Frontend"
    assert team_service.role_label("smm") == "smm"


# --- The pages -----------------------------------------------------------------


def test_the_profile_offers_the_team_card_and_a_way_in(student):
    html = student.client.get("/me").text

    assert "Разработка n64hub" in html
    assert "Хочешь развивать школьную платформу вместе с нами?" in html
    assert "Подать заявку в команду" in html
    # The link and the dialog opener in one element: with a script the sheet
    # opens, without one the href is a real page.
    assert 'href="/me/dev-apply"' in html
    assert 'data-dialog-open="dev-apply"' in html
    assert 'id="dev-apply"' in html


def test_the_sheet_offers_the_four_roles(student):
    html = student.client.get("/me").text

    for role in team_service.ROLES:
        assert f'value="{role}"' in html
        assert team_service.ROLE_LABELS[role] in html


def test_the_apply_page_stands_on_its_own(student):
    response = student.client.get("/me/dev-apply")

    assert response.status_code == 200
    assert 'action="/me/dev-applications"' in response.text
    for role in team_service.ROLES:
        assert f'value="{role}"' in response.text


def test_the_form_needs_a_session(client):
    response = client.get("/me/dev-apply", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_sending_without_a_script_stores_it_and_returns_to_the_profile(
    student, session_factory
):
    response = student.post(
        "/me/dev-applications",
        "/me/dev-apply",
        {"role": "design", "contact": "@nickname"},
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/me"

    with session_factory() as db:
        application = team_service.get_for_student(db, student.student_id)
    assert application is not None
    assert (application.role, application.contact) == ("design", "@nickname")

    # And the profile says so, rather than leaving the student to wonder whether
    # the button did anything.
    assert "@nickname" in student.client.get("/me").text


def test_a_refused_application_comes_back_with_what_was_typed(student, session_factory):
    response = student.post(
        "/me/dev-applications",
        "/me/dev-apply",
        {"role": "smm", "contact": "@nickname"},
    )

    assert response.status_code == 200
    assert "Выберите роль" in response.text
    assert 'value="@nickname"' in response.text
    assert _rows(session_factory) == []


# --- Reading them back ----------------------------------------------------------


def test_the_admin_screen_shows_who_applied(admin, student, session_factory):
    student.post(
        "/me/dev-applications",
        "/me/dev-apply",
        {"role": "cyber", "contact": "@nickname"},
    )

    html = admin.client.get("/admin/dev-applications").text
    assert "@nickname" in html
    assert team_service.ROLE_LABELS["cyber"] in html
    assert _student(session_factory, student.student_id).alias in html
    # The dashboard counts them too, so the queue is not something an admin has
    # to go looking for.
    assert "Заявки в команду" in admin.client.get("/admin").text


def test_the_admin_screen_is_closed_to_students(student):
    response = student.client.get("/admin/dev-applications", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/login"
