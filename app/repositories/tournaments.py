"""Доступ к турнирам и бейджам.

Слой читает и умеет ``upsert``: турниры и награды заливает сидер
(``scripts/seed_demo.py``), в интерфейсе их не заводят. Коммит -- не здесь.
"""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.esports import StudentBadge, Tournament


def list_tournaments(db: Session, *, limit: int = 30) -> list[Tournament]:
    """Турниры: сначала те, что ещё впереди, затем прошедшие.

    Сортировка по ``starts_at`` «по убыванию», но с NULL-датой вперёд: у
    турнира без даты она ещё не назначена, и показывать его среди прошедших
    было бы неверно.
    """
    stmt = (
        select(Tournament)
        .order_by(Tournament.starts_at.desc().nulls_first(), Tournament.id.desc())
        .limit(limit)
    )
    return list(db.execute(stmt).scalars().all())


def get_tournament(db: Session, tournament_id: int) -> Tournament | None:
    return db.get(Tournament, tournament_id)


def badges_by_tournament(
    db: Session, tournament_ids: list[int]
) -> dict[int, list[StudentBadge]]:
    """Бейджи всех перечисленных турниров одним запросом (без N+1)."""
    if not tournament_ids:
        return {}
    stmt = select(StudentBadge).where(
        StudentBadge.tournament_id.in_(tournament_ids)
    )
    grouped: dict[int, list[StudentBadge]] = {}
    for badge in db.execute(stmt).scalars().all():
        grouped.setdefault(badge.tournament_id, []).append(badge)
    return grouped


def list_badges_for_student(db: Session, student_id: int) -> list[StudentBadge]:
    stmt = (
        select(StudentBadge)
        .where(StudentBadge.student_id == student_id)
        .order_by(StudentBadge.awarded_at.desc())
    )
    return list(db.execute(stmt).scalars().all())


def tournaments_by_id(
    db: Session, tournament_ids: list[int]
) -> dict[int, Tournament]:
    """Турниры по списку id одним запросом -- для карточек бейджей в профиле."""
    if not tournament_ids:
        return {}
    stmt = select(Tournament).where(Tournament.id.in_(tournament_ids))
    return {t.id: t for t in db.execute(stmt).scalars().all()}


def upsert_tournament(
    db: Session,
    *,
    game: str,
    title: str,
    format: str | None = None,
    starts_at: datetime | None = None,
    status: str = "upcoming",
    description: str | None = None,
) -> tuple[Tournament, bool]:
    """Вставить или обновить турнир; ключ -- ``(игра, название)``.

    Названия турниров школа придумывает сама, и повторяются они редко, поэтому
    пары «игра + название» хватает как естественного ключа: повторный запуск
    сидера обновляет турнир, а не заводит второй такой же.
    """
    existing = db.execute(
        select(Tournament).where(Tournament.game == game, Tournament.title == title)
    ).scalar_one_or_none()

    if existing is None:
        tournament = Tournament(
            game=game,
            title=title,
            format=format,
            starts_at=starts_at,
            status=status,
            description=description,
        )
        db.add(tournament)
        db.flush()
        return tournament, True

    existing.format = format
    existing.starts_at = starts_at
    existing.status = status
    existing.description = description
    db.flush()
    return existing, False


def upsert_badge(
    db: Session, *, student_id: int, tournament_id: int, code: str
) -> tuple[StudentBadge, bool]:
    """Выдать бейдж; ключ -- ``(ученик, турнир, награда)``, как в индексе.

    Существующий бейдж не переписывается: ``awarded_at`` -- это дата награды,
    и второй запуск сидера не должен её сдвигать.
    """
    existing = db.execute(
        select(StudentBadge).where(
            StudentBadge.student_id == student_id,
            StudentBadge.tournament_id == tournament_id,
            StudentBadge.code == code,
        )
    ).scalar_one_or_none()

    if existing is not None:
        return existing, False

    badge = StudentBadge(
        student_id=student_id, tournament_id=tournament_id, code=code
    )
    db.add(badge)
    db.flush()
    return badge, True
