"""Ассистент: сначала база, потом -- и только при ключе -- модель.

Гибридный режим устроен так, что **без ключа ассистент всё равно работает**.
Сначала вопрос разбирается по базе школы (расписание, домашка, учителя,
турниры) -- это бесплатно, мгновенно и не отправляет никуда ни строчки
ученика. Только если база ответить не может *и* в настройках лежит ключ,
тот же вопрос уходит в модель (Claude или DeepSeek -- что настроено).

Что важно и чего здесь сознательно нет:

* **Текст ученика никуда не уходит без ключа.** Ключ -- это явное решение
  владельца сайта. Пока его нет, наружу не уходит ничего.
* **Персональные данные не уходят и с ключом.** В модель едет вопрос и
  обезличенный контекст (расписание и домашка класса). Псевдоним, класс и
  почта ученика в запрос не попадают: модель -- не друг и не учитель, ей
  незачем знать, кто спрашивает.
* **Ответ модели не сохраняется** и не показывается другим: это разговор
  один на один, а не пост.
* Ключ читается из настроек и никогда не логируется; в лог идёт только факт
  «спросили базу / спросили модель» и код провайдера.
"""

from dataclasses import dataclass
from datetime import date, timedelta

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.core.logging import logger
from app.models.school import SCHOOL_DAYS
from app.models.student import Student
from app.repositories import schedule as schedule_repo
from app.repositories import teachers as teachers_repo
from app.repositories import tournaments as tournaments_repo
from app.services import esports as esports_service
from app.services import school as school_service

# Ответ, который ученик видит на экране. ``source`` -- откуда он взялся:
# база, модель или подсказка. Экран показывает это честно, чтобы никто не
# принимал ответ модели за выписку из школьной базы.
SOURCE_DB = "db"
SOURCE_LLM = "llm"
SOURCE_HELP = "help"

# Сколько символов вопроса принимаем. Длинный вопрос -- это не вопрос, а
# попытка залить контекст; отказываем по длине до всякой работы.
MAX_QUESTION_LENGTH = 400

DAY_WORDS = {
    "понедельник": 1,
    "вторник": 2,
    "среду": 3,
    "среда": 3,
    "четверг": 4,
    "пятницу": 5,
    "пятница": 5,
    "субботу": 6,
    "суббота": 6,
}


@dataclass
class Answer:
    text: str
    source: str


def _normalize(question: str) -> str:
    return " ".join(question.lower().split())


def _mentions(normalized: str, *words: str) -> bool:
    return any(word in normalized for word in words)


# Сколько первых букв предмета считать его основой при поиске в вопросе.
# Четыре, потому что в вопросе предмет стоит в падеже («что задали по физике»),
# и целое слово «физика» в такой строке не найдётся. Для школьных предметов
# четырёх букв хватает, чтобы они не путались между собой; «ОБЖ» короче, и
# срез просто даёт его целиком.
_SUBJECT_STEM = 4


def _mentions_subject(value: str | None, normalized: str) -> bool:
    """Есть ли предмет из базы в вопросе -- с учётом падежа.

    Сравнение по основе, а не по слову целиком: «по физике», «про алгебру»,
    «учитель истории». Это не морфология, а дешёвое приближение, которое
    ошибается только в сторону лишнего совпадения -- и тогда ответ просто
    окажется шире вопроса.
    """
    if not value:
        return False
    return value.lower()[:_SUBJECT_STEM] in normalized


def _resolve_day(normalized: str, today: date) -> int | None:
    """Какой день расписания спрашивают: сегодня, завтра или названный."""
    if _mentions(normalized, "сегодня", "сейчас"):
        return school_service.today_weekday()
    if _mentions(normalized, "завтра"):
        weekday = (today + timedelta(days=1)).isoweekday()
        return weekday if weekday in SCHOOL_DAYS else None
    for word, day in DAY_WORDS.items():
        if word in normalized:
            return day
    return None


def answer_from_db(
    db: Session, student: Student, normalized: str, *, today: date
) -> Answer | None:
    """Ответ по базе школы или None, если база на такой вопрос не отвечает."""
    class_name = student.class_name

    if _mentions(normalized, "расписан", "урок", "уроки", "пара", "пары"):
        if not class_name:
            return Answer(
                "Ваш класс не указан в профиле, поэтому расписание показать не "
                "могу. Попросите администратора указать класс.",
                SOURCE_DB,
            )
        day = _resolve_day(normalized, today)
        if day is None:
            day = school_service.today_weekday() or 1
        lessons = schedule_repo.list_for_day(db, class_name, day)
        # Винительный падеж: «на вторник», а не «на вторник» из именительного.
        label = school_service.DAY_ACCUSATIVE.get(
            day, school_service.DAY_LABELS.get(day, "")
        )
        if not lessons:
            return Answer(f"На {label} уроков нет.", SOURCE_DB)
        lines = [
            f"{lesson.lesson_no}. {lesson.subject} "
            f"({school_service.lesson_time(lesson)}"
            # Кабинет как записан: «каб. 24» уже несёт своё слово, «спортзал» --
            # нет, и приписывать ему «каб.» было бы неверно.
            + (f", {lesson.room}" if lesson.room else "")
            + ")"
            for lesson in lessons
        ]
        return Answer(f"Расписание на {label}:\n" + "\n".join(lines), SOURCE_DB)

    if _mentions(normalized, "домашк", "домашн", "дз", "задали", "задание"):
        if not class_name:
            return Answer(
                "Ваш класс не указан в профиле, поэтому домашнее задание "
                "показать не могу.",
                SOURCE_DB,
            )
        items = schedule_repo.homework_for_class(db, class_name, since=today)
        # Если в вопросе назван предмет -- сузим до него.
        subject_hits = [
            item for item in items if _mentions_subject(item.subject, normalized)
        ]
        if subject_hits:
            items = subject_hits
        if not items:
            return Answer("На ближайшие дни домашнего задания нет.", SOURCE_DB)
        lines = [
            f"{item.subject} к {item.due_date.strftime('%d.%m')}: {item.body}"
            for item in items[:5]
        ]
        return Answer("Домашнее задание:\n" + "\n".join(lines), SOURCE_DB)

    if _mentions(normalized, "учител", "преподав", "препод"):
        teachers = teachers_repo.list_active_teachers(db)
        hits = [t for t in teachers if t.display_name.lower() in normalized]
        if not hits:
            # По предмету -- той же основой, что и домашнее задание: «учитель
            # физики» должен находиться так же, как «что задали по физике».
            hits = [
                t for t in teachers if _mentions_subject(t.subject, normalized)
            ]
        if hits:
            lines = [
                f"{t.display_name}"
                + (f" -- {t.subject}" if t.subject else "")
                for t in hits
            ]
            return Answer("Нашёл:\n" + "\n".join(lines), SOURCE_DB)
        if teachers:
            return Answer(
                f"В школе {len(teachers)} учител(ей) в списке. Полный список -- "
                "на странице «Учителя».",
                SOURCE_DB,
            )

    if _mentions(normalized, "турнир", "cs2", "cs 2", "brawl", "киберспорт"):
        tournaments = tournaments_repo.list_tournaments(db)
        if not tournaments:
            return Answer("Турниров пока нет.", SOURCE_DB)
        lines = [
            f"{t.title} ({esports_service.game_label(t.game)}, "
            f"{esports_service.status_label(t.status)})"
            for t in tournaments[:5]
        ]
        return Answer("Турниры школы:\n" + "\n".join(lines), SOURCE_DB)

    if _mentions(normalized, "что ты умеешь", "помощь", "help", "как польз"):
        return Answer(_HELP_TEXT, SOURCE_HELP)

    return None


_HELP_TEXT = (
    "Я умею отвечать по школьной базе:\n"
    "• «расписание на завтра» -- уроки вашего класса;\n"
    "• «что задали по алгебре» -- домашнее задание;\n"
    "• «турниры по CS2» -- киберспорт школы;\n"
    "• «учитель физики» -- кто ведёт предмет.\n"
    "Если настроен ключ модели, я отвечу и на свободный вопрос."
)


def build_context(db: Session, student: Student, *, today: date) -> str:
    """Обезличенный контекст для модели: расписание и домашка класса.

    Ни псевдонима, ни класса, ни id: модель получает только то, что и так
    висит на доске в коридоре.
    """
    if not student.class_name:
        return "У ученика класс не указан."
    lessons = schedule_repo.list_for_day(
        db, student.class_name, school_service.today_weekday() or 1
    )
    homework = schedule_repo.homework_for_class(db, student.class_name, since=today)
    lesson_line = ", ".join(
        f"{lesson.lesson_no}) {lesson.subject}" for lesson in lessons
    ) or "нет"
    homework_line = "; ".join(
        f"{item.subject} к {item.due_date.isoformat()}: {item.body}"
        for item in homework[:5]
    ) or "нет"
    return f"Уроки сегодня: {lesson_line}\nДомашнее задание: {homework_line}"


_SYSTEM_PROMPT = (
    "Ты -- ассистент школьного хаба n64hub. Отвечай кратко, по-русски и по "
    "делу. Ты помогаешь ученикам школы: расписание, домашние задания, "
    "школьные события. Если ответа не знаешь, скажи об этом честно и "
    "предложи спросить у учителя или администратора. Никогда не проси и не "
    "повторяй личные данные: имена, телефоны, адреса, ссылки. Не выдумывай "
    "факты о школе, которых нет в контексте."
)


class LlmUnavailable(Exception):
    """Модель не настроена или не ответила."""


def llm_configured() -> bool:
    return bool(settings.ANTHROPIC_API_KEY or settings.DEEPSEEK_API_KEY)


def ask_llm(question: str, context: str) -> str:
    """Спросить модель. Claude -- если есть его ключ, иначе DeepSeek.

    Оба вызова идут сырым HTTP через httpx: SDK Anthropic покрывает только
    Anthropic, а нам нужен один и тот же код на двух провайдеров, один из
    которых OpenAI-совместим.
    """
    if settings.ANTHROPIC_API_KEY:
        return _ask_anthropic(question, context)
    if settings.DEEPSEEK_API_KEY:
        return _ask_deepseek(question, context)
    raise LlmUnavailable()


def _ask_anthropic(question: str, context: str) -> str:
    payload = {
        "model": settings.ANTHROPIC_MODEL,
        "max_tokens": settings.ASSISTANT_MAX_TOKENS,
        "system": _SYSTEM_PROMPT,
        "messages": [
            {
                "role": "user",
                "content": f"Контекст школы:\n{context}\n\nВопрос: {question}",
            }
        ],
    }
    response = httpx.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": settings.ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json=payload,
        timeout=settings.ASSISTANT_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    blocks = response.json().get("content", [])
    text = "".join(
        block.get("text", "") for block in blocks if block.get("type") == "text"
    )
    if not text.strip():
        raise LlmUnavailable()
    return text.strip()


def _ask_deepseek(question: str, context: str) -> str:
    payload = {
        "model": settings.DEEPSEEK_MODEL,
        "max_tokens": settings.ASSISTANT_MAX_TOKENS,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"Контекст школы:\n{context}\n\nВопрос: {question}",
            },
        ],
    }
    response = httpx.post(
        "https://api.deepseek.com/chat/completions",
        headers={
            "Authorization": f"Bearer {settings.DEEPSEEK_API_KEY}",
            "content-type": "application/json",
        },
        json=payload,
        timeout=settings.ASSISTANT_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    choices = response.json().get("choices", [])
    if not choices:
        raise LlmUnavailable()
    text = (choices[0].get("message") or {}).get("content", "")
    if not text.strip():
        raise LlmUnavailable()
    return text.strip()


def ask(db: Session, student: Student, question: str, *, today: date) -> Answer:
    """Полный конвейер: база -> модель -> подсказка.

    Ни один отказ не показывается ученику как ошибка: ассистент либо
    отвечает, либо честно говорит, чего не знает.
    """
    normalized = _normalize(question)
    local = answer_from_db(db, student, normalized, today=today)
    if local is not None:
        return local

    if not llm_configured():
        return Answer(
            "По базе школы я на это ответить не могу, а модель не подключена. "
            "Попробуйте спросить про расписание, домашнее задание, учителей "
            "или турниры.",
            SOURCE_HELP,
        )

    try:
        text = ask_llm(question, build_context(db, student, today=today))
    except (httpx.HTTPError, LlmUnavailable, KeyError, ValueError) as exc:
        # Текст вопроса и тело ответа в лог не идут: там может быть что
        # угодно, включая чужие персональные данные.
        logger.warning("assistant_llm_failed kind=%s", type(exc).__name__)
        return Answer(
            "Модель сейчас не отвечает. Попробуйте ещё раз или спросите про "
            "расписание и домашнее задание -- на это я отвечаю из базы школы.",
            SOURCE_HELP,
        )
    logger.info("assistant_llm_answered")
    return Answer(text, SOURCE_LLM)
