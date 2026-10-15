"""Школьный день: подписи дней, «сегодня» и виджет на главной.

Здесь нет ни базы, ни запросов -- только раскладка уже прочитанных строк по
дням и словари подписей. Разделение то же, что у отзывов об учителях:
подписи -- данные в одном месте, а не строки, разбросанные по шаблонам.
"""

from datetime import date, datetime, timezone

from app.models.school import SCHOOL_DAYS

# Подписи дней идут в порядке ISO-номеров: 1 -- понедельник.
DAY_LABELS: dict[int, str] = {
    1: "Понедельник",
    2: "Вторник",
    3: "Среда",
    4: "Четверг",
    5: "Пятница",
    6: "Суббота",
}

# Короткие подписи для узких экранов и для виджета на главной.
DAY_SHORT: dict[int, str] = {
    1: "Пн",
    2: "Вт",
    3: "Ср",
    4: "Чт",
    5: "Пт",
    6: "Сб",
}

# Винительный падеж -- для фраз вида «расписание на вторник». Подставлять сюда
# именительный нельзя: получится «расписание на среда». Отдельный словарь
# вместо правил склонения: дней шесть, и они не меняются.
DAY_ACCUSATIVE: dict[int, str] = {
    1: "понедельник",
    2: "вторник",
    3: "среду",
    4: "четверг",
    5: "пятницу",
    6: "субботу",
}


def today_weekday(now: datetime | None = None) -> int | None:
    """Номер сегодняшнего учебного дня или None в воскресенье.

    ``None`` -- это не ошибка, а честный ответ: в воскресенье уроков нет, и
    виджет на главной должен сказать именно это, а не показать понедельник.
    """
    current = now or datetime.now(timezone.utc)
    weekday = current.isoweekday()
    return weekday if weekday in SCHOOL_DAYS else None


def group_by_day(entries) -> list[tuple[int, str, list]]:
    """Раскладка недели: список ``(номер дня, подпись, уроки)``.

    Дни без уроков не выпадают из списка -- иначе по странице не понять, что
    в среду занятий нет вообще, а не что среда потерялась.
    """
    by_day: dict[int, list] = {day: [] for day in SCHOOL_DAYS}
    for entry in entries:
        by_day.setdefault(entry.day_of_week, []).append(entry)
    return [
        (day, DAY_LABELS.get(day, str(day)), by_day.get(day, []))
        for day in sorted(by_day)
    ]


def lesson_time(entry) -> str:
    """«08:30–09:15» одной строкой -- в шаблон время приходит уже собранным."""
    return f"{entry.starts_at}–{entry.ends_at}"


def day_summary(db, class_name: str | None, *, now: datetime | None = None) -> dict:
    """Сводка для виджета на главной: уроки сегодня и ближайшая домашка.

    Живёт здесь, а не в роутере ленты: виджет показывает школьный день, и
    лента не должна знать ни про расписание, ни про таблицы домашки. Пустой
    класс -- это не ошибка, а обычный случай: виджет тогда молчит.

    ``date`` -- день, о котором сводка: его спрашивает экран класса, чтобы
    подсветить задание, сдавать которое уже сегодня. Часы в одном месте, а не
    у каждого вызывающего.
    """
    # Импорт внутри функции: модуль читается лентой на каждом запросе, а база
    # нужна только этому вызову. Заодно не возникает цикла импортов
    # repositories -> services.
    from app.repositories import schedule as schedule_repo

    current = now or datetime.now(timezone.utc)
    today = current.date()
    if not class_name:
        return {
            "class_name": None,
            "date": today,
            "lessons": [],
            "homework": [],
            "weekday": None,
        }

    weekday = today_weekday(current)
    return {
        "class_name": class_name,
        "date": today,
        "weekday": weekday,
        "lessons": (
            schedule_repo.list_for_day(db, class_name, weekday) if weekday else []
        ),
        "homework": upcoming_homework(
            schedule_repo.homework_for_class(db, class_name, since=today),
            today=today,
        ),
    }


def upcoming_homework(homework, *, today: date, limit: int = 4) -> list:
    """Ближайшие задания: только сегодняшние и будущие, самое срочное первым."""
    due = [item for item in homework if item.due_date >= today]
    due.sort(key=lambda item: (item.due_date, item.subject))
    return due[:limit]


def is_due_today(item, *, today: date) -> bool:
    return item.due_date == today
