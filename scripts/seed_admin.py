#!/usr/bin/env python
"""Create the first admin account (idempotent).

Credentials come from the command line (``--username`` / ``--password``) or,
if not given, from the ``ADMIN_USERNAME`` / ``ADMIN_PASSWORD`` settings.

The script never prints or stores the password in plaintext and never changes
an existing admin's password: if an admin with the given username already
exists, it reports that and exits.

Usage:
    .venv/bin/python scripts/seed_admin.py --username admin --password '...'
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.db.base import SessionLocal  # noqa: E402
from app.repositories.admins import create_admin, get_admin_by_username  # noqa: E402

MIN_PASSWORD_LENGTH = 12


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create the first admin account.")
    parser.add_argument(
        "--username",
        default=None,
        help="Admin username (defaults to ADMIN_USERNAME).",
    )
    parser.add_argument(
        "--password",
        default=None,
        help="Admin password (defaults to ADMIN_PASSWORD).",
    )
    args = parser.parse_args(argv)

    username = args.username or settings.ADMIN_USERNAME
    password = args.password or settings.ADMIN_PASSWORD

    if not username:
        print(
            "Ошибка: не задан логин (--username или ADMIN_USERNAME).",
            file=sys.stderr,
        )
        return 2

    if not password or len(password) < MIN_PASSWORD_LENGTH:
        print(
            f"Ошибка: пароль должен быть не короче {MIN_PASSWORD_LENGTH} символов.",
            file=sys.stderr,
        )
        return 2

    with SessionLocal() as db:
        if get_admin_by_username(db, username) is not None:
            print(f"Админ '{username}' уже существует — изменение пароля пропущено.")
            return 0

        # This script is the calling layer: it owns the commit.
        create_admin(db, username=username, password_hash=hash_password(password))
        db.commit()

    print(f"Создан админ: {username}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
