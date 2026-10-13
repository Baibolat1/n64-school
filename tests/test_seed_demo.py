"""The demo seeder: it fills everything, and a second run fills nothing.

The database work is driven through the ``seed_*`` functions with the test
session instead of ``main()``, which would reach for the real ``SessionLocal``
and write into ``school.db``. Idempotency is the property worth pinning down:
a seeder that doubles its rows on the second run quietly ruins a demo.
"""

from datetime import date, datetime, timezone

import pytest

from scripts import seed_demo


def _run_all(db, *, today=None, now=None):
    """Every seeding step in the order ``main`` runs them; returns the counters."""
    today = today or date(2026, 10, 13)
    now = now or datetime(2026, 10, 13, 9, 0, tzinfo=timezone.utc)
    counts = {}
    counts.update(seed_demo.seed_students(db))
    counts.update(seed_demo.seed_school(db, today=today))
    counts.update(seed_demo.seed_esports(db, now=now))
    counts.update(seed_demo.seed_social(db))
    db.commit()
    return counts


def test_the_first_run_creates_everything(session_factory):
    with session_factory() as db:
        counts = _run_all(db)

    assert set(counts) == {
        "ученики",
        "расписание",
        "домашние задания",
        "заметки",
        "турниры",
        "награды",
        "посты",
        "комментарии",
        "лайки",
    }
    # Nothing is empty: an empty table would be a silent typo in the data.
    for name, tally in counts.items():
        assert tally.created > 0, name
        assert tally.updated == 0, name


def test_a_second_run_creates_nothing(session_factory):
    with session_factory() as db:
        _run_all(db)
        counts = _run_all(db)

    for name, tally in counts.items():
        assert tally.created == 0, name
        assert tally.updated > 0, name


def test_the_homework_dates_follow_the_day_it_ran(session_factory):
    # The demo must not go stale: the tasks are counted from today, so a run on
    # another day moves them instead of leaving yesterday's rows behind.
    from app.repositories import schedule as schedule_repo

    with session_factory() as db:
        _run_all(db, today=date(2026, 10, 13))
        first = schedule_repo.homework_for_class(
            db, "9А", since=date(2026, 10, 1)
        )
        assert min(item.due_date for item in first) > date(2026, 10, 13)

        _run_all(db, today=date(2026, 10, 20))
        second = schedule_repo.homework_for_class(
            db, "9А", since=date(2026, 10, 1)
        )

    assert len(second) == len(first)
    assert min(item.due_date for item in second) > date(2026, 10, 20)


def test_the_login_account_gets_a_password_that_verifies(session_factory):
    from app.core.security import verify_password
    from app.repositories import students as students_repo

    with session_factory() as db:
        _run_all(db)
        assert seed_demo.grant_login(db, password="демо-пароль") is True
        db.commit()
        student = students_repo.get_student_by_alias(db, seed_demo.LOGIN_ALIAS)

    assert student.email == seed_demo.LOGIN_EMAIL
    assert verify_password(student.password_hash, "демо-пароль")
    assert not verify_password(student.password_hash, "другой-пароль")


def test_an_anonymous_post_is_stored_under_the_shared_alias(session_factory):
    from sqlalchemy import select

    from app.models.content import Post
    from app.services.publication import ANONYMOUS_ALIAS

    with session_factory() as db:
        _run_all(db)
        anonymous = db.execute(
            select(Post).where(Post.is_anonymous.is_(True))
        ).scalars().all()

    assert anonymous, "the demo feed should contain an anonymous post"
    for post in anonymous:
        # The alias on an anonymous post is the shared one, never the author's.
        assert post.display_alias == ANONYMOUS_ALIAS


def test_a_typo_in_the_data_stops_the_run(session_factory, monkeypatch):
    # Half-written demo data is worse than none: an unknown pseudonym has to be
    # an error, not a silently skipped row.
    monkeypatch.setattr(
        seed_demo, "BADGES", (("Никого-Нет", "Осенний кубок по CS2", "mvp"),)
    )
    with session_factory() as db:
        seed_demo.seed_students(db)
        with pytest.raises(ValueError, match="не найден"):
            seed_demo.seed_esports(
                db, now=datetime(2026, 10, 13, 9, 0, tzinfo=timezone.utc)
            )
        # The error stops the run, so the caller's commit never happens.
        db.rollback()
