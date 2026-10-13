#!/usr/bin/env python
"""Заполняет сайт демонстрационными данными (идемпотентно).

Заливает то, без чего новый сайт выглядит пустым: расписание и домашние
задания классов, турниры школы с наградами, профили учеников с аватарками и
несколько постов с комментариями и лайками.

Данные выдуманы и лежат прямо в скрипте, а не в JSON-файле, как у учителей:
это витрина для демонстрации, а не данные школы, и настоящие имена в неё
попасть не должны. Файл данных здесь был бы ещё одним местом, куда их можно
случайно вписать.

Повторный запуск ничего не дублирует: у каждой таблицы свой естественный ключ
(класс и урок сетки, игра и название турнира, псевдоним ученика), и строки
обновляются на месте. Посты опознаются по тексту, который у каждого свой.

Домашние задания и заметки к уроку считаются от *сегодняшнего* дня, поэтому
демонстрация не устаревает: задание всегда «на завтра», заметка -- «на
сегодня». Повторный запуск на другой день сдвигает срок задания, а не оставляет
рядом вчерашнее.

Usage:
    .venv/bin/python scripts/seed_demo.py
    .venv/bin/python scripts/seed_demo.py --with-login
"""

import argparse
import secrets
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.security import hash_password  # noqa: E402
from app.db.base import SessionLocal  # noqa: E402
from app.repositories import comments as comments_repo  # noqa: E402
from app.repositories import likes as likes_repo  # noqa: E402
from app.repositories import posts as posts_repo  # noqa: E402
from app.repositories import schedule as schedule_repo  # noqa: E402
from app.repositories import students as students_repo  # noqa: E402
from app.repositories import tournaments as tournaments_repo  # noqa: E402
from app.services import publication  # noqa: E402

# Звонки: номер урока и его начало с концом. Одно расписание на всю школу --
# так и бывает в жизни, а свой звонок на класс был бы отдельной таблицей.
BELL = (
    (1, "08:30", "09:15"),
    (2, "09:25", "10:10"),
    (3, "10:25", "11:10"),
    (4, "11:25", "12:10"),
    (5, "12:25", "13:10"),
    (6, "13:20", "14:05"),
)

# Кабинет по предмету: он один и тот же всю неделю, поэтому не повторяется в
# сетке. Предмета здесь может и не быть -- тогда урока без кабинета.
SUBJECT_ROOMS = {
    "Математика": "каб. 24",
    "Алгебра": "каб. 24",
    "Геометрия": "каб. 24",
    "Русский язык": "каб. 12",
    "Литература": "каб. 12",
    "Физика": "каб. 31",
    "Химия": "каб. 28",
    "Биология": "каб. 19",
    "История": "каб. 18",
    "Обществознание": "каб. 18",
    "География": "каб. 22",
    "Английский язык": "каб. 7",
    "Информатика": "каб. 33",
    "ОБЖ": "каб. 15",
    "Физкультура": "спортзал",
    "Музыка": "актовый зал",
    "ИЗО": "каб. 26",
    "Технология": "мастерская",
}

# Класс -> день недели (1..6) -> уроки по порядку через запятую.
WEEK = {
    "9А": {
        1: "Математика, Русский язык, Физика, История, Английский язык, Физкультура",
        2: "Алгебра, Литература, Химия, География, Английский язык, ОБЖ",
        3: "Русский язык, Математика, Биология, История, Информатика, Физкультура",
        4: "Геометрия, Физика, Русский язык, Обществознание, Английский язык",
        5: "Алгебра, Литература, Химия, География, Музыка, Физкультура",
        6: "История, Математика, Биология, ИЗО, Технология",
    },
    "10Б": {
        1: "Алгебра, Литература, Физика, Информатика, Английский язык",
        2: "Геометрия, Русский язык, Химия, История, Физкультура",
        3: "Математика, Биология, Обществознание, Английский язык, ОБЖ",
        4: "Физика, Литература, Информатика, География, Технология",
        5: "Алгебра, Русский язык, История, Химия, Физкультура",
    },
}

# Домашнее задание: класс, предмет, через сколько дней сдавать, что задано.
HOMEWORK = (
    ("9А", "Математика", 1, "§12, №345–348 (б, г)"),
    ("9А", "Русский язык", 1, "Упр. 178, повторить правило о причастном обороте"),
    ("9А", "Физика", 2, "Конспект §21, задачи 4 и 5 после параграфа"),
    ("9А", "История", 3, "Параграф 14, выписать даты реформ"),
    ("9А", "Английский язык", 1, "Unit 5: слова 5a, упражнение 3 письменно"),
    ("10Б", "Алгебра", 1, "№812–815, готовиться к самостоятельной работе"),
    ("10Б", "Физика", 2, "Лабораторная: оформить таблицу измерений"),
)

# Заметки к уроку на сегодня: замены и «принести с собой».
NOTES = (
    ("9А", "Физика — замена: будет геометрия, каб. 24"),
    ("9А", "Физкультура: принести сменную обувь"),
)

# Турниры: игра, название, формат, через сколько дней, статус, описание.
TOURNAMENTS = (
    (
        "brawl",
        "Brawl Stars: кубок школы 3×3",
        "3×3, BO1",
        0,
        "live",
        "Играют сборные 9-х и 10-х классов. Матчи идут в актовом зале, "
        "финал — сразу после уроков.",
    ),
    (
        "cs2",
        "Осенний кубок по CS2",
        "5×5, BO3",
        6,
        "upcoming",
        "Школьная сетка на восемь команд. Заявку подаёт капитан команды, "
        "тренировки по вторникам и четвергам.",
    ),
    (
        "cs2",
        "Летний турнир по CS2",
        "5×5, BO1",
        -42,
        "finished",
        "Первый турнир года: восемь команд, играли до последнего раунда.",
    ),
    (
        "brawl",
        "Зимний блиц Brawl Stars",
        "3×3, BO3",
        -96,
        "finished",
        "Быстрый вечерний турнир на 16 команд.",
    ),
)

# Награды: псевдоним, название турнира, код награды.
BADGES = (
    ("Тихий Лис", "Осенний кубок по CS2", "captain"),
    ("Тихий Лис", "Летний турнир по CS2", "mvp"),
    ("Полярная Сова", "Brawl Stars: кубок школы 3×3", "mvp"),
    ("Быстрый Кот", "Brawl Stars: кубок школы 3×3", "captain"),
    ("Лунный Ёж", "Летний турнир по CS2", "captain"),
    ("Зимняя Рысь", "Brawl Stars: кубок школы 3×3", "winner"),
)

# Ученики: псевдоним, класс, картинка из встроенной галереи (см.
# ``app.core.avatars``; ключа нет -- значит кружок с буквой).
STUDENTS = (
    ("Тихий Лис", "9А", "waves"),
    ("Полярная Сова", "9А", "moon"),
    ("Быстрый Кот", "9А", "peaks"),
    ("Лунный Ёж", "9А", "spiral"),
    ("Зимняя Рысь", "10Б", "dawn"),
    ("Ясный Ветер", "10Б", "leaf"),
    ("Добрый Барс", "9А", None),
    ("Северный Кедр", "10Б", "diamond"),
)

# Посты: псевдоним автора, анонимно ли, текст. По тексту пост и опознаётся при
# повторном запуске, поэтому тексты не повторяются. У анонимного поста автор
# всё равно настоящий (анонимность -- это подпись, а не отсутствие автора), но
# в подписи окажется общий «Аноним».
POSTS = (
    ("Тихий Лис", False, "Собрали команду на осенний кубок по CS2. Не хватает "
                         "пятого — пишите в комментарии, тренируемся по вторникам."),
    ("Добрый Барс", True, "Кто-нибудь понял, что задали по геометрии? В расписании "
                          "замену поставили, а в дневник ничего не записали."),
    ("Полярная Сова", False, "В столовой снова появились сырники. Считаю это "
                             "главным событием недели."),
    ("Зимняя Рысь", False, "Открыли запись в школьный медиаклуб: учимся снимать и "
                           "монтировать короткие видео. Первое занятие в пятницу."),
    ("Лунный Ёж", False, "Напоминаю: турнир по Brawl Stars уже идёт, сетка висит "
                         "на первом этаже. Финалы — после уроков."),
)

# Комментарии: номер поста в POSTS (с нуля), псевдоним, анонимно ли, текст.
COMMENTS = (
    (0, "Быстрый Кот", False, "Я в деле, напишу в личку вечером."),
    (0, "Ясный Ветер", True, "А новичков берёте? Играю месяца три."),
    (1, "Добрый Барс", False, "Параграф 12, номера 345–348. В расписании всё верно."),
    (2, "Ясный Ветер", False, "Сырники — это серьёзно. Иду проверять."),
    (4, "Северный Кедр", False, "Сетку видел, у 10Б сильная команда."),
)

# Лайки: номер поста, псевдоним.
LIKES = (
    (0, "Полярная Сова"),
    (0, "Быстрый Кот"),
    (0, "Добрый Барс"),
    (1, "Тихий Лис"),
    (2, "Лунный Ёж"),
    (2, "Зимняя Рысь"),
    (2, "Ясный Ветер"),
    (3, "Полярная Сова"),
    (4, "Быстрый Кот"),
    (4, "Северный Кедр"),
)

# Аккаунт для демонстрации: к нему привязываются пароль и адрес по флагу
# --with-login. Псевдоним -- первый в STUDENTS, чтобы у входа был профиль с
# расписанием, наградами и постами.
LOGIN_ALIAS = "Тихий Лис"
LOGIN_EMAIL = "demo@n64hub.example"


class Tally:
    """Счётчик «создано / обновлено» для одного вида данных."""

    def __init__(self) -> None:
        self.created = 0
        self.updated = 0

    def add(self, created: bool) -> None:
        if created:
            self.created += 1
        else:
            self.updated += 1


def _student(db, alias: str, where: str):
    """Ученик по псевдониму или понятная ошибка данных.

    Опечатка в псевдониме должна останавливать сидер, а не создавать половину
    данных молча: без ученика пост или награду записать некуда, и «тихо
    пропустить» здесь значит получить пустую ленту и час поисков причины.
    """
    student = students_repo.get_student_by_alias(db, alias)
    if student is None:
        raise ValueError(f"{where}: ученик {alias!r} не найден")
    return student


def seed_school(db, *, today) -> dict[str, Tally]:
    """Расписание, домашние задания и заметки -- по всем классам из WEEK."""
    entries, homework, notes = Tally(), Tally(), Tally()

    for class_name, week in WEEK.items():
        for day_of_week, subjects in week.items():
            for lesson_no, subject in enumerate(subjects.split(", "), start=1):
                bell = next(
                    (bell for bell in BELL if bell[0] == lesson_no), None
                )
                if bell is None:
                    # В звонков меньше уроков, чем в сетке: это ошибка данных,
                    # а не молча пропущенный урок.
                    raise ValueError(
                        f"{class_name}: нет звонка на урок {lesson_no} ({subject})"
                    )
                _, starts_at, ends_at = bell
                _, created = schedule_repo.upsert_entry(
                    db,
                    class_name=class_name,
                    day_of_week=day_of_week,
                    lesson_no=lesson_no,
                    subject=subject,
                    room=SUBJECT_ROOMS.get(subject),
                    starts_at=starts_at,
                    ends_at=ends_at,
                )
                entries.add(created)

    for class_name, subject, days_ahead, body in HOMEWORK:
        _, created = schedule_repo.upsert_homework(
            db,
            class_name=class_name,
            subject=subject,
            due_date=today + timedelta(days=days_ahead),
            body=body,
        )
        homework.add(created)

    for class_name, body in NOTES:
        _, created = schedule_repo.upsert_note(
            db, class_name=class_name, on_date=today, body=body
        )
        notes.add(created)

    return {"расписание": entries, "домашние задания": homework, "заметки": notes}


def seed_esports(db, *, now) -> dict[str, Tally]:
    """Турниры и награды. Награды ссылаются на учеников, поэтому идут после них."""
    tournaments, badges = Tally(), Tally()

    by_title: dict[str, int] = {}
    for game, title, format, days_ahead, status, description in TOURNAMENTS:
        tournament, created = tournaments_repo.upsert_tournament(
            db,
            game=game,
            title=title,
            format=format,
            starts_at=now + timedelta(days=days_ahead),
            status=status,
            description=description,
        )
        by_title[title] = tournament.id
        tournaments.add(created)

    for alias, title, code in BADGES:
        # Награда без ученика -- опечатка в данных, а не повод завести ученика
        # на ходу: сидер учеников идёт первым и уже отработал.
        student = _student(db, alias, f"бейдж {code!r}")
        tournament_id = by_title.get(title)
        if tournament_id is None:
            raise ValueError(f"бейдж {code!r}: турнир {title!r} не найден")
        _, created = tournaments_repo.upsert_badge(
            db, student_id=student.id, tournament_id=tournament_id, code=code
        )
        badges.add(created)

    return {"турниры": tournaments, "награды": badges}


def seed_students(db) -> dict[str, Tally]:
    """Профили учеников (псевдоним, класс, картинка)."""
    students = Tally()
    for alias, class_name, avatar_key in STUDENTS:
        _, created = students_repo.upsert_student(
            db, alias=alias, class_name=class_name, avatar_key=avatar_key
        )
        students.add(created)
    return {"ученики": students}


def seed_social(db) -> dict[str, Tally]:
    """Посты, комментарии и лайки -- чтобы лента не была пустой."""
    posts, comments, likes = Tally(), Tally(), Tally()

    # Номер поста в POSTS -> id в базе: комментарии и лайки адресуются номером.
    post_ids: list[int] = []
    for alias, is_anonymous, body in POSTS:
        existing = posts_repo.get_post_by_body(db, body)
        if existing is not None:
            post_ids.append(existing.id)
            posts.add(False)
            continue
        author = _student(db, alias, "пост")
        post = posts_repo.create_post(
            db,
            author_id=author.id,
            # Подпись берётся из того же правила, что и на сайте: у анонимного
            # поста это общий «Аноним», а не псевдоним автора.
            display_alias=publication.display_alias_for(author, is_anonymous),
            is_anonymous=is_anonymous,
            body=body,
        )
        post_ids.append(post.id)
        posts.add(True)

    for index, alias, is_anonymous, body in COMMENTS:
        if comments_repo.find_by_post_and_body(db, post_ids[index], body) is not None:
            comments.add(False)
            continue
        author = _student(db, alias, "комментарий")
        comments_repo.create_comment(
            db,
            post_id=post_ids[index],
            author_id=author.id,
            display_alias=publication.display_alias_for(author, is_anonymous),
            is_anonymous=is_anonymous,
            body=body,
        )
        comments.add(True)

    for index, alias in LIKES:
        student = _student(db, alias, "лайк")
        if likes_repo.get_like(db, student.id, post_ids[index]) is not None:
            likes.add(False)
            continue
        likes_repo.add_like(db, student.id, post_ids[index])
        likes.add(True)

    return {"посты": posts, "комментарии": comments, "лайки": likes}


def grant_login(db, *, password: str) -> bool:
    """Дать демонстрационному ученику вход по почте и паролю.

    Пригласительный код для демонстрации неудобен: его пришлось бы держать
    рядом в открытом виде. Ученик из ``LOGIN_ALIAS`` получает адрес и пароль, и
    входит через обычную форму «почта и пароль» -- она работает для всех, у кого
    пароль есть, независимо от лимита регистрации.
    """
    if students_repo.get_student_by_alias(db, LOGIN_ALIAS) is None:
        return False
    students_repo.upsert_student(
        db, alias=LOGIN_ALIAS, email=LOGIN_EMAIL, password_hash=hash_password(password)
    )
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fill the site with demo data (schedule, esports, students, feed)."
    )
    parser.add_argument(
        "--with-login",
        action="store_true",
        help=f"Give {LOGIN_ALIAS!r} an email+password login ({LOGIN_EMAIL}).",
    )
    parser.add_argument(
        "--password",
        default=None,
        help="Password for --with-login (default: a random one, printed once).",
    )
    args = parser.parse_args(argv)

    now = datetime.now(timezone.utc)
    password = args.password or secrets.token_urlsafe(9)

    with SessionLocal() as db:
        counts: dict[str, Tally] = {}
        counts.update(seed_students(db))
        counts.update(seed_school(db, today=now.date()))
        counts.update(seed_esports(db, now=now))
        counts.update(seed_social(db))
        login_ok = grant_login(db, password=password) if args.with_login else False
        db.commit()

    for name, tally in counts.items():
        print(f"{name}: создано {tally.created}, обновлено {tally.updated}")

    if args.with_login:
        if login_ok:
            print(f"Вход: {LOGIN_EMAIL} / {password}")
        else:
            print(
                f"Ученик {LOGIN_ALIAS!r} не найден — вход не выдан.", file=sys.stderr
            )
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
