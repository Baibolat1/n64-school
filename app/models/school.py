"""Расписание уроков и домашние задания -- данные школы, а не код.

Обе таблицы описывают *школьный день*, а не ученика: расписание -- это
сетка «класс × день недели × номер урока», домашнее задание -- строка на
«класс × предмет × дата сдачи». Никаких идентификаторов учеников здесь нет,
поэтому заливка данных (``scripts/seed_demo.py``) их не касается приватности:
персональных данных в этих строках не больше, чем на доске объявлений.

Время урока -- текст ``"08:30"``, а не ``Time``: с ним сравнивает только
шаблон, а разбор ``"08:30"`` в ``time`` добавил бы тип ради одной сортировки,
которая и так идёт по ``lesson_no``.
"""

from datetime import date, datetime

from sqlalchemy import Date, DateTime, Index, Integer, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# Дни недели -- числа 1..6 (понедельник..суббота), как в ISO. Воскресенья в
# школьном расписании нет, поэтому седьмого значения не существует.
SCHOOL_DAYS = (1, 2, 3, 4, 5, 6)


class ScheduleEntry(Base):
    """Один урок в сетке: «класс, день, номер урока» уникальны."""

    __tablename__ = "schedule_entries"
    __table_args__ = (
        Index(
            "uq_schedule_class_day_lesson",
            "class_name",
            "day_of_week",
            "lesson_no",
            unique=True,
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # «9А», «10Б» -- свободный текст: список классов ведёт школа, а не код.
    class_name: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    day_of_week: Mapped[int] = mapped_column(Integer, nullable=False)
    lesson_no: Mapped[int] = mapped_column(Integer, nullable=False)
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    # Кабинет необязателен: у физкультуры он «спортзал», у урока на улице его нет.
    room: Mapped[str | None] = mapped_column(Text, nullable=True)
    starts_at: Mapped[str] = mapped_column(Text, nullable=False)
    ends_at: Mapped[str] = mapped_column(Text, nullable=False)


class Homework(Base):
    """Домашнее задание: что задано классу и к какому дню."""

    __tablename__ = "homework"
    __table_args__ = (
        Index("ix_homework_class_due", "class_name", "due_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    class_name: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    due_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class LessonNote(Base):
    """Заметка к уроку: замены, кабинет, «принести тетрадь».

    Отдельная таблица, а не колонка в ``schedule_entries``: заметка живёт
    ровно на один день и не должна переписывать саму сетку расписания.
    """

    __tablename__ = "lesson_notes"

    id: Mapped[int] = mapped_column(primary_key=True)
    class_name: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    on_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
