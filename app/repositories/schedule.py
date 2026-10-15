"""Доступ к расписанию и домашним заданиям.

Слой читает и умеет ``upsert`` -- им пользуется сидер (``scripts/seed_demo.py``);
как и остальные репозитории, он не коммитит, транзакцией владеет вызывающий.
В интерфейсе расписание не редактируют, поэтому запись здесь существует ровно
ради заливки данных. Выборки -- «на класс»: класс ученика и есть единственный
ключ, по которому эти данные различаются.
"""

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.school import Homework, LessonNote, ScheduleEntry


def list_for_class(db: Session, class_name: str) -> list[ScheduleEntry]:
    """Вся неделя класса, в порядке сетки: день, затем номер урока."""
    stmt = (
        select(ScheduleEntry)
        .where(ScheduleEntry.class_name == class_name)
        .order_by(ScheduleEntry.day_of_week, ScheduleEntry.lesson_no)
    )
    return list(db.execute(stmt).scalars().all())


def list_class_names(db: Session) -> list[str]:
    """Классы, по которым у школы есть расписание, -- по разу каждый.

    Отдельного справочника классов нет: класс существует ровно там, где стоит
    его сетка. Из строк, встреченных где попало (в домашке, в заметках), список
    не собирается -- класс без уроков обещал бы расписание, которого нет.

    Порядка здесь нет нарочно: SQLite складывает кириллицу по коду, и обычный
    ``ORDER BY`` поставил бы «10А» перед «8А». Школьный порядок знает сервис
    (``app.services.classes``).
    """
    stmt = select(ScheduleEntry.class_name).distinct()
    return list(db.execute(stmt).scalars().all())


def list_for_day(
    db: Session, class_name: str, day_of_week: int
) -> list[ScheduleEntry]:
    """Уроки класса на один день недели (1..6)."""
    stmt = (
        select(ScheduleEntry)
        .where(
            ScheduleEntry.class_name == class_name,
            ScheduleEntry.day_of_week == day_of_week,
        )
        .order_by(ScheduleEntry.lesson_no)
    )
    return list(db.execute(stmt).scalars().all())


def homework_for_class(
    db: Session, class_name: str, *, since: date, limit: int = 40
) -> list[Homework]:
    """Задания класса со сроком не раньше ``since``, ближайшие первыми."""
    stmt = (
        select(Homework)
        .where(Homework.class_name == class_name, Homework.due_date >= since)
        .order_by(Homework.due_date, Homework.subject)
        .limit(limit)
    )
    return list(db.execute(stmt).scalars().all())


def notes_for_class(
    db: Session, class_name: str, *, on_date: date
) -> list[LessonNote]:
    """Заметки класса на конкретный день (замены, «принести тетрадь»)."""
    stmt = (
        select(LessonNote)
        .where(LessonNote.class_name == class_name, LessonNote.on_date == on_date)
        .order_by(LessonNote.id)
    )
    return list(db.execute(stmt).scalars().all())


def upsert_entry(
    db: Session,
    *,
    class_name: str,
    day_of_week: int,
    lesson_no: int,
    subject: str,
    room: str | None,
    starts_at: str,
    ends_at: str,
) -> tuple[ScheduleEntry, bool]:
    """Вставить или обновить урок сетки.

    Естественный ключ -- ``(класс, день, номер урока)``, ровно та же тройка,
    что держит уникальный индекс: повторный запуск сидера переписывает урок на
    месте вместо того, чтобы завести второй. Возвращает урок и признак
    «создан» (``False`` -- обновлён).
    """
    existing = db.execute(
        select(ScheduleEntry).where(
            ScheduleEntry.class_name == class_name,
            ScheduleEntry.day_of_week == day_of_week,
            ScheduleEntry.lesson_no == lesson_no,
        )
    ).scalar_one_or_none()

    if existing is None:
        entry = ScheduleEntry(
            class_name=class_name,
            day_of_week=day_of_week,
            lesson_no=lesson_no,
            subject=subject,
            room=room,
            starts_at=starts_at,
            ends_at=ends_at,
        )
        db.add(entry)
        db.flush()
        return entry, True

    existing.subject = subject
    existing.room = room
    existing.starts_at = starts_at
    existing.ends_at = ends_at
    db.flush()
    return existing, False


def upsert_homework(
    db: Session,
    *,
    class_name: str,
    subject: str,
    due_date: date,
    body: str,
) -> tuple[Homework, bool]:
    """Вставить или обновить задание.

    Ключ -- ``(класс, предмет)``: у демо-класса на предмет приходится одно
    актуальное задание, и повторный запуск сидера на другой день сдвигает срок
    вместо того, чтобы оставить рядом вчерашнюю строку. Для настоящей школы с
    несколькими заданиями на предмет ключ был бы другим.
    """
    existing = db.execute(
        select(Homework).where(
            Homework.class_name == class_name, Homework.subject == subject
        )
    ).scalar_one_or_none()

    if existing is None:
        homework = Homework(
            class_name=class_name, subject=subject, due_date=due_date, body=body
        )
        db.add(homework)
        db.flush()
        return homework, True

    existing.due_date = due_date
    existing.body = body
    db.flush()
    return existing, False


def upsert_note(
    db: Session, *, class_name: str, on_date: date, body: str
) -> tuple[LessonNote, bool]:
    """Вставить или обновить заметку класса.

    Ключ -- ``(класс, текст)``, а не ``(класс, дата)``: заметок на день у класса
    бывает несколько (замена по физике и сменка на физкультуру), и ключ по дате
    затирал бы все, кроме последней. Дата у существующей заметки обновляется --
    сидер считает свои заметки «на сегодня», и повторный запуск сдвигает день,
    а не заводит вторую такую же строку.
    """
    existing = db.execute(
        select(LessonNote).where(
            LessonNote.class_name == class_name, LessonNote.body == body
        )
    ).scalar_one_or_none()

    if existing is None:
        note = LessonNote(class_name=class_name, on_date=on_date, body=body)
        db.add(note)
        db.flush()
        return note, True

    existing.on_date = on_date
    db.flush()
    return existing, False
