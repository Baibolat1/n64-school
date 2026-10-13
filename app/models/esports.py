"""Киберспорт: турниры школы и бейджи, которые ученики на них получили.

Турнир -- это запись о событии (игра, формат, когда), а бейдж -- ребро
«ученик × турнир × награда». Ребро, а не колонка в ``students``: бейдж
присваивается за конкретный турнир, и один ученик может быть и MVP, и
капитаном, причём в разных турнирах. Счётчики на ученике разошлись бы с
фактами, а строки с ограничением -- нет.
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# Игры, в которые играет школа. Коды, а не названия: подпись живёт в
# ``app/services/esports.py`` и не дублируется в шаблонах.
GAME_CODES = ("cs2", "brawl")

# Награды. ``mvp`` и ``captain`` -- не единственные возможные: список
# открытый, поэтому неизвестный код не роняет страницу, а показывается как есть.
BADGE_CODES = ("mvp", "captain", "winner")

TOURNAMENT_STATUSES = ("upcoming", "live", "finished")


class Tournament(Base):
    __tablename__ = "tournaments"

    id: Mapped[int] = mapped_column(primary_key=True)
    game: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    # «5x5, BO3» -- свободный текст: форматов больше, чем стоит заводить кодов.
    format: Mapped[str | None] = mapped_column(Text, nullable=True)
    starts_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default="upcoming", index=True
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class StudentBadge(Base):
    __tablename__ = "student_badges"
    __table_args__ = (
        # Один и тот же бейдж за один турнир не выдаётся дважды -- это держит
        # база, а не проверка «прочитал-записал».
        UniqueConstraint(
            "student_id", "tournament_id", "code", name="uq_student_badges_unique"
        ),
        Index("ix_student_badges_student", "student_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(
        ForeignKey("students.id"), nullable=False, index=True
    )
    tournament_id: Mapped[int] = mapped_column(
        ForeignKey("tournaments.id"), nullable=False, index=True
    )
    code: Mapped[str] = mapped_column(Text, nullable=False)
    awarded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
