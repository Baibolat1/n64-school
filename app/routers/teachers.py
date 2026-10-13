"""Teacher review routes: the list, a teacher's page and the review form.

Every route requires a valid student session and a non-banned student, enforced
by ``require_not_banned`` (anonymous visitors are redirected to ``/login``,
banned students get the 403 page). The author of a review is taken from the
session, the teacher from the URL -- and the teacher is resolved to an *active*
one, so a deactivated teacher disappears from the site rather than staying
reachable by a bookmarked link.

The review form is built from the criteria table rather than from a fixed list
in a template: which questions are asked, what they are called and what scale
each uses is data (see ``scripts/seed_teachers.py``). Adding a criterion or
rewording one is a data change, not a code change.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.core.deps import require_csrf, require_not_banned
from app.core.pagination import page_window, parse_page
from app.core.templates import templates
from app.db.base import get_db
from app.models.content import CONTENT_STATUS_VISIBLE
from app.models.student import Student
from app.models.teacher import Teacher
from app.repositories import criteria as criteria_repo
from app.repositories import reviews as reviews_repo
from app.repositories import students as students_repo
from app.repositories import teachers as teachers_repo
from app.services import reviews as reviews_service

# ``require_csrf`` is attached to the whole router: it is a no-op for safe
# methods, and the one state-changing route here is a form POST.
router = APIRouter(
    prefix="/teachers", tags=["teachers"], dependencies=[Depends(require_csrf)]
)

# Reviews per page on a teacher's page.
PAGE_SIZE = 10

# Name of the form fields that carry the scores: ``score_<criterion code>``.
# Rendering and parsing share this prefix, so the two cannot drift apart.
SCORE_FIELD_PREFIX = "score_"


def _get_active_teacher(db: Session, teacher_id: int) -> Teacher:
    """Load an active teacher or raise 404.

    404 (not 403) for an inactive teacher, so the list shown to students is
    exactly the set of teachers that exist as far as they are concerned.
    """
    teacher = teachers_repo.get_teacher(db, teacher_id)
    if teacher is None or not teacher.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Учитель не найден"
        )
    return teacher


def _error_message(
    reason: reviews_service.ReviewError,
    *,
    retry_after: int = 0,
    max_length: int = reviews_service.MAX_REVIEW_LENGTH,
) -> str:
    """Human-readable refusal message for a failed submit attempt."""
    if reason is reviews_service.ReviewError.EMPTY:
        return "Текст отзыва не может быть пустым"
    if reason is reviews_service.ReviewError.TOO_LONG:
        return f"Слишком длинно: максимум {max_length} символов"
    if reason is reviews_service.ReviewError.RATE_LIMITED:
        return f"Слишком часто. Попробуйте снова через {retry_after} с."
    if reason is reviews_service.ReviewError.PII_BLOCKED:
        return (
            "В тексте есть данные, которые нельзя публиковать: телефоны, "
            "адреса почты, ссылки и мессенджеры. Уберите их и отправьте снова."
        )
    if reason is reviews_service.ReviewError.SCORE_REQUIRED:
        return "Поставьте оценку по каждому критерию"
    if reason is reviews_service.ReviewError.SCORE_OUT_OF_RANGE:
        return "Оценка вне допустимого диапазона"
    if reason is reviews_service.ReviewError.NO_CRITERIA:
        return "Критерии оценки ещё не заданы — отзыв сохранить нельзя"
    return "Не удалось сохранить отзыв"


def _refusal_status(reason: reviews_service.ReviewError) -> int:
    """Rate limiting is the one refusal a client can act on programmatically."""
    if reason is reviews_service.ReviewError.RATE_LIMITED:
        return status.HTTP_429_TOO_MANY_REQUESTS
    return status.HTTP_200_OK


def _own_review_context(
    db: Session, student: Student, teacher: Teacher
) -> dict[str, Any]:
    """The student's existing review of this teacher, if any.

    Returned as form-ready values: the template must not know that a review has
    its own scores in a second table. On a fresh visit these prefill the form;
    after a refusal the router overrides them with what was just submitted.
    """
    own = reviews_repo.get_review(db, student.id, teacher.id)
    if own is None:
        return {
            "own_review": None,
            "own_review_visible": True,
            "form_comment": "",
            "form_scores": {},
            "form_is_anonymous": False,
        }
    scores = reviews_repo.scores_by_review(db, [own.id])[own.id]
    return {
        "own_review": own,
        "own_review_visible": own.status == CONTENT_STATUS_VISIBLE,
        "form_comment": own.comment,
        "form_scores": {key: str(value) for key, value in scores.items()},
        "form_is_anonymous": own.is_anonymous,
    }


def _render_teacher(
    request: Request,
    db: Session,
    student: Student,
    teacher: Teacher,
    *,
    page: int = 1,
    error: str | None = None,
    form_comment: str | None = None,
    form_scores: dict[int, str] | None = None,
    form_is_anonymous: bool | None = None,
    status_code: int = status.HTTP_200_OK,
) -> Response:
    own = _own_review_context(db, student, teacher)
    criteria = criteria_repo.list_active_criteria(db)
    total = reviews_repo.count_visible_reviews(db, teacher.id)
    page, total_pages = page_window(page, total, PAGE_SIZE)
    reviews = reviews_repo.list_visible_reviews(
        db, teacher.id, limit=PAGE_SIZE, offset=(page - 1) * PAGE_SIZE
    )
    # The reviewer's picture is the current one, fetched in one query for the
    # whole page rather than snapshotted with the review.
    avatar_keys = students_repo.avatar_keys_by_id(
        db, [review.student_id for review in reviews]
    )

    # Everything the form needs, with anything just submitted winning over what
    # is stored -- that is what keeps a refusal from eating the student's text.
    if form_comment is not None:
        own["form_comment"] = form_comment
    if form_scores is not None:
        own["form_scores"] = form_scores
    if form_is_anonymous is not None:
        own["form_is_anonymous"] = form_is_anonymous

    return templates.TemplateResponse(
        request,
        "teacher.html",
        {
            "student": student,
            "teacher": teacher,
            "criteria": criteria,
            "criterion_stats": reviews_repo.criterion_stats(db, teacher.id),
            "overall": reviews_repo.overall_stats(db, teacher.id),
            "reviews": reviews,
            "avatar_keys": avatar_keys,
            "scores_by_review": reviews_repo.scores_by_review(
                db, [review.id for review in reviews]
            ),
            "page": page,
            "total_pages": total_pages,
            "total_reviews": total,
            "score_field_prefix": SCORE_FIELD_PREFIX,
            "max_review_length": reviews_service.MAX_REVIEW_LENGTH,
            "error": error,
            **own,
        },
        status_code=status_code,
    )


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def teacher_list(
    request: Request,
    student: Student = Depends(require_not_banned),
    db: Session = Depends(get_db),
) -> Response:
    """The teachers page: everyone active, with their average score.

    Not paginated on purpose: the list is the school's staff, a few dozen rows
    at most, and the whole point of the page is to see it at a glance.
    """
    teachers = teachers_repo.list_active_teachers(db)
    return templates.TemplateResponse(
        request,
        "teachers.html",
        {
            "student": student,
            "teachers": teachers,
            "stats_by_teacher": reviews_repo.overall_stats_by_teacher(db),
        },
    )


@router.get("/{teacher_id}", response_class=HTMLResponse)
def teacher_page(
    request: Request,
    teacher_id: int,
    page: str | None = None,
    student: Student = Depends(require_not_banned),
    db: Session = Depends(get_db),
) -> Response:
    teacher = _get_active_teacher(db, teacher_id)
    return _render_teacher(
        request, db, student, teacher, page=parse_page(page)
    )


@router.post("/{teacher_id}/reviews", response_class=HTMLResponse)
async def submit_review(
    request: Request,
    teacher_id: int,
    student: Student = Depends(require_not_banned),
    db: Session = Depends(get_db),
) -> Response:
    """Create the student's review of this teacher, or replace the existing one.

    The form is read from the request rather than through a Pydantic schema
    because its fields depend on the criteria rows: each one is named after a
    criterion code, so there is nothing fixed to declare.
    """
    teacher = _get_active_teacher(db, teacher_id)
    criteria = criteria_repo.list_active_criteria(db)
    form = await request.form()

    raw_comment = form.get("comment")
    comment = raw_comment if isinstance(raw_comment, str) else ""
    is_anonymous = form.get("is_anonymous") == "true"

    # ``score_<code>`` fields, resolved against the criteria the form was built
    # from: a code that is not on the list is ignored, a value that is not a
    # number is treated as no answer at all.
    submitted = {
        key[len(SCORE_FIELD_PREFIX):]: value
        for key, value in form.multi_items()
        if isinstance(key, str)
        and key.startswith(SCORE_FIELD_PREFIX)
        and isinstance(value, str)
    }
    scores: dict[int, int] = {}
    form_scores: dict[int, str] = {}
    for criterion in criteria:
        raw = submitted.get(criterion.code)
        if raw is None:
            continue
        form_scores[criterion.id] = raw
        try:
            scores[criterion.id] = int(raw)
        except ValueError:
            continue

    result = reviews_service.submit_review(
        db,
        student,
        teacher,
        comment=comment,
        scores=scores,
        criteria=criteria,
        is_anonymous=is_anonymous,
    )
    if result.ok:
        # Post/Redirect/Get: a reload will not resubmit the form.
        return RedirectResponse(
            url=f"/teachers/{teacher.id}", status_code=status.HTTP_303_SEE_OTHER
        )

    response = _render_teacher(
        request,
        db,
        student,
        teacher,
        error=_error_message(result.reason, retry_after=result.retry_after),
        form_comment=result.comment,
        form_scores=form_scores,
        form_is_anonymous=result.is_anonymous,
        status_code=_refusal_status(result.reason),
    )
    if result.retry_after:
        response.headers["Retry-After"] = str(result.retry_after)
    return response
