#!/usr/bin/env python
"""Load teachers and review criteria from a JSON data file (idempotent).

The school's staff and the questions a review is scored on are *data*: nothing
about either is written into the code. This script is the only thing that puts
them into the database, and it can be re-run at any time -- a teacher is matched
by name and a criterion by code, so running it twice updates rows in place
instead of duplicating them.

File format (see ``data/teachers.demo.json`` for a complete example)::

    {
      "criteria": [
        {"code": "explains", "label": "Понятно объясняет",
         "scale_min": 1, "scale_max": 5, "position": 10, "is_active": true}
      ],
      "teachers": [
        {"display_name": "Имя Фамилия", "subject": "Математика", "is_active": true}
      ]
    }

``code`` must be unique among criteria and ``display_name`` among teachers --
they are the keys the script matches on. ``scale_min``/``scale_max`` describe
the rating control on the page, so a criterion may use any scale; ``position``
orders the criteria (default: 10, 20, 30, ... in file order); ``is_active``
takes a record off the site without losing its reviews.

By default the script reads ``TEACHERS_DATA_FILE`` (``data/teachers.local.json``
-- deliberately outside the repository, because real names of school staff are
personal data) and falls back to the committed demo file with invented names if
that one does not exist. ``--prune`` deactivates teachers missing from the file;
nothing is ever deleted, since reviews point at teacher rows.

Usage:
    .venv/bin/python scripts/seed_teachers.py
    .venv/bin/python scripts/seed_teachers.py --file data/teachers.local.json
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import _absolute, settings  # noqa: E402
from app.db.base import SessionLocal  # noqa: E402
from app.repositories import criteria as criteria_repo  # noqa: E402
from app.repositories import teachers as teachers_repo  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEMO_FILE = PROJECT_ROOT / "data" / "teachers.demo.json"

# Top-level keys the format knows about; anything else is a typo, not a feature.
_KNOWN_KEYS = {"note", "criteria", "teachers"}


class DataError(Exception):
    """The data file cannot be used as written."""


def _field(item: dict[str, Any], key: str, where: str, *, required: bool) -> Any:
    """Read one field, complaining about typos in the surrounding object."""
    if key not in item:
        if required:
            raise DataError(f"{where}: обязательное поле '{key}' отсутствует")
        return None
    value = item[key]
    if required and (value is None or value == ""):
        raise DataError(f"{where}: поле '{key}' не может быть пустым")
    return value


def _string(item: dict[str, Any], key: str, where: str, *, required: bool = False) -> str | None:
    value = _field(item, key, where, required=required)
    if value is None:
        return None
    if not isinstance(value, str):
        raise DataError(f"{where}: поле '{key}' должно быть строкой")
    return value


def _integer(item: dict[str, Any], key: str, where: str, *, default: int) -> int:
    value = _field(item, key, where, required=False)
    if value is None:
        return default
    # bool is an int in Python, but it is not what the format means here.
    if not isinstance(value, int) or isinstance(value, bool):
        raise DataError(f"{where}: поле '{key}' должно быть целым числом")
    return value


def _boolean(item: dict[str, Any], key: str, where: str, *, default: bool) -> bool:
    value = _field(item, key, where, required=False)
    if value is None:
        return default
    if not isinstance(value, bool):
        raise DataError(f"{where}: поле '{key}' должно быть true или false")
    return value


def _check_keys(item: dict[str, Any], allowed: set[str], where: str) -> None:
    unknown = sorted(set(item) - allowed)
    if unknown:
        raise DataError(f"{where}: неизвестные поля: {', '.join(unknown)}")


def _list(raw: dict[str, Any], key: str, where: str) -> list[Any]:
    value = _field(raw, key, where, required=True)
    if not isinstance(value, list):
        raise DataError(f"{where}: поле '{key}' должно быть списком")
    return value


def load_data(path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Read and validate the file, returning ``(criteria, teachers)``."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise DataError(f"файл не найден: {path}") from None
    except json.JSONDecodeError as error:
        raise DataError(f"{path}: некорректный JSON ({error})") from None

    if not isinstance(raw, dict):
        raise DataError(f"{path}: ожидается объект JSON верхнего уровня")
    _check_keys(raw, _KNOWN_KEYS, str(path))

    criteria: list[dict[str, Any]] = []
    seen_codes: set[str] = set()
    for index, item in enumerate(_list(raw, "criteria", str(path))):
        where = f"criteria[{index}]"
        if not isinstance(item, dict):
            raise DataError(f"{where}: ожидается объект")
        _check_keys(
            item,
            {"code", "label", "scale_min", "scale_max", "position", "is_active"},
            where,
        )
        code = _string(item, "code", where, required=True)
        label = _string(item, "label", where, required=True)
        scale_min = _integer(item, "scale_min", where, default=1)
        scale_max = _integer(item, "scale_max", where, default=5)
        if code in seen_codes:
            raise DataError(f"{where}: код '{code}' повторяется")
        if scale_min > scale_max:
            raise DataError(f"{where}: scale_min больше scale_max")
        seen_codes.add(code)  # type: ignore[arg-type]
        criteria.append(
            {
                "code": code,
                "label": label,
                "scale_min": scale_min,
                "scale_max": scale_max,
                "position": _integer(item, "position", where, default=(index + 1) * 10),
                "is_active": _boolean(item, "is_active", where, default=True),
            }
        )

    teachers: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    for index, item in enumerate(_list(raw, "teachers", str(path))):
        where = f"teachers[{index}]"
        if not isinstance(item, dict):
            raise DataError(f"{where}: ожидается объект")
        _check_keys(item, {"display_name", "subject", "is_active"}, where)
        display_name = _string(item, "display_name", where, required=True)
        if display_name in seen_names:
            raise DataError(f"{where}: имя '{display_name}' повторяется")
        seen_names.add(display_name)  # type: ignore[arg-type]
        teachers.append(
            {
                "display_name": display_name,
                "subject": _string(item, "subject", where),
                "is_active": _boolean(item, "is_active", where, default=True),
            }
        )

    return criteria, teachers


def seed(db, criteria, teachers, *, prune: bool) -> tuple[int, int, int, int, int]:
    """Write the data through the repositories; return the counters."""
    criteria_created = criteria_updated = 0
    for criterion in criteria:
        _, created = criteria_repo.upsert_criterion(db, **criterion)
        criteria_created += created
        criteria_updated += not created

    teachers_created = teachers_updated = 0
    for teacher in teachers:
        _, created = teachers_repo.upsert_teacher(db, **teacher)
        teachers_created += created
        teachers_updated += not created

    deactivated = 0
    if prune:
        names = {teacher["display_name"] for teacher in teachers}
        deactivated = len(teachers_repo.deactivate_missing(db, keep_names=names))

    db.commit()
    return (
        criteria_created,
        criteria_updated,
        teachers_created,
        teachers_updated,
        deactivated,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Load teachers and review criteria from a JSON file."
    )
    parser.add_argument(
        "--file",
        default=None,
        help=f"Path to the data file (defaults to TEACHERS_DATA_FILE, then {DEMO_FILE}).",
    )
    parser.add_argument(
        "--prune",
        action="store_true",
        help="Also deactivate teachers that are not in the file (nothing is deleted).",
    )
    args = parser.parse_args(argv)

    # The configured path is resolved against the project root, not the current
    # directory: the script is also run from a service whose cwd is not the repo
    # (see app.config._absolute). An explicit --file is taken as given.
    path = Path(args.file) if args.file else Path(_absolute(settings.TEACHERS_DATA_FILE))
    if not path.exists() and args.file is None:
        print(
            f"Файл {path} не найден — беру демо-данные {DEMO_FILE.name} "
            "(имена выдуманы).",
            file=sys.stderr,
        )
        path = DEMO_FILE

    try:
        criteria, teachers = load_data(path)
    except DataError as error:
        print(f"Ошибка данных: {error}", file=sys.stderr)
        return 2

    with SessionLocal() as db:
        created_c, updated_c, created_t, updated_t, deactivated = seed(
            db, criteria, teachers, prune=args.prune
        )

    print(
        f"{path}: критерии — создано {created_c}, обновлено {updated_c}; "
        f"учителя — создано {created_t}, обновлено {updated_t}"
        + (f", отключено {deactivated}" if args.prune else "")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
