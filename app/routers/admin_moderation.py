"""Экраны модерации: очереди открытых жалоб и флагов ПДн.

Вход, выход, дашборд и приглашения остаются в ``app.routers.admin``; сюда
переезжают сами очереди модерации, чтобы первый модуль не разросся. Префикс
``/admin`` общий с ним, но пути не пересекаются: у ``admin`` -- ``/admin``,
``/admin/login``, ``/admin/logout``, ``/admin/invites``, здесь -- ``/admin/reports``,
``/admin/content/{type}/{id}`` и далее.

Строки очереди собираются в роутере, а не в шаблоне: шаблоны сервисы не
вызывают. Резолвер модератора уже без фильтра видимости, поэтому скрытый или
удалённый объект здесь виден -- очередь не должна выглядеть так, будто с ним
ничего не делали.
"""

from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.core.deps import require_admin, require_csrf
from app.core.pagination import page_window, parse_page
from app.core.templates import templates
from app.db.base import get_db
from app.models.admin import Admin
from app.models.audit import (
    ACTION_BAN,
    ACTION_DELETE,
    ACTION_HIDE,
    ACTION_RESTORE,
    PII_FLAG_STATUS_DISMISSED,
    PII_FLAG_STATUS_RESOLVED,
    TARGET_STUDENT,
    PiiBlockedAttempt,
    PiiFlag,
)
from app.models.report import Report
from app.repositories import audit as audit_repo
from app.repositories import pii_blocked as pii_blocked_repo
from app.repositories import pii_flags as pii_flags_repo
from app.repositories import reports as reports_repo
from app.repositories import students as students_repo
from app.repositories.audit import AuditEntry
from app.services import moderation as moderation_service
from app.services.moderation import (
    ACTION_LABELS,
    MAX_NOTE_LENGTH,
    RULE_LABELS,
    ModerationError,
    ModerationTarget,
)
from app.services.reports import REASON_LABELS

router = APIRouter(
    prefix="/admin", tags=["admin-moderation"], dependencies=[Depends(require_csrf)]
)

# Жалоб на странице очереди; фиксированный размер держит ссылки пагинации простыми.
PAGE_SIZE = 20


@dataclass(frozen=True)
class ReportRow:
    """Одна строка очереди открытых жалоб.

    ``target`` может быть None, если объект не зарезолвился: строка всё равно
    рисуется, только на месте текста объекта будет подпись. ``status_label`` тоже
    None в этом случае -- статуса показывать не у чего.
    """

    report: Report
    reason_label: str
    reporter_alias: str
    target: ModerationTarget | None
    target_label: str
    status_label: str | None
    target_report_count: int


def _build_row(
    db: Session, report: Report, counts: dict[tuple[str, int], int]
) -> ReportRow:
    """Собрать одну строку очереди: жалобу, её причину, объект и счётчик жалоб.

    Объект резолвится на строку, а не выборкой на всю страницу: виды контента
    лежат в разных таблицах, и страница держит их всего ``PAGE_SIZE``.
    """
    target = moderation_service.resolve_moderation_target(
        db, report.target_type, report.target_id
    )
    reporter = students_repo.get_student(db, report.reporter_id)
    return ReportRow(
        report=report,
        reason_label=REASON_LABELS.get(report.reason_code, report.reason_code),
        reporter_alias=reporter.alias if reporter is not None else "—",
        target=target,
        target_label=moderation_service.TARGET_LABELS.get(
            report.target_type, report.target_type
        ),
        status_label=(
            moderation_service.STATUS_LABELS.get(target.status, target.status)
            if target is not None
            else None
        ),
        target_report_count=counts.get((report.target_type, report.target_id), 0),
    )


@router.get("/reports", response_class=HTMLResponse)
def reports_queue(
    request: Request,
    page: str | None = None,
    admin: Admin = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Response:
    """Очередь открытых жалоб: кто пожаловался, на что и сколько таких жалоб."""
    total = reports_repo.count_open(db)
    page, total_pages = page_window(parse_page(page), total, PAGE_SIZE)
    reports = reports_repo.list_open(db, limit=PAGE_SIZE, offset=(page - 1) * PAGE_SIZE)
    # Счётчики -- одним запросом на страницу, а не по запросу на строку.
    counts = reports_repo.open_report_counts(db)
    rows = [_build_row(db, report, counts) for report in reports]
    return templates.TemplateResponse(
        request,
        "admin/reports.html",
        {"rows": rows, "page": page, "total_pages": total_pages},
    )


@dataclass(frozen=True)
class FlagRow:
    """Одна строка очереди открытых флагов ПДн.

    ``target`` может быть None, если объект не зарезолвился: строка всё равно
    рисуется, только со подписью «Объект не найден» и без ссылки на хаб -- сам
    флаг закрывается по своему id, поэтому работать с ним можно и без цели.
    ``status_label`` тоже None в этом случае -- статуса показывать не у чего.
    """

    flag: PiiFlag
    rule_label: str
    target: ModerationTarget | None
    target_label: str
    status_label: str | None
    target_flag_count: int


def _build_flag_row(
    db: Session, flag: PiiFlag, counts: dict[tuple[str, int], int]
) -> FlagRow:
    """Собрать одну строку очереди флагов: правило, объект и счётчик флагов.

    Объект резолвится на строку -- так же, как в очереди жалоб (см.
    :func:`_build_row`).
    """
    target = moderation_service.resolve_moderation_target(
        db, flag.target_type, flag.target_id
    )
    return FlagRow(
        flag=flag,
        rule_label=RULE_LABELS.get(flag.rule_code, flag.rule_code),
        target=target,
        target_label=moderation_service.TARGET_LABELS.get(
            flag.target_type, flag.target_type
        ),
        status_label=(
            moderation_service.STATUS_LABELS.get(target.status, target.status)
            if target is not None
            else None
        ),
        target_flag_count=counts.get((flag.target_type, flag.target_id), 0),
    )


@router.get("/pii", response_class=HTMLResponse)
def pii_queue(
    request: Request,
    page: str | None = None,
    admin: Admin = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Response:
    """Очередь открытых мягких срабатываний фильтра ПДн.

    Мягкий фильтр не отказывает в публикации: он оставляет флаг с фрагментом
    текста, и модератор решает, подтвердить правило или снять замечание.
    Действия прямо в очереди возвращают сюда же -- на ``/admin/pii`` -- чтобы
    модератор не терял очередь при разборе нескольких флагов подряд.
    """
    total = pii_flags_repo.count_open(db)
    page, total_pages = page_window(parse_page(page), total, PAGE_SIZE)
    flags = pii_flags_repo.list_open(db, limit=PAGE_SIZE, offset=(page - 1) * PAGE_SIZE)
    # Счётчики -- одним запросом на страницу, а не по запросу на строку.
    counts = pii_flags_repo.open_flag_counts(db)
    rows = [_build_flag_row(db, flag, counts) for flag in flags]
    return templates.TemplateResponse(
        request,
        "admin/pii.html",
        {"rows": rows, "page": page, "total_pages": total_pages},
    )


@dataclass(frozen=True)
class BlockedRow:
    """Одна строка журнала жёстких отказов публикации.

    Текста публикации в строке нет и быть не может: фиксируется только факт
    отказа -- кто, когда, что публиковал и какие правила сработали.
    """

    attempt: PiiBlockedAttempt
    student_alias: str
    target_label: str
    rule_labels: list[str]


def _rule_labels(rule_codes: str) -> list[str]:
    """Разобрать сохранённые коды правил в русские подписи.

    ``rule_codes`` -- это коды через запятую (так их пишет репозиторий).
    Неизвестный код показываем как есть, чтобы сработавшее правило не пропало
    из журнала из-за отставшей карты подписей.
    """
    return [
        RULE_LABELS.get(code, code)
        for code in (part.strip() for part in rule_codes.split(","))
        if code
    ]


def _build_blocked_row(entry: pii_blocked_repo.BlockedEntry) -> BlockedRow:
    """Собрать одну строку журнала отказов: ученик, объект и правила отказа."""
    return BlockedRow(
        attempt=entry.attempt,
        student_alias=entry.student_alias,
        target_label=moderation_service.TARGET_LABELS.get(
            entry.attempt.target_type, entry.attempt.target_type
        ),
        rule_labels=_rule_labels(entry.attempt.rule_codes),
    )


@router.get("/blocked", response_class=HTMLResponse)
def blocked_journal(
    request: Request,
    page: str | None = None,
    admin: Admin = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Response:
    """Журнал жёстких отказов публикации: кто, когда, что и почему.

    Объекта за строкой нет: публикация не состоялась, więc ссылки на хаб у этих
    строк быть не может. Текста отказа тоже нет -- фильтр его не сохраняет,
    и это его свойство, а не забывчивость экрана.
    """
    total = pii_blocked_repo.count_all(db)
    page, total_pages = page_window(parse_page(page), total, PAGE_SIZE)
    entries = pii_blocked_repo.list_recent(
        db, limit=PAGE_SIZE, offset=(page - 1) * PAGE_SIZE
    )
    rows = [_build_blocked_row(entry) for entry in entries]
    return templates.TemplateResponse(
        request,
        "admin/blocked.html",
        {"rows": rows, "page": page, "total_pages": total_pages},
    )


# След по одному объекту короткий, поэтому карточка берёт свежие записи одним
# запросом без пагинации -- в отличие от очередей, делить тут нечего.
TRAIL_LIMIT = 50


@dataclass(frozen=True)
class HubReportRow:
    """Одна открытая жалоба на объект в его карточке.

    Подписи причины и жалобщика считаются так же, как в ``_build_row``, но без
    полей самого объекта и счётчика: страница карточки и есть один объект, а
    жалоб на нём единицы.
    """

    report: Report
    reason_label: str
    reporter_alias: str


def _build_hub_report(db: Session, report: Report) -> HubReportRow:
    """Собрать одну строку списка жалоб на объект."""
    reporter = students_repo.get_student(db, report.reporter_id)
    return HubReportRow(
        report=report,
        reason_label=REASON_LABELS.get(report.reason_code, report.reason_code),
        reporter_alias=reporter.alias if reporter is not None else "—",
    )


@dataclass(frozen=True)
class HubFlagRow:
    """Один открытый флаг ПДн по объекту в его карточке."""

    flag: PiiFlag
    rule_label: str


@dataclass(frozen=True)
class HubTrailRow:
    """Одна запись следа по объекту: подпись действия, админ и время."""

    entry: AuditEntry
    action_label: str


def _hub_context(
    db: Session, target_type: str, target_id: int
) -> dict | None:
    """Собрать контекст карточки объекта или None, если объекта нет.

    Неизвестный тип и несуществующий/чужой id возвращают None: вызывающий сам
    решает, ответить 404 (GET) или переспросить решение сервиса (POST).
    """
    if target_type not in moderation_service.TARGET_LABELS:
        return None
    target = moderation_service.resolve_moderation_target(db, target_type, target_id)
    if target is None:
        return None

    return {
        "target": target,
        "target_label": moderation_service.TARGET_LABELS[target_type],
        "status_label": moderation_service.STATUS_LABELS.get(
            target.status, target.status
        ),
        "author": students_repo.get_student(db, target.author_id),
        "report_rows": [
            _build_hub_report(db, report)
            for report in reports_repo.list_open_for_target(
                db, target_type, target_id
            )
        ],
        "flag_rows": [
            HubFlagRow(
                flag=flag,
                rule_label=RULE_LABELS.get(flag.rule_code, flag.rule_code),
            )
            for flag in pii_flags_repo.list_open_for_target(
                db, target_type, target_id
            )
        ],
        "trail_rows": [
            HubTrailRow(
                entry=entry,
                action_label=ACTION_LABELS.get(
                    entry.action.action, entry.action.action
                ),
            )
            for entry in audit_repo.list_for_target(
                db, target_type, target_id, TRAIL_LIMIT, 0
            )
        ],
        "max_note_length": MAX_NOTE_LENGTH,
    }


def _render_hub(
    request: Request,
    db: Session,
    target_type: str,
    target_id: int,
    *,
    error: str | None = None,
    status_code: int = status.HTTP_200_OK,
) -> Response:
    """Отрисовать карточку объекта, при необходимости -- с сообщением об ошибке.

    Одна и та же страница для GET и для неуспешного POST: модератор видит
    решение, которое не прошло, и может выбрать другое на месте. Неизвестный тип
    или несуществующий объект -- 404.
    """
    context = _hub_context(db, target_type, target_id)
    if context is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    context["error"] = error
    return templates.TemplateResponse(
        request, "admin/content.html", context, status_code=status_code
    )


@router.get("/content/{target_type}/{target_id}", response_class=HTMLResponse)
def content_hub(
    request: Request,
    target_type: str,
    target_id: int,
    admin: Admin = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Response:
    """Карточка объекта: что это, чьё, в каком статусе и что с ним делали.

    Резолвер модератора видит и скрытое, и удалённое, поэтому на карточку
    попадает в том числе то, что ученику уже недоступно. Неизвестный тип или
    несуществующий объект -- 404: пустая карточка тут ничего не объясняет.
    """
    return _render_hub(request, db, target_type, target_id)


# --- Решения модератора: POST-действия над объектом, жалобой или флагом ------

# Решения формы по объекту, которые принимает общий сервис скрытия/возврата/
# удаления; ``ban`` обрабатывается отдельно, потому что бьёт по автору.
_CONTENT_DECISIONS = frozenset({ACTION_HIDE, ACTION_RESTORE, ACTION_DELETE})

# Машинные причины отказа сервиса -- в текст для модератора. Живёт рядом с
# роутами: это формулировки экрана, а не часть домена, поэтому в сервис не
# переезжает.
_ERROR_MESSAGES: dict[ModerationError, str] = {
    ModerationError.TARGET_NOT_FOUND: "Объект не найден",
    ModerationError.REPORT_NOT_FOUND: "Жалоба уже закрыта или не найдена",
    ModerationError.FLAG_NOT_FOUND: "Флаг ПДн уже закрыт или не найден",
    ModerationError.STUDENT_NOT_FOUND: "Ученик не найден",
    ModerationError.NOT_RESTORABLE: "Удалённый объект вернуть нельзя",
    ModerationError.UNKNOWN_ACTION: "Неизвестное действие",
    ModerationError.TOO_LONG: "Заметка слишком длинная",
}
_FALLBACK_ERROR = "Не удалось выполнить действие"


def _error_text(reason: ModerationError | None) -> str:
    """Текст отказа по машинной причине; неизвестная причина -- запасной текст."""
    if reason is None:
        return _FALLBACK_ERROR
    return _ERROR_MESSAGES.get(reason, _FALLBACK_ERROR)


def _hub_url(target_type: str, target_id: int) -> str:
    """Адрес карточки объекта: он же значение по умолчанию для ``next``."""
    return f"/admin/content/{target_type}/{target_id}"


def _safe_next(raw: str | None, fallback: str) -> str:
    """Куда вернуть модератора после POST, не доверяя ``next`` из формы.

    ``next`` приходит из браузера, то есть его можно подделать. Разрешаем только
    внутренние пути раздела: значение должно начинаться с ``/admin`` и не
    начинаться с ``//`` (``//host/path`` -- это уже чужой сайт). Всё остальное,
    включая пустое значение, заменяется на ``fallback`` -- это защита от open
    redirect, иначе поле превратилось бы в прыжок на сторонний адрес.
    """
    if raw and raw.startswith("/admin") and not raw.startswith("//"):
        return raw
    return fallback


@router.post("/content/{target_type}/{target_id}/decide", response_class=HTMLResponse)
def decide_content(
    request: Request,
    target_type: str,
    target_id: int,
    decision: Annotated[str, Form()],
    note: Annotated[str | None, Form()] = None,
    next_url: Annotated[str | None, Form(alias="next")] = None,
    admin: Admin = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Response:
    """Применить решение модератора к объекту: скрыть, вернуть, удалить, забанить.

    ``ban`` бьёт по автору, а не по самому объекту, поэтому уходит в отдельную
    ветку сервиса; скрытие/возврат/удаление -- в общую. Неизвестное значение
    формы сервис не трогает: модератор получает ошибку, объект остаётся как был.
    """
    if decision in _CONTENT_DECISIONS:
        result = moderation_service.apply_content_action(
            db,
            admin,
            target_type=target_type,
            target_id=target_id,
            action=decision,
            note=note,
        )
    elif decision == ACTION_BAN:
        result = moderation_service.decide_object(
            db,
            admin,
            target_type=target_type,
            target_id=target_id,
            decision=decision,
            note=note,
        )
    else:
        result = moderation_service.ActionResult(
            ok=False, reason=ModerationError.UNKNOWN_ACTION
        )

    if not result.ok:
        return _render_hub(
            request, db, target_type, target_id, error=_error_text(result.reason)
        )
    return RedirectResponse(
        url=_safe_next(next_url, _hub_url(target_type, target_id)),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/reports/{report_id}/dismiss", response_class=HTMLResponse)
def dismiss_report_action(
    request: Request,
    report_id: int,
    note: Annotated[str | None, Form()] = None,
    next_url: Annotated[str | None, Form(alias="next")] = None,
    admin: Admin = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Response:
    """Отклонить одну открытую жалобу, не трогая объект за ней.

    Несуществующая жалоба -- 404: возвращать модератора некуда. Уже закрытая или
    чужая -- та же карточка объекта с сообщением, чтобы он видел контекст.
    """
    report = db.get(Report, report_id)
    if report is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    result = moderation_service.dismiss_report(
        db, admin, report_id=report_id, note=note
    )
    if not result.ok:
        return _render_hub(
            request,
            db,
            report.target_type,
            report.target_id,
            error=_error_text(result.reason),
        )

    # Возврат по умолчанию -- карточка объекта жалобы; если и её уже нет, ведём
    # в очередь жалоб, чтобы модератор не остался на битом адресе.
    fallback = _hub_url(report.target_type, report.target_id)
    if (
        moderation_service.resolve_moderation_target(
            db, report.target_type, report.target_id
        )
        is None
    ):
        fallback = "/admin/reports"
    return RedirectResponse(
        url=_safe_next(next_url, fallback),
        status_code=status.HTTP_303_SEE_OTHER,
    )


def _close_flag(
    request: Request,
    db: Session,
    admin: Admin,
    flag_id: int,
    decision: str,
    note: str | None,
    next_url: str | None,
) -> Response:
    """Общая часть «Принять»/«Отклонить» для одного флага ПДн.

    Несуществующий флаг -- 404. Уже закрытый -- карточка объекта флага с
    сообщением: закрыть флаг второй раз нельзя, но контекст модератору нужен.
    """
    flag = db.get(PiiFlag, flag_id)
    if flag is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    result = moderation_service.close_flag(
        db, admin, flag_id=flag_id, decision=decision, note=note
    )
    if not result.ok:
        return _render_hub(
            request,
            db,
            flag.target_type,
            flag.target_id,
            error=_error_text(result.reason),
        )
    return RedirectResponse(
        url=_safe_next(next_url, _hub_url(flag.target_type, flag.target_id)),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/pii-flags/{flag_id}/resolve", response_class=HTMLResponse)
def resolve_pii_flag(
    request: Request,
    flag_id: int,
    note: Annotated[str | None, Form()] = None,
    next_url: Annotated[str | None, Form(alias="next")] = None,
    admin: Admin = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Response:
    """Принять флаг ПДн: правило сработало, замечание подтверждено."""
    return _close_flag(
        request, db, admin, flag_id, PII_FLAG_STATUS_RESOLVED, note, next_url
    )


@router.post("/pii-flags/{flag_id}/dismiss", response_class=HTMLResponse)
def dismiss_pii_flag(
    request: Request,
    flag_id: int,
    note: Annotated[str | None, Form()] = None,
    next_url: Annotated[str | None, Form(alias="next")] = None,
    admin: Admin = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Response:
    """Отклонить флаг ПДн: срабатывание ложное, замечание снимается."""
    return _close_flag(
        request, db, admin, flag_id, PII_FLAG_STATUS_DISMISSED, note, next_url
    )


# --- Карточка ученика (``/admin/students/{id}``) -----------------------------


@dataclass(frozen=True)
class StudentTrailRow:
    """Одна запись следа на карточке ученика: действие, админ и ссылка.

    ``hub_url`` заполнен только тогда, когда запись указывает на контент самого
    ученика, чтобы модератор мог открыть тот самый пост или комментарий, из-за
    которого его заблокировали. У записей про самого ученика (бан/разбан)
    ссылки нет.
    """

    entry: AuditEntry
    action_label: str
    hub_url: str | None


def _student_trail_row(entry: AuditEntry) -> StudentTrailRow:
    """Обернуть запись следа русской подписью действия и, если есть, ссылкой."""
    target_type = entry.action.target_type
    hub_url = (
        _hub_url(target_type, entry.action.target_id)
        if target_type in moderation_service.TARGET_LABELS
        else None
    )
    return StudentTrailRow(
        entry=entry,
        action_label=ACTION_LABELS.get(entry.action.action, entry.action.action),
        hub_url=hub_url,
    )


def _student_url(student_id: int) -> str:
    """Адрес карточки ученика: значение по умолчанию для ``next`` её действий."""
    return f"/admin/students/{student_id}"


def _student_context(db: Session, student_id: int) -> dict | None:
    """Собрать контекст карточки ученика или None, если такого ученика нет.

    След вбирает и то, что делали с самим учеником, и то, что делали с его
    контентом (``audit_repo.list_for_student``), поэтому к посту или комментарию
    за блокировкой можно перейти с этого же экрана.
    """
    student = students_repo.get_student(db, student_id)
    if student is None:
        return None
    return {
        "student": student,
        "trail_rows": [
            _student_trail_row(entry)
            for entry in audit_repo.list_for_student(db, student_id, TRAIL_LIMIT, 0)
        ],
        "max_note_length": MAX_NOTE_LENGTH,
    }


def _render_student(
    request: Request,
    db: Session,
    student_id: int,
    *,
    error: str | None = None,
    status_code: int = status.HTTP_200_OK,
) -> Response:
    """Отрисовать карточку ученика, при необходимости -- с сообщением об ошибке.

    Одна и та же страница для GET и для неуспешного POST: модератор видит
    решение, которое не прошло, и выбирает другое на месте. Несуществующий
    ученик -- 404.
    """
    context = _student_context(db, student_id)
    if context is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    context["error"] = error
    return templates.TemplateResponse(
        request, "admin/student.html", context, status_code=status_code
    )


@router.get("/students/{student_id}", response_class=HTMLResponse)
def student_card(
    request: Request,
    student_id: int,
    admin: Admin = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Response:
    """Карточка ученика: псевдоним, состояние блокировки и что с ним делали.

    Реальных имён в системе нет, поэтому единственное, что тут есть об ученике,
    -- псевдоним: карточка остаётся про модерацию -- переключатель бана, даты и
    след. Несуществующий ученик -- 404.
    """
    return _render_student(request, db, student_id)


def _student_decision_response(
    request: Request,
    db: Session,
    student_id: int,
    result: moderation_service.ActionResult,
    next_url: str | None,
) -> Response:
    """Общий ответ на бан/разбан: успех -- редирект, отказ -- та же карточка."""
    if not result.ok:
        return _render_student(
            request, db, student_id, error=_error_text(result.reason)
        )
    return RedirectResponse(
        url=_safe_next(next_url, _student_url(student_id)),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/students/{student_id}/ban", response_class=HTMLResponse)
def ban_student_action(
    request: Request,
    student_id: int,
    note: Annotated[str | None, Form()] = None,
    next_url: Annotated[str | None, Form(alias="next")] = None,
    admin: Admin = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Response:
    """Заблокировать ученика; необязательная заметка становится причиной бана."""
    result = moderation_service.ban_student(
        db, admin, student_id=student_id, note=note
    )
    return _student_decision_response(request, db, student_id, result, next_url)


@router.post("/students/{student_id}/unban", response_class=HTMLResponse)
def unban_student_action(
    request: Request,
    student_id: int,
    note: Annotated[str | None, Form()] = None,
    next_url: Annotated[str | None, Form(alias="next")] = None,
    admin: Admin = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Response:
    """Разблокировать ученика; необязательная заметка остаётся только в следе."""
    result = moderation_service.unban_student(
        db, admin, student_id=student_id, note=note
    )
    return _student_decision_response(request, db, student_id, result, next_url)


# --- Журнал действий (``/admin/audit``) --------------------------------------


@dataclass(frozen=True)
class AuditRow:
    """Одна строка журнала: запись следа, подписи и ссылка на объект.

    ``link_url`` заполнен только тогда, когда за записью стоит публичная
    сущность, которую есть куда открыть: контент ведёт на свой хаб, ученик -- на
    свою карточку. У записи про жалобу или про флаг ПДн ссылки нет: она указывает
    на жалобу или флаг, у которых отдельного экрана не существует.
    """

    entry: AuditEntry
    action_label: str
    target_label: str
    link_url: str | None


def _audit_link_url(target_type: str, target_id: int) -> str | None:
    """Адрес объекта за записью или None, если открывать нечего.

    Контентные цели ведут на хаб, ученик -- на свою карточку; для жалобы и флага
    адреса нет, и выдумывать его не нужно.
    """
    if target_type in moderation_service.TARGET_LABELS:
        return _hub_url(target_type, target_id)
    if target_type == TARGET_STUDENT:
        return _student_url(target_id)
    return None


def _build_audit_row(entry: AuditEntry) -> AuditRow:
    """Собрать одну строку журнала: подписи действия и вида цели плюс ссылку."""
    action = entry.action
    return AuditRow(
        entry=entry,
        action_label=ACTION_LABELS.get(action.action, action.action),
        target_label=moderation_service.AUDIT_TARGET_LABELS.get(
            action.target_type, action.target_type
        ),
        link_url=_audit_link_url(action.target_type, action.target_id),
    )


@router.get("/audit", response_class=HTMLResponse)
def audit_journal(
    request: Request,
    page: str | None = None,
    admin: Admin = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Response:
    """Полный след модерации: кто, когда, что сделал и с чем.

    Записи только добавляются, поэтому журнал -- это вся история раздела, а не
    срез по одному объекту, как карточки. Свежие сверху; сырых кодов действий на
    странице нет -- их заменяют русские подписи из сервиса.
    """
    total = audit_repo.count_all(db)
    page, total_pages = page_window(parse_page(page), total, PAGE_SIZE)
    entries = audit_repo.list_recent(
        db, limit=PAGE_SIZE, offset=(page - 1) * PAGE_SIZE
    )
    rows = [_build_audit_row(entry) for entry in entries]
    return templates.TemplateResponse(
        request,
        "admin/audit.html",
        {"rows": rows, "page": page, "total_pages": total_pages},
    )
