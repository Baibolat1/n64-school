"""Школьные экраны: расписание, киберспорт и ассистент.

Три страницы одного раздела, поэтому один роутер. Все три требуют действующего
ученика (ассистент ещё и не отдаёт свои ответы никому, кроме спросившего:
ответ живёт в одном рендере и никуда не сохраняется).

Запись здесь одна -- вопрос ассистенту. Он тратит бюджет частоты (см.
``RATE_LIMIT_ASSISTANT``): ответ из базы бесплатен, но и он считается, чтобы
счётчик оставался одним и понятным. Кроме личного окна есть суточный потолок на
весь сайт (``ASSISTANT_DAILY_LIMIT``): вопросы, которые доходят до модели,
стоят денег, и по одному ученику этого не видно.
"""

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request, Response, status
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.core.deps import require_csrf, require_not_banned
from app.core.templates import templates
from app.db.base import get_db
from app.models.student import Student
from app.repositories import schedule as schedule_repo
from app.repositories import students as students_repo
from app.repositories import tournaments as esports_repo
from app.services import assistant as assistant_service
from app.services import esports as esports_service
from app.services import school as school_service
from app.services.rate_limit import limiter

router = APIRouter(tags=["school"], dependencies=[Depends(require_csrf)])

# Сутки для суточного потолка ассистента. Окно скользящее (как и у остальных
# лимитов), поэтому «сутки» -- это последние 24 часа, а не календарный день:
# считать по календарю значило бы держать в счётчике ещё и часовой пояс.
_DAY_SECONDS = 24 * 60 * 60


def _today() -> datetime:
    return datetime.now(timezone.utc)


@router.get("/schedule", response_class=HTMLResponse)
def schedule_page(
    request: Request,
    student: Student = Depends(require_not_banned),
    db: Session = Depends(get_db),
) -> Response:
    """Неделя класса ученика плюс ближайшее домашнее задание."""
    now = _today()
    today = now.date()
    entries = (
        schedule_repo.list_for_class(db, student.class_name)
        if student.class_name
        else []
    )
    homework = (
        schedule_repo.homework_for_class(db, student.class_name, since=today)
        if student.class_name
        else []
    )
    notes = (
        schedule_repo.notes_for_class(db, student.class_name, on_date=today)
        if student.class_name
        else []
    )
    today_weekday = school_service.today_weekday(now)
    today_lessons = (
        [entry for entry in entries if entry.day_of_week == today_weekday]
        if today_weekday
        else []
    )
    return templates.TemplateResponse(
        request,
        "schedule.html",
        {
            "student": student,
            "week": school_service.group_by_day(entries),
            "homework": homework,
            "notes": notes,
            "today_weekday": today_weekday,
            "today_lessons": today_lessons,
            "today_date": today,
            "day_labels": school_service.DAY_LABELS,
            "day_short": school_service.DAY_SHORT,
        },
    )


@router.get("/esports", response_class=HTMLResponse)
def esports_page(
    request: Request,
    student: Student = Depends(require_not_banned),
    db: Session = Depends(get_db),
) -> Response:
    """Турниры школы и бейджи, которые на них получили ученики."""
    tournaments = esports_repo.list_tournaments(db)
    badges = esports_repo.badges_by_tournament(db, [t.id for t in tournaments])
    student_ids = [badge.student_id for group in badges.values() for badge in group]
    avatar_keys = students_repo.avatar_keys_by_id(db, student_ids)
    aliases = students_repo.aliases_by_id(db, student_ids)
    return templates.TemplateResponse(
        request,
        "esports.html",
        {
            "student": student,
            "tournaments": tournaments,
            "badges": badges,
            "avatar_keys": avatar_keys,
            "aliases": aliases,
            "game_label": esports_service.game_label,
            "badge_label": esports_service.badge_label,
            "status_label": esports_service.status_label,
        },
    )


@router.get("/assistant", response_class=HTMLResponse)
def assistant_page(
    request: Request,
    student: Student = Depends(require_not_banned),
    db: Session = Depends(get_db),
) -> Response:
    return templates.TemplateResponse(
        request,
        "assistant.html",
        {
            "student": student,
            "llm_ready": assistant_service.llm_configured(),
            "max_question_length": assistant_service.MAX_QUESTION_LENGTH,
        },
    )


@router.post("/assistant", response_class=HTMLResponse)
def ask_assistant(
    request: Request,
    question: Annotated[str, Form()] = "",
    student: Student = Depends(require_not_banned),
    db: Session = Depends(get_db),
) -> Response:
    """Ответить на вопрос. Отказ -- та же страница с текстом, не 4xx.

    Ученику нечего исправлять в запросе: он либо получает ответ, либо
    понятную фразу о том, почему ответа нет. Исключение -- частота: на ней
    честнее отдать 429, потому что это единственный отказ, который клиент
    может осознанно переждать.
    """
    text = question.strip()
    page = {
        "student": student,
        "llm_ready": assistant_service.llm_configured(),
        "max_question_length": assistant_service.MAX_QUESTION_LENGTH,
        "question": text,
    }

    if not text:
        page["error"] = "Введите вопрос."
        return templates.TemplateResponse(
            request, "assistant.html", page, status_code=status.HTTP_200_OK
        )
    if len(text) > assistant_service.MAX_QUESTION_LENGTH:
        page["error"] = (
            f"Слишком длинный вопрос: максимум "
            f"{assistant_service.MAX_QUESTION_LENGTH} символов."
        )
        return templates.TemplateResponse(
            request, "assistant.html", page, status_code=status.HTTP_200_OK
        )

    # Суточный потолок сайта проверяется первым: вопрос, которому отказал общий
    # бюджет, не должен ещё и тратить личный.
    day_limit = limiter.check(
        "assistant:day",
        limit=settings.ASSISTANT_DAILY_LIMIT,
        window_seconds=_DAY_SECONDS,
    )
    if not day_limit.allowed:
        # Не «ошибка сервера», а понятная фраза: ученику нечего исправлять, ему
        # надо подождать -- и он об этом узнаёт.
        page["error"] = (
            "На сегодня вопросы к помощнику закончились: общий бюджет школы "
            "на сутки исчерпан. Загляните завтра — расписание, уроки и "
            "киберспорт работают как обычно."
        )
        response = templates.TemplateResponse(
            request,
            "assistant.html",
            page,
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        )
        response.headers["Retry-After"] = str(day_limit.retry_after)
        return response

    limit = limiter.check(
        f"assistant:{student.id}",
        limit=settings.RATE_LIMIT_ASSISTANT,
        window_seconds=settings.RATE_LIMIT_ASSISTANT_WINDOW_SECONDS,
    )
    if not limit.allowed:
        page["error"] = (
            f"Слишком много вопросов. Попробуйте через {limit.retry_after} с."
        )
        response = templates.TemplateResponse(
            request,
            "assistant.html",
            page,
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        )
        response.headers["Retry-After"] = str(limit.retry_after)
        return response

    answer = assistant_service.ask(db, student, text, today=_today().date())
    page["answer"] = answer.text
    page["answer_source"] = answer.source
    return templates.TemplateResponse(
        request, "assistant.html", page, status_code=status.HTTP_200_OK
    )
