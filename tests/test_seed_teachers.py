"""Tests for the teacher/criteria data file and the seeding script.

Teachers and criteria are data, so most of what can go wrong is a malformed file
or a re-run that duplicates rows -- that is what these tests pin down. The
script's database work is driven directly through ``seed()`` with the test
session, not through ``main()``, which would reach for the real ``SessionLocal``.
"""

import json
import sys
from pathlib import Path

from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models.teacher import Criterion, Teacher  # noqa: E402
from app.repositories import criteria as criteria_repo  # noqa: E402
from scripts import seed_teachers  # noqa: E402

DEMO_FILE = Path(__file__).resolve().parents[1] / "data" / "teachers.demo.json"


def _write(tmp_path, data):
    path = tmp_path / "teachers.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def _minimal(criteria=None, teachers=None):
    return {
        "criteria": criteria if criteria is not None else [
            {"code": "explains", "label": "Понятно объясняет"}
        ],
        "teachers": teachers if teachers is not None else [
            {"display_name": "Тестов Учитель", "subject": "Физика"}
        ],
    }


def test_demo_file_is_valid_and_has_invented_names():
    criteria, teachers = seed_teachers.load_data(DEMO_FILE)
    assert len(criteria) == 4
    assert len(teachers) == 4
    # The demo file must never look like a real member of staff.
    assert all("demo" in teacher["display_name"].lower() or "(демо)" in
               teacher["display_name"].lower() for teacher in teachers)


def test_defaults_are_filled_in(tmp_path):
    criteria, teachers = seed_teachers.load_data(_write(tmp_path, _minimal()))
    assert criteria[0]["scale_min"] == 1
    assert criteria[0]["scale_max"] == 5
    assert criteria[0]["position"] == 10
    assert criteria[0]["is_active"] is True
    assert teachers[0]["subject"] == "Физика"
    assert teachers[0]["is_active"] is True


def test_unknown_field_is_rejected(tmp_path):
    data = _minimal()
    data["teachers"][0]["subjects"] = "опечатка"
    try:
        seed_teachers.load_data(_write(tmp_path, data))
    except seed_teachers.DataError as error:
        assert "subjects" in str(error)
    else:  # pragma: no cover - the assertion below is the point
        raise AssertionError("unknown field was accepted")


def test_duplicate_code_is_rejected(tmp_path):
    data = _minimal(criteria=[
        {"code": "same", "label": "Раз"},
        {"code": "same", "label": "Два"},
    ])
    try:
        seed_teachers.load_data(_write(tmp_path, data))
    except seed_teachers.DataError as error:
        assert "повторяется" in str(error)
    else:  # pragma: no cover
        raise AssertionError("duplicate code was accepted")


def test_missing_required_field_is_rejected(tmp_path):
    data = _minimal(teachers=[{"subject": "Физика"}])
    try:
        seed_teachers.load_data(_write(tmp_path, data))
    except seed_teachers.DataError as error:
        assert "display_name" in str(error)
    else:  # pragma: no cover
        raise AssertionError("a nameless teacher was accepted")


def test_broken_scale_is_rejected(tmp_path):
    data = _minimal(criteria=[
        {"code": "x", "label": "Что-то", "scale_min": 5, "scale_max": 1}
    ])
    try:
        seed_teachers.load_data(_write(tmp_path, data))
    except seed_teachers.DataError as error:
        assert "scale_min" in str(error)
    else:  # pragma: no cover
        raise AssertionError("an inverted scale was accepted")


def test_broken_json_is_reported_not_raised_through(tmp_path):
    path = tmp_path / "teachers.json"
    path.write_text("{не json", encoding="utf-8")
    try:
        seed_teachers.load_data(path)
    except seed_teachers.DataError as error:
        assert "JSON" in str(error)
    else:  # pragma: no cover
        raise AssertionError("broken JSON was accepted")


def test_missing_file_is_reported(tmp_path):
    try:
        seed_teachers.load_data(tmp_path / "нет.json")
    except seed_teachers.DataError as error:
        assert "не найден" in str(error)
    else:  # pragma: no cover
        raise AssertionError("a missing file was accepted")


def test_seeding_twice_updates_in_place(session_factory, tmp_path):
    criteria, teachers = seed_teachers.load_data(
        _write(tmp_path, _minimal(criteria=[
            {"code": "explains", "label": "Как объясняет"}
        ]))
    )
    with session_factory() as db:
        seed_teachers.seed(db, criteria, teachers, prune=False)
    with session_factory() as db:
        seed_teachers.seed(db, criteria, teachers, prune=False)
        assert len(db.execute(select(Criterion)).scalars().all()) == 1
        assert len(db.execute(select(Teacher)).scalars().all()) == 1

    # The same code with a new label and a new scale is an update, not a copy.
    changed, _ = seed_teachers.load_data(
        _write(tmp_path, _minimal(criteria=[
            {"code": "explains", "label": "Насколько понятно", "scale_max": 10}
        ]))
    )
    with session_factory() as db:
        seed_teachers.seed(db, changed, teachers, prune=False)
        criterion = db.execute(select(Criterion)).scalars().one()
        assert criterion.label == "Насколько понятно"
        assert criterion.scale_max == 10
        assert len(db.execute(select(Teacher)).scalars().all()) == 1


def test_prune_deactivates_but_never_deletes(session_factory, tmp_path):
    criteria, teachers = seed_teachers.load_data(
        _write(tmp_path, _minimal(teachers=[
            {"display_name": "Первый"},
            {"display_name": "Второй"},
        ]))
    )
    with session_factory() as db:
        seed_teachers.seed(db, criteria, teachers, prune=False)

    # "Второй" disappears from the file: with --prune they leave the site.
    with session_factory() as db:
        seed_teachers.seed(db, criteria, teachers[:1], prune=True)
        remaining = db.execute(select(Teacher)).scalars().all()
        assert len(remaining) == 2                       # the row is still there
        by_name = {teacher.display_name: teacher for teacher in remaining}
        assert by_name["Первый"].is_active is True
        assert by_name["Второй"].is_active is False

    # Without --prune nothing is switched off.
    with session_factory() as db:
        seed_teachers.seed(db, criteria, teachers, prune=False)
        assert all(
            teacher.is_active
            for teacher in db.execute(select(Teacher)).scalars().all()
        )


def test_upsert_helpers_are_indempotent(session_factory):
    with session_factory() as db:
        criteria_repo.upsert_criterion(
            db, code="c", label="Раз", scale_min=1, scale_max=5
        )
        db.commit()
    with session_factory() as db:
        _, created = criteria_repo.upsert_criterion(
            db, code="c", label="Два", scale_min=0, scale_max=2
        )
        db.commit()
        assert created is False
        criterion = db.execute(select(Criterion)).scalars().one()
        assert (criterion.label, criterion.scale_min, criterion.scale_max) == (
            "Два", 0, 2,
        )
