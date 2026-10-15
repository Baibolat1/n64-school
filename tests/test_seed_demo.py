"""The demo seeder: it fills everything, and a second run fills nothing.

The database work is driven through the ``seed_*`` functions with the test
session instead of ``main()``, which would reach for the real ``SessionLocal``
and write into ``school.db``. Idempotency is the property worth pinning down:
a seeder that doubles its rows on the second run quietly ruins a demo.
"""

from datetime import date, datetime, timezone

import pytest
from sqlalchemy import func, select

from app.db.base import Base
from app.models.admin import Admin
from app.repositories import comments as comments_repo
from app.repositories import criteria as criteria_repo
from app.repositories import likes as likes_repo
from app.repositories import teachers as teachers_repo
from app.repositories.admins import create_admin
from scripts import seed_demo


def _seed_teachers(db):
    """The teachers and criteria the demo reviews are written about.

    A real run gets both from ``scripts/seed_teachers.py`` and
    ``data/teachers.demo.json``; here only the names and codes matter, and they
    are read from the seeder's own data so the two cannot drift apart.
    """
    codes = sorted({code for row in seed_demo.REVIEWS for code in row[4]})
    for position, code in enumerate(codes, start=1):
        criteria_repo.upsert_criterion(
            db, code=code, label=code, scale_min=1, scale_max=5, position=position * 10
        )
    for name in sorted({row[1] for row in seed_demo.REVIEWS}):
        teachers_repo.upsert_teacher(db, display_name=name, subject=None)


def _run_all(db, *, today=None, now=None):
    """Every seeding step in the order ``main`` runs them; returns the counters."""
    today = today or date(2026, 10, 13)
    now = now or datetime(2026, 10, 13, 9, 0, tzinfo=timezone.utc)
    _seed_teachers(db)
    counts = {}
    counts.update(seed_demo.seed_students(db))
    counts.update(seed_demo.seed_school(db, today=today))
    counts.update(seed_demo.seed_esports(db, now=now))
    counts.update(seed_demo.seed_social(db))
    counts.update(seed_demo.seed_reviews(db))
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
        "отзывы",
        "оценки",
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


def test_reviews_are_skipped_without_teachers(session_factory):
    # The seeder is the second half of the demo, not the first: with no teachers
    # in the database it fills everything else and says nothing about reviews
    # rather than failing the whole run.
    with session_factory() as db:
        counts = {}
        counts.update(seed_demo.seed_students(db))
        counts.update(seed_demo.seed_reviews(db))
        db.commit()

    assert "отзывы" not in counts


def test_the_reset_empties_the_demo_tables_and_keeps_the_admin(session_factory):
    # Two lists, no overlap: a table that falls into both is a reset that eats
    # the way into the panel.
    assert set(seed_demo.RESET_TABLES).isdisjoint(seed_demo.KEEP_TABLES)

    with session_factory() as db:
        _run_all(db)
        admin = create_admin(db, username="moderator", password_hash="не-настоящий")
        db.commit()
        admin_id = admin.id

        removed = seed_demo.reset(db)
        db.commit()

        assert sum(removed.values()) > 0
        for table in seed_demo.RESET_TABLES:
            rows = db.execute(
                select(func.count()).select_from(Base.metadata.tables[table])
            ).scalar_one()
            assert rows == 0, table
        # The way in stays: a reset that locked the moderator out would be a
        # reset nobody could use.
        assert db.get(Admin, admin_id) is not None

        counts = _run_all(db)

    for name, tally in counts.items():
        assert tally.created > 0, name


def test_the_demo_pins_the_idea_and_the_school_answers_it(session_factory):
    # The showcase post: a student's idea, supported by nearly the whole school
    # and taken up by the administration. It is the reason the pin and the badge
    # exist, so its shape in the data is worth a test of its own.
    from app.models.content import Post
    from app.repositories import posts as posts_repo

    with session_factory() as db:
        _run_all(db)
        featured = db.execute(
            select(Post).where(Post.is_featured.is_(True))
        ).scalars().all()
        idea = next(post for post in featured if post.body.startswith("Идея для школы"))
        likes = likes_repo.count_likes_by_post(db, [idea.id])
        comments = comments_repo.list_visible_comments(db, idea.id)
        top = posts_repo.list_feed_page(db, limit=5, offset=0)

    assert len(featured) == 2, "демо держит два закреплённых поста"
    assert likes[idea.id] > 30, "идею поддерживает почти вся школа"
    assert seed_demo.ADMIN_ALIAS in {comment.display_alias for comment in comments}
    # The idea opens the feed, the announcement follows it: a demo where the
    # order depends on which tuple was written first is a demo nobody can read.
    assert top[0].body.startswith("Идея для школы")
    assert top[1].display_alias == seed_demo.ADMIN_ALIAS


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
