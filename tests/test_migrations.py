"""Миграции, собранные с нуля, дают ту же схему, что модели.

Пустая база, поднятая `alembic upgrade head`, обязана совпадать с
`Base.metadata`: расхождение миграций с моделями не видно ни в коде, ни в
тестах на готовой базе — оно всплывает только на свежей установке (или в
`alembic check`), и до деплоя о нём знать неоткуда. Именно так вышло с 0009:
она объявила правило «одна заявка на ученика» дважды — констрейнтом и обычным
индексом, — а модель просит один уникальный индекс.
"""

from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine

from app.config import settings
from app.db.base import Base

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def empty_database(tmp_path, monkeypatch):
    """Адрес пустого файла SQLite — тот, куда смотрит и alembic, и тест."""
    url = f"sqlite:///{tmp_path / 'migrations.db'}"
    # migrations/env.py берёт адрес из настроек приложения, а не из alembic.ini,
    # поэтому одного этого подменённого значения хватает: school.db не тронут.
    monkeypatch.setattr(settings, "DATABASE_URL", url)
    return url


def test_upgrade_head_matches_models(empty_database):
    """Вся цепочка миграций не оставляет ни одной операции для автогенерации."""
    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")

    engine = create_engine(empty_database)
    with engine.connect() as connection:
        context = MigrationContext.configure(connection)
        diff = compare_metadata(context, Base.metadata)

    # Непустой diff — это ровно то, что alembic check печатает как дрейф:
    # ('add_index', ...), ('remove_constraint', ...) и прочие пары операций.
    assert diff == []
